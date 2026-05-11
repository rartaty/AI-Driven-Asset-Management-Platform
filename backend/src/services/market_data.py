"""
Market Data Service — 歩み値 (Time & Sales) 再構築 + Layer 1/2 ストレージ管理

要件: §B.2 (短期は ms 級レイテンシ要件)
関連: ADR-0008 (Tick Data 設計決定), ADR-0009 (Phase 7 スコープ), TICK_DATA_PIPELINE_DESIGN.md

3 層アーキテクチャ:
  Layer 1 HOT (in-memory deque)        — 戦略から直接アクセス、ms 級レイテンシ
  Layer 2 WARM (SQLite Market_Ticks)   — 永続化 + バックテスト
  Layer 3 COLD (圧縮 / Phase 8 で TimescaleDB 移行予定)

データソース (歩み値はどの API も直接提供しない、こちら側で再構築する):
  REAL モード: services/kabu_push_client から Push を on_push() に転送
  PAPER モード: yfinance 1 分足を疑似 tick に変換 (volume 60 等分按分 / side='MID' 固定)

責任境界:
  - データ層 (本ファイル): push 集約・LR-EMO side 推定・永続化・PAPER 変換・gap 補完
  - 戦略層 (strategy/vwap_short.py): get_recent_ticks() を呼んで VWAP/Z スコア計算

構築アルゴリズム (ADR-0009 で確定):
  - 集約粒度: 1 秒 bucket (bucket 内の最後の push を採用)
  - side 推定: Lee-Ready + EMO 拡張 (lee_ready_emo)
  - volume delta: bucket 終端の cumulative_volume - 前 bucket の cumulative_volume
    - delta < 0 は異常 (場前リセット除く) → tick 記録せず logger.error
"""
from __future__ import annotations

import logging
from collections import deque, defaultdict
from datetime import datetime
from threading import Lock
from typing import Deque, Dict, List, Optional

from sqlalchemy.orm import Session

from models.schema import Market_Ticks, TickSide

logger = logging.getLogger(__name__)


# ===== 定数 =====
DEFAULT_DEQUE_SIZE = 3600  # 1 銘柄当たり 1 時間分の 1 秒 bucket (運用で調整可)


# ===== Lee-Ready + EMO アルゴリズム =====

def lee_ready_emo(
    price: int,
    bid: Optional[int],
    ask: Optional[int],
    prev_price: Optional[int],
) -> TickSide:
    """歩み値の主導側を推定する (LR-EMO)。

    決定木:
      1. quote 欠損時: tick test のみ
      2. price >= ask → BUY_AGGR (買い手主導: ask に当てに行った)
      3. price <= bid → SELL_AGGR (売り手主導: bid に当てに行った)
      4. price > midpoint → BUY_AGGR
      5. price < midpoint → SELL_AGGR
      6. price == midpoint → tick test fallback (前 bucket 比較)
    """
    if bid is None or ask is None:
        if prev_price is None:
            return TickSide.mid
        if price > prev_price:
            return TickSide.buy_aggressor
        if price < prev_price:
            return TickSide.sell_aggressor
        return TickSide.mid

    # EMO 拡張: at-quote trades は無条件で side 確定
    if price >= ask:
        return TickSide.buy_aggressor
    if price <= bid:
        return TickSide.sell_aggressor

    # Quote rule: midpoint 比較
    midpoint = (bid + ask) / 2
    if price > midpoint:
        return TickSide.buy_aggressor
    if price < midpoint:
        return TickSide.sell_aggressor

    # price == midpoint → tick test fallback
    if prev_price is None:
        return TickSide.mid
    if price > prev_price:
        return TickSide.buy_aggressor
    if price < prev_price:
        return TickSide.sell_aggressor
    return TickSide.mid


# ===== Tick Reconstructor (1 秒 bucket aggregator) =====

