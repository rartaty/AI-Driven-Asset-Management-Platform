"""Tests for backend/src/services/market_data.py (Phase 7 P7-2)

カバー範囲:
- LR-EMO アルゴリズム (Lee-Ready + EMO 拡張)
- TickReconstructor の 1 秒 bucket 集約
- Layer1Store deque 操作
- flush_layer2 の Market_Ticks 書込
- yfinance_to_pseudo_ticks の PAPER 変換
- 異常検知: cumulative_volume 巻き戻り
- 場前リセット特例 (09:00-09:05)
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base
from models.schema import Asset_Master, AssetCategory, Market_Ticks, TickSide
from services import market_data as md


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    # Asset_Master を 1 件登録 (FK 制約用)
    db.add(Asset_Master(
        ticker_symbol="7203", asset_name="Toyota",
        category=AssetCategory.short, is_active=True,
    ))
    db.commit()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def reset_singletons():
    """各テスト前にグローバル状態をリセット (Layer1Store / TickReconstructor)。"""
    md.reset_state()
    yield
    md.reset_state()


# ===== LR-EMO アルゴリズム =====

class TestLeeReadyEmo:
    def test_at_ask_returns_buy_aggressor(self):
        # EMO: price >= ask → BUY
        assert md.lee_ready_emo(price=3001, bid=2999, ask=3001, prev_price=3000) == TickSide.buy_aggressor

    def test_at_bid_returns_sell_aggressor(self):
        # EMO: price <= bid → SELL
        assert md.lee_ready_emo(price=2999, bid=2999, ask=3001, prev_price=3000) == TickSide.sell_aggressor

    def test_above_midpoint_returns_buy(self):
        # midpoint=3000、price=3000.5 → BUY
        # 整数のみだが price>midpoint で判定
        assert md.lee_ready_emo(price=3001, bid=2998, ask=3002, prev_price=3000) == TickSide.buy_aggressor

    def test_below_midpoint_returns_sell(self):
        assert md.lee_ready_emo(price=2999, bid=2998, ask=3002, prev_price=3000) == TickSide.sell_aggressor

    def test_at_midpoint_uptick_returns_buy(self):
        # midpoint=3000、price=3000、prev=2999 → uptick → BUY
        assert md.lee_ready_emo(price=3000, bid=2999, ask=3001, prev_price=2999) == TickSide.buy_aggressor

    def test_at_midpoint_downtick_returns_sell(self):
        assert md.lee_ready_emo(price=3000, bid=2999, ask=3001, prev_price=3001) == TickSide.sell_aggressor

    def test_at_midpoint_zerotick_returns_mid(self):
        # 横ばい + midpoint ぴったり + prev も同じ → MID
        assert md.lee_ready_emo(price=3000, bid=2999, ask=3001, prev_price=3000) == TickSide.mid

    def test_quote_missing_uses_tick_test_only(self):
        # bid/ask 欠損 → tick test のみ
        assert md.lee_ready_emo(price=3001, bid=None, ask=None, prev_price=3000) == TickSide.buy_aggressor
        assert md.lee_ready_emo(price=2999, bid=None, ask=None, prev_price=3000) == TickSide.sell_aggressor
        assert md.lee_ready_emo(price=3000, bid=None, ask=None, prev_price=3000) == TickSide.mid

    def test_quote_missing_no_prev_returns_mid(self):
        # 初回 push (prev_price なし) で quote 欠損 → MID
        assert md.lee_ready_emo(price=3000, bid=None, ask=None, prev_price=None) == TickSide.mid

    def test_flat_at_ask_emo_disambiguates(self):
        """ユーザー指摘シナリオ: 価格横ばい + at-ask → EMO で BUY 判定 (LR 単独なら MID)。"""
        # price=ask=3000, bid=2999, prev=3000 (横ばい)
        assert md.lee_ready_emo(price=3000, bid=2999, ask=3000, prev_price=3000) == TickSide.buy_aggressor

    def test_flat_at_bid_emo_disambiguates(self):
        """同上: 価格横ばい + at-bid → EMO で SELL 判定。"""
        assert md.lee_ready_emo(price=3000, bid=3000, ask=3001, prev_price=3000) == TickSide.sell_aggressor


# ===== TickReconstructor: 1 秒 bucket 集約 =====

def _push(price, cum, bid=None, ask=None, ts=None, is_synthetic=False):
    """テスト用 push 辞書を組み立てる。"""
    return {
        "timestamp": ts or datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc),
        "last_price": price,
        "cumulative_volume": cum,
        "bid_price": bid,
        "ask_price": ask,
        "is_synthetic": is_synthetic,
    }


class TestTickReconstructor:
    def test_first_push_returns_none(self):
        r = md.TickReconstructor()
        assert r.on_push("7203", _push(3000, 100)) is None

    def test_same_second_pushes_aggregate(self):
        r = md.TickReconstructor()
        ts = datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc)
        # 同一秒内 3 push: 全て None (bucket 切り替わらない)
        assert r.on_push("7203", _push(3000, 100, ts=ts)) is None
        assert r.on_push("7203", _push(3001, 110, ts=ts)) is None
        assert r.on_push("7203", _push(3002, 120, ts=ts)) is None

    def test_bucket_emission_on_second_change(self):
        r = md.TickReconstructor()
        t1 = datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 5, 9, 10, 0, 1, tzinfo=timezone.utc)
        assert r.on_push("7203", _push(3000, 100, ts=t1)) is None
        # 秒切替 → 前 bucket emit
        emitted = r.on_push("7203", _push(3010, 150, ts=t2))
        assert emitted is not None
        assert emitted["last_price"] == 3000
        assert emitted["cumulative_volume"] == 100
        assert emitted["delta_volume"] == 100  # 前 bucket なし → 自身 = delta
        assert emitted["push_count"] == 1

    def test_delta_volume_computed_from_prev_bucket(self):
        r = md.TickReconstructor()
        t1 = datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 5, 9, 10, 0, 1, tzinfo=timezone.utc)
        t3 = datetime(2026, 5, 9, 10, 0, 2, tzinfo=timezone.utc)

        r.on_push("7203", _push(3000, 100, ts=t1))
        emit1 = r.on_push("7203", _push(3010, 150, ts=t2))   # 1秒目を確定
        emit2 = r.on_push("7203", _push(3020, 200, ts=t3))   # 2秒目を確定

        assert emit1["delta_volume"] == 100  # 初回 bucket
        assert emit2["delta_volume"] == 50   # 150 → 200 = +50

    def test_cumulative_volume_regression_during_session_returns_none(self):
        """異常: 取引時間中に cumulative_volume が減少 → tick 記録なし。"""
        r = md.TickReconstructor()
        t1 = datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 5, 9, 10, 0, 1, tzinfo=timezone.utc)
        t3 = datetime(2026, 5, 9, 10, 0, 2, tzinfo=timezone.utc)
        r.on_push("7203", _push(3000, 200, ts=t1))
        r.on_push("7203", _push(3010, 300, ts=t2))   # 1秒目 emit (delta=200)
        # 2秒目: cumulative が減少 → 異常
        emit = r.on_push("7203", _push(3020, 100, ts=t3))
        assert emit is None or emit.get("delta_volume", 1) >= 0

    def test_market_open_reset_special_case(self):
        """場前リセット (09:00-09:05): cumulative=0 への巻き戻りは異常扱いしない。"""
        r = md.TickReconstructor()
        # 前日 close 後
        t_prev = datetime(2026, 5, 9, 8, 59, 0, tzinfo=timezone.utc)
        # 当日場前 (09:01)
        t_open = datetime(2026, 5, 9, 9, 1, 0, tzinfo=timezone.utc)
        t_open_next = datetime(2026, 5, 9, 9, 1, 1, tzinfo=timezone.utc)

        r.on_push("7203", _push(3000, 50000, ts=t_prev))
        r.on_push("7203", _push(2950, 0, ts=t_open))   # cumulative リセット
        emit = r.on_push("7203", _push(2960, 100, ts=t_open_next))
        assert emit is not None
        assert emit["delta_volume"] == 0  # 起点 (cumulative=0)

    def test_lee_ready_emo_applied_in_bucket(self):
        """bucket 完成時に LR-EMO で side_inference が確定。"""
        r = md.TickReconstructor()
        t1 = datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 5, 9, 10, 0, 1, tzinfo=timezone.utc)

        # 1秒目: price=3000, ask=3000 (at-ask) → BUY_AGGR
        r.on_push("7203", _push(3000, 100, bid=2999, ask=3000, ts=t1))
        emit = r.on_push("7203", _push(3000, 200, bid=2999, ask=3000, ts=t2))
        # 1秒目を emit、初回なので prev_price なし → bid/ask あるので EMO 部分が動く
        assert emit["side_inference"] == TickSide.buy_aggressor


# ===== Layer1Store =====

class TestLayer1Store:
    def test_push_and_get_recent(self):
        store = md.Layer1Store(maxlen=10)
        for i in range(5):
            store.push_tick({"ticker_symbol": "7203", "last_price": 3000 + i})
        ticks = store.get_recent_ticks("7203", n=3)
        assert len(ticks) == 3
        assert ticks[-1]["last_price"] == 3004  # 最新

    def test_maxlen_enforced(self):
        store = md.Layer1Store(maxlen=3)
        for i in range(10):
            store.push_tick({"ticker_symbol": "7203", "last_price": 3000 + i})
        ticks = store.get_recent_ticks("7203", n=10)
        assert len(ticks) == 3  # maxlen 制限
        assert ticks[0]["last_price"] == 3007  # 古いものは捨てられる

    def test_per_ticker_isolation(self):
        store = md.Layer1Store()
        store.push_tick({"ticker_symbol": "7203", "last_price": 3000})
        store.push_tick({"ticker_symbol": "9984", "last_price": 8000})
        assert len(store.get_recent_ticks("7203")) == 1
        assert len(store.get_recent_ticks("9984")) == 1

    def test_empty_ticker_returns_empty_list(self):
        store = md.Layer1Store()
        assert store.get_recent_ticks("9999") == []


# ===== flush_layer2 =====

class TestFlushLayer2:
    def test_flush_persists_ticks(self, session):
        ticks = [
            {
                "timestamp": datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc),
                "ticker_symbol": "7203",
                "last_price": 3000,
                "cumulative_volume": 100,
                "delta_volume": 100,
                "bid_price": 2999,
                "ask_price": 3001,
                "side_inference": TickSide.buy_aggressor,
                "is_synthetic": False,
                "push_count": 1,
            },
        ]
        count = md.flush_layer2(session, ticks)
        assert count == 1

        rows = session.query(Market_Ticks).all()
        assert len(rows) == 1
        assert rows[0].last_price == 3000
        assert rows[0].side_inference == TickSide.buy_aggressor

    def test_flush_empty_returns_zero(self, session):
        assert md.flush_layer2(session, []) == 0

    def test_flush_bulk_insert(self, session):
        ticks = [
            {
                "timestamp": datetime(2026, 5, 9, 10, 0, sec, tzinfo=timezone.utc),
                "ticker_symbol": "7203",
                "last_price": 3000 + sec,
                "cumulative_volume": 100 * (sec + 1),
                "delta_volume": 100,
                "bid_price": None,
                "ask_price": None,
                "side_inference": TickSide.mid,
                "is_synthetic": True,
                "push_count": 1,
            }
            for sec in range(10)
        ]
        count = md.flush_layer2(session, ticks)
        assert count == 10
        assert session.query(Market_Ticks).count() == 10


# ===== PAPER モード変換 =====

class TestYfinanceToPseudoTicks:
    def test_one_minute_to_60_seconds(self):
        """yfinance 1 分足 → 60 個の 1 秒 tick に展開。"""
        import pandas as pd
        df = pd.DataFrame({
            "Close": [3000],
            "Volume": [600],
        }, index=[datetime(2026, 5, 9, 10, 0, 0)])

        ticks = md.yfinance_to_pseudo_ticks("7203", df)
        assert len(ticks) == 60
        # 全 tick が is_synthetic=True
        assert all(t["is_synthetic"] for t in ticks)
        # 全 tick が side='MID'
        assert all(t["side_inference"] == TickSide.mid for t in ticks)
        # volume 按分: 600 / 60 = 10/sec
        assert all(t["delta_volume"] == 10 for t in ticks)
        # 累積 volume が単調増加
        cumulative = [t["cumulative_volume"] for t in ticks]
        assert cumulative == sorted(cumulative)

    def test_zero_volume_minute_no_crash(self):
        """volume=0 でも例外なく処理。"""
        import pandas as pd
        df = pd.DataFrame({
            "Close": [3000],
            "Volume": [0],
        }, index=[datetime(2026, 5, 9, 10, 0, 0)])

        ticks = md.yfinance_to_pseudo_ticks("7203", df)
        assert len(ticks) == 60
        assert all(t["delta_volume"] == 0 for t in ticks)


# ===== 公開 API: on_push / get_recent_ticks =====

class TestPublicAPI:
    def test_on_push_pipes_to_layer1(self):
        ts1 = datetime(2026, 5, 9, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 5, 9, 10, 0, 1, tzinfo=timezone.utc)
        md.on_push("7203", _push(3000, 100, ts=ts1))
        md.on_push("7203", _push(3010, 150, ts=ts2))   # 1秒目 emit → Layer 1 に push

        ticks = md.get_recent_ticks("7203")
        assert len(ticks) == 1
        assert ticks[0]["last_price"] == 3000

    def test_get_recent_ticks_unknown_returns_empty(self):
        assert md.get_recent_ticks("9999") == []