class TickReconstructor:
    """Push を 1 秒 bucket に集約。bucket 切り替わり時に完成 tick を返す。

    各銘柄の bucket 状態と前 bucket の close 値を保持。
    """

    def __init__(self) -> None:
        # ticker -> {epoch_sec, last_push, push_count}
        self._current_bucket: Dict[str, dict] = {}
        # ticker -> {last_price, cumulative_volume} — 前 bucket の close (LR-EMO 用)
        self._last_completed: Dict[str, dict] = {}

    def on_push(self, ticker: str, push: dict) -> Optional[dict]:
        """Push 受信。bucket が切り替わったら前 bucket の Tick (dict) を返す、未切替なら None。

        push 期待スキーマ::
            {
                "timestamp": datetime,           # tz-aware 推奨
                "last_price": int,
                "cumulative_volume": int,
                "bid_price": Optional[int],
                "ask_price": Optional[int],
                "is_synthetic": Optional[bool],  # PAPER 等で True
            }
        """
        ts: datetime = push["timestamp"]
        epoch_sec = int(ts.timestamp())
        bucket = self._current_bucket.get(ticker)
        emitted: Optional[dict] = None

        if bucket is not None and bucket["epoch_sec"] != epoch_sec:
            emitted = self._finalize_bucket(ticker, bucket)
            # 新 bucket を作成 (push を初回として)
            self._current_bucket[ticker] = {
                "epoch_sec": epoch_sec,
                "last_push": push,
                "push_count": 1,
            }
        elif bucket is None:
            # 初回 push
            self._current_bucket[ticker] = {
                "epoch_sec": epoch_sec,
                "last_push": push,
                "push_count": 1,
            }
        else:
            # 同一 bucket 内: last_push を上書き、count++
            bucket["last_push"] = push
            bucket["push_count"] += 1

        return emitted

    def flush_all(self) -> List[dict]:
        """全 bucket を強制 finalize (場引け時等)。各銘柄の現 bucket を確定して返す。"""
        out: List[dict] = []
        for ticker, bucket in list(self._current_bucket.items()):
            tick = self._finalize_bucket(ticker, bucket)
            if tick is not None:
                out.append(tick)
        self._current_bucket.clear()
        return out

    def _finalize_bucket(self, ticker: str, bucket: dict) -> Optional[dict]:
        last = bucket["last_push"]
        cum = last["cumulative_volume"]
        prev = self._last_completed.get(ticker)
        # 初回 bucket は cumulative_volume を起点とみなす (delta = cum)。
        # 場前リセット特例と一貫した「当日起点」セマンティクス。
        # 取引時間中の WebSocket 切断中盤からの再接続でも delta は連続性を保つ。
        prev_cum = prev["cumulative_volume"] if prev else 0
        delta = cum - prev_cum

        if delta < 0:
            ts: datetime = last["timestamp"]
            # 場前リセット特例: 09:00-09:05 の cumulative_volume 巻き戻り (前日 close 後 → 当日0)
            if ts.hour == 9 and ts.minute < 5:
                logger.info(
                    f"[MarketData] Cumulative volume reset for {ticker} at market open "
                    f"(prev={prev_cum}, curr={cum})"
                )
                delta = cum  # 当日起点の delta
            else:
                logger.error(
                    f"[MarketData] ABNORMAL: cumulative_volume regression for {ticker}: "
                    f"prev={prev_cum} curr={cum} at {ts.isoformat()}"
                )
                # 異常 tick は emit しない (短期 bucket kill switch fire は呼出側で実施)
                return None

        prev_price = prev["last_price"] if prev else None
        side = lee_ready_emo(
            last["last_price"],
            last.get("bid_price"),
            last.get("ask_price"),
            prev_price,
        )

        tick = {
            "timestamp": last["timestamp"],
            "ticker_symbol": ticker,
            "last_price": last["last_price"],
            "cumulative_volume": cum,
            "delta_volume": delta,
            "bid_price": last.get("bid_price"),
            "ask_price": last.get("ask_price"),
            "side_inference": side,
            "is_synthetic": last.get("is_synthetic", False),
            "push_count": bucket["push_count"],
        }

        self._last_completed[ticker] = {
            "last_price": last["last_price"],
            "cumulative_volume": cum,
        }
        return tick


# ===== Layer 1 HOT (in-memory deque) =====

class Layer1Store:
    """銘柄毎の deque (maxlen 制限) で直近 tick を保持。

    戦略はここから get_recent_ticks() で取得して VWAP/Z スコア計算。
    永続化失敗 (Layer 2 落下) でも Layer 1 は継続稼働する設計 (要件 §6 fail-safe)。
    """

    def __init__(self, maxlen: int = DEFAULT_DEQUE_SIZE) -> None:
        self._maxlen = maxlen
        self._deques: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=maxlen))
        self._lock = Lock()

    def push_tick(self, tick: dict) -> None:
        with self._lock:
            self._deques[tick["ticker_symbol"]].append(tick)

    def get_recent_ticks(self, ticker: str, n: int = 100) -> List[dict]:
        """直近 N 個の tick を返す (新しい順ではなく時系列順)。"""
        with self._lock:
            d = self._deques.get(ticker)
            if not d:
                return []
            return list(d)[-n:]

    def drain_all(self) -> List[dict]:
        """全 tick を返却して deque をクリア (Layer 2 flush 後の状態リセット用)。"""
        with self._lock:
            out: List[dict] = []
            for d in self._deques.values():
                out.extend(d)
                d.clear()
            return out

    def clear(self, ticker: Optional[str] = None) -> None:
        with self._lock:
            if ticker:
                self._deques.pop(ticker, None)
            else:
                self._deques.clear()


# ===== Layer 2 WARM flush (SQLite bulk insert) =====

def flush_layer2(session: Session, ticks: List[dict]) -> int:
    """Layer 1 から受け取った tick リストを Market_Ticks へ bulk insert。

    :return: 永続化したレコード数
    """
    if not ticks:
        return 0

    objs = [
        Market_Ticks(
            ticker_symbol=t["ticker_symbol"],
            timestamp=t["timestamp"],
            last_price=t["last_price"],
            cumulative_volume=t["cumulative_volume"],
            delta_volume=t["delta_volume"],
            bid_price=t.get("bid_price"),
            ask_price=t.get("ask_price"),
            side_inference=t.get("side_inference"),
            is_synthetic=t.get("is_synthetic", False),
            push_count=t.get("push_count", 1),
        )
        for t in ticks
    ]
    session.bulk_save_objects(objs)
    session.commit()
    return len(objs)


# ===== PAPER モード: yfinance 1 分足 → 疑似 tick 変換 =====

def yfinance_to_pseudo_ticks(ticker: str, df) -> List[dict]:
    """yfinance の 1 分足 DataFrame を疑似 tick に変換 (PAPER モード用)。

    各 1 分足を 60 個の 1 秒 bucket に按分 (volume 一様分布仮定 / side='MID' 固定)。
    is_synthetic=True で記録される。

    :param ticker: 銘柄コード (Asset_Master.ticker_symbol と整合させる)
    :param df: yfinance.Ticker.history(period='1d', interval='1m') 等の DataFrame。
               index は DatetimeIndex、columns は Open/High/Low/Close/Volume を含む。
    """
    ticks: List[dict] = []
    cumulative = 0
    for ts, row in df.iterrows():
        per_second_volume = int(row["Volume"]) // 60 if row.get("Volume") else 0
        for sec in range(60):
            cumulative += per_second_volume
            ts_sec = ts.replace(second=sec, microsecond=0)
            ticks.append({
                "timestamp": ts_sec,
                "ticker_symbol": ticker,
                "last_price": int(row["Close"]),
                "cumulative_volume": cumulative,
                "delta_volume": per_second_volume,
                "bid_price": None,
                "ask_price": None,
                "side_inference": TickSide.mid,
                "is_synthetic": True,
                "push_count": 1,
            })
    return ticks


# ===== グローバルシングルトン =====

_layer1: Optional[Layer1Store] = None
_reconstructor: Optional[TickReconstructor] = None


def get_layer1() -> Layer1Store:
    global _layer1
    if _layer1 is None:
        _layer1 = Layer1Store()
    return _layer1


def get_reconstructor() -> TickReconstructor:
    global _reconstructor
    if _reconstructor is None:
        _reconstructor = TickReconstructor()
    return _reconstructor


def get_recent_ticks(ticker: str, n: int = 100) -> List[dict]:
    """戦略向け公開 API: 直近 N 個の tick を Layer 1 から取得。"""
    return get_layer1().get_recent_ticks(ticker, n)


def on_push(ticker: str, push: dict) -> Optional[dict]:
    """データ層への push 受信エントリポイント (kabu_push_client 等から呼ばれる)。

    1 秒 bucket に集約し、bucket 完成時は Layer 1 deque に追加して完成 tick を返す。
    Layer 2 永続化は別途 flush_layer2() で定期実行 (scheduler.job_flush_market_ticks)。
    """
    tick = get_reconstructor().on_push(ticker, push)
    if tick is not None:
        get_layer1().push_tick(tick)
    return tick


def reset_state() -> None:
    """テスト用: グローバル状態をリセット。"""
    global _layer1, _reconstructor
    _layer1 = None
    _reconstructor = None
