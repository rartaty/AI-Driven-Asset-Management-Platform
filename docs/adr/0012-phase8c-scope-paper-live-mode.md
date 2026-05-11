# ADR-0012: Phase 8c スコープ = PAPER_LIVE モード (実マーケットデータ + 仮想発注)

- **Status**: Proposed
- **Date**: 2026-05-09
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0009 (Phase 7 / Tick Data + WebSocket), ADR-0010 (Phase 6 / SRE), ADR-0011 (Phase 8a / Runner)

---

## Context (背景)

ユーザー要望: 「実際の市場の値を使用して、実際のお金は使わずに取引テストができるまで開発したい」

これはクオンツ業界で **PAPER_LIVE / Forward Test モード** と呼ばれる定番手法。実マーケットデータで戦略の挙動を観察しながら、実マネーを使わず仮想発注で検証できるため、本番移行前の段階的検証に必須。

**現状の TRADE_MODE 二値**:
- `PAPER`: yfinance 1分足按分疑似 tick + paper_trader 仮想発注
- `REAL`: kabu Push 実 tick + (TODO) 実発注 — 発注パスが未実装 (Phase 8d 持ち越し)

**ギャップ**: 「実 tick + 仮想発注」モードがない。PAPER は精度が低く (1分足按分は実 tick と桁違いに粗い)、REAL は実マネーが必要なため検証コストが高い。

**さらに**: PAPER_LIVE の中間段階を持つことで、本番移行 (Phase 8d) の **段階的リスク管理** が可能になる。

## Decision (決定)

**Phase 8c のスコープを PAPER_LIVE モード追加で確定**。期間目安 1〜2 日。

### 設計方針

**TRADE_MODE 三値化**:
- `PAPER` (現状維持): yfinance データ + 仮想発注
- `PAPER_LIVE` (新規): **kabu Push 実データ + 仮想発注** ← 本 ADR の対象
- `REAL` (Phase 8d): kabu Push 実データ + 実発注

### スコープ詳細

#### 8c-1. kabu_push_client.start() — PAPER_LIVE 接続許可
```python
# 現状: TRADE_MODE=PAPER で no-op
if os.getenv("TRADE_MODE", "PAPER").upper() == "PAPER":
    return True

# 改修後: PAPER のみスキップ、PAPER_LIVE/REAL は接続
mode = os.getenv("TRADE_MODE", "PAPER").upper()
if mode == "PAPER":
    logger.info("[KabuPush] PAPER mode: yfinance pump alternative used")
    return True
# PAPER_LIVE / REAL は WebSocket 接続続行
```

#### 8c-2. scheduler.job_paper_pump — PAPER_LIVE で skip
```python
# 現状: TRADE_MODE=PAPER 以外で skip
if os.getenv("TRADE_MODE", "PAPER").upper() != "PAPER":
    return

# 改修後: PAPER のみで稼働 (PAPER_LIVE は kabu Push が代替)
# 同じ条件式で OK (PAPER_LIVE != PAPER で skip 動作)
```

#### 8c-3. scheduler.job_short_term_trade — PAPER + PAPER_LIVE 両対応 + Position Sizing

**100 株固定撤廃**: PAPER_LIVE では実 buying_power に基づく動的 qty 計算 + **日次手数料無料枠 (kabu) を遵守** (Phase 8d REAL でも同じロジック使用)

```python
def _calculate_position_qty(db, price: int, bucket: str = "Short") -> int:
    """Position sizing + kabu 日次手数料無料枠遵守。

    Step 1: buying_power × POSITION_SIZE_PCT で基本 target_yen 算出
    Step 2: KABU_DAILY_ORDER_LIMIT_YEN の残量を計算 (Trade_Logs 当日累計から)
    Step 3: target_yen を min(基本, 残量) に切り詰め
    Step 4: 100 株単位切り捨て

    PAPER_LIVE / REAL mode で共通使用。
    """
    from sqlalchemy import desc as _desc, func as _func
    from datetime import date as _date
    from models.schema import Daily_Asset_Snapshot, Trade_Logs

    if price <= 0:
        return 0

    # Step 1: buying_power × POSITION_SIZE_PCT
    latest = db.query(Daily_Asset_Snapshot).order_by(_desc(Daily_Asset_Snapshot.date)).first()
    buying_power = latest.buying_power if latest else 1_000_000  # default ¥1M
    pct = float(os.getenv("POSITION_SIZE_PCT", "0.20"))  # default 20%
    target_yen = buying_power * pct

    # Step 2: kabu 日次手数料無料枠の残量チェック
    daily_limit = int(os.getenv("KABU_DAILY_ORDER_LIMIT_YEN", "1000000"))  # default ¥100万
    if daily_limit > 0:
        today = _date.today()
        used_yen = db.query(
            _func.coalesce(_func.sum(Trade_Logs.quantity * Trade_Logs.price), 0)
        ).filter(
            _func.date(Trade_Logs.timestamp) == today
        ).scalar() or 0
        remaining_yen = max(0, daily_limit - int(used_yen))
        if remaining_yen <= 0:
            logger.warning(f"[PositionSize] Daily limit ¥{daily_limit:,} reached, skip")
            return 0
        # 基本 target と残量の小さい方を採用
        target_yen = min(target_yen, remaining_yen)

    # Step 3-4: 100 株単位切り捨て
    qty = int(target_yen // (price * 100)) * 100
    return max(qty, 0)  # 0 なら発注スキップ

# 改修後
if trade_mode in ("PAPER", "PAPER_LIVE"):
    is_buy = sig["signal"] == "BUY"
    qty = _calculate_position_qty(db, int(sig["current_price"]), bucket="Short")
    if qty == 0:
        logger.info(f"[VWAPSignal] {symbol}: insufficient buying_power or daily limit, skip")
        continue
    paper_trader_engine.execute_virtual_order(
        symbol, name=f"Asset-{symbol}", qty=qty, is_buy=is_buy,
        db=db, bucket="Short",
    )
else:  # REAL → Phase 8d で本実装 (同じ _calculate_position_qty を使用)
    logger.warning(f"[VWAPSignal] REAL-mode order placement deferred to Phase 8d")
```

**PAPER モードのみ qty=100 hardcode 維持**: yfinance 疑似 tick はあくまで初期検証用で position sizing の精度議論はオーバースペック。**PAPER_LIVE 以降は実 buying_power 連動 + kabu 無料枠遵守**。

**Position Sizing 設計詳細**:
- 環境変数 `POSITION_SIZE_PCT` (デフォルト **0.20** = 20%) で 1 取引あたりの基本サイズ
- 環境変数 `PAPER_INITIAL_CAPITAL` (デフォルト **1000000** = ¥100万) で paper_trader の初期仮想資金
- 環境変数 `KABU_DAILY_ORDER_LIMIT_YEN` (デフォルト **1000000** = ¥100万/日) で kabu 1日定額コース無料枠
- Trade_Logs の当日累計売買代金 (`quantity × price`) を SUM して残量計算
- 残量 ≤ 基本 target なら残量に切り詰め (無料枠超過しない)
- 100 株単位切り捨て (日本株標準ロット)
- 残量 0 → qty=0 で発注スキップ
- 将来拡張: ボラティリティ調整 (Kelly criterion 風) は Phase 10 候補

**動作例** (buying_power=¥1M / POSITION_SIZE_PCT=0.20 / KABU_DAILY_ORDER_LIMIT_YEN=¥100万):

| 状況 | 当日累計 | 基本 target_yen | 残量 | 銘柄価格 | qty 計算 | 結果 |
|---|---|---|---|---|---|---|
| 朝1番 | ¥0 | ¥100K | ¥100万 | ¥500 | min(100K, 1M)=100K → 100K÷50K×100 | **200株** (¥100K) |
| 朝1番 | ¥0 | ¥100K | ¥100万 | ¥3,000 | min(100K, 1M)=100K → 100K÷300K=0 | **skip** (1取引で 100K に届かない) |
| 累計 ¥800K 後 | ¥800K | ¥100K | ¥200K | ¥500 | min(100K, 200K)=100K → 200株 | **200株** (¥100K) |
| 累計 ¥950K 後 | ¥950K | ¥100K | ¥50K | ¥500 | min(100K, 50K)=50K → 100株 | **100株** (¥50K) |
| 累計 ¥1M 到達 | ¥1M | ¥100K | ¥0 | any | remaining=0 | **skip** |

#### 8c-4. scheduler.job_profit_sweep — PAPER + PAPER_LIVE 両対応
```python
if trade_mode in ("PAPER", "PAPER_LIVE"):
    # 既存 paper_trader 経路 (実 price は Layer 1 から取得される)
    ...
else:  # REAL
    pass  # Phase 8d で実装
```

#### 8c-5. paper_trader.get_realtime_price — Layer 1 優先 fallback yfinance
```python
def get_realtime_price(self, symbol: str) -> float:
    # Layer 1 deque 最新 tick を優先 (PAPER_LIVE 時は実 kabu price)
    from services.market_data import get_recent_ticks
    ticks = get_recent_ticks(symbol, n=1)
    if ticks:
        return float(ticks[-1]["last_price"])

    # Layer 1 空 (取引時間外 / WebSocket 未接続) → yfinance fallback
    yf_symbol = f"{symbol}.T" if not symbol.endswith(".T") else symbol
    try:
        ticker = yf.Ticker(yf_symbol)
        data = ticker.history(period="1d")
        if not data.empty:
            return float(data['Close'].iloc[-1])
    except Exception as e:
        logger.error(f"[PaperTrader] yfinance fallback failed for {symbol}: {e}")
    return 0.0
```

#### 8c-6. runner/main_trade.py — PAPER_LIVE 起動許可
```python
def check_trade_mode(allow_paper: bool = False) -> str:
    mode = os.getenv("TRADE_MODE", "PAPER").upper()
    if mode == "REAL":
        # 確認文字列必須 (CLAUDE.md 絶対禁止 1)
        ...
    elif mode == "PAPER_LIVE":
        # 実マネー使わないため確認文字列不要
        # ただし kabu Station 稼働必須 → preflight で smoke test
        return mode
    elif mode == "PAPER":
        if not allow_paper:
            raise PreflightError("PAPER mode requires --allow-paper")
        return mode
    else:
        raise PreflightError(f"Unknown TRADE_MODE: {mode}. Valid: PAPER | PAPER_LIVE | REAL")

def check_kabu_authenticate_for_live() -> None:
    """PAPER_LIVE / REAL モードで kabu Station の到達性確認。"""
    from api.kabucom import KabucomAPIClient
    client = KabucomAPIClient()
    if not client.authenticate():
        raise PreflightError("kabu Station authenticate failed (kabu Station not running?)")
```

#### 8c-7. .env.example 更新
```
# TRADE_MODE: PAPER | PAPER_LIVE | REAL
#   PAPER     : yfinance 疑似 tick + paper_trader 仮想発注 (kabu Station 不要・初期検証向け / position sizing 動作)
#   PAPER_LIVE: kabu Push 実 tick + paper_trader 仮想発注 (kabu Station 必要・実マーケット検証向け / position sizing 動作)
#   REAL      : kabu Push 実 tick + kabucom.place_order 実発注 (CLAUDE.md 絶対禁止 1 - 明示確認必須 / position sizing 動作)
TRADE_MODE=PAPER

# Paper trader 初期仮想資金 (PAPER / PAPER_LIVE mode の架空資金)
PAPER_INITIAL_CAPITAL=1000000

# Position sizing (PAPER_LIVE / REAL mode 共通)
# buying_power の何% を 1 取引あたりの基本サイズとするか (0.0 〜 1.0)
# 例: 0.20 = 20% / buying_power=¥1,000,000 のとき 1 取引基本 ¥200,000
POSITION_SIZE_PCT=0.20

# kabu 手数料無料枠 (1 日の総売買代金上限・自分のプランに合わせて設定)
# 1日定額コース → 1000000 (¥100万)
# 0 を指定すると制限なし (PAPER モードでは無視される)
KABU_DAILY_ORDER_LIMIT_YEN=1000000
```

#### 8c-8. テスト追加
- test_kabu_push_client.py: PAPER_LIVE で start() が WebSocket 接続を試みること
- test_scheduler.py: PAPER_LIVE で job_paper_pump が skip / job_short_term_trade が paper_trader 経由
- **test_scheduler.py: _calculate_position_qty が buying_power × pct を 100株単位で返す / 不足時 0 を返す / price=0 で 0 を返す**
- test_paper_trader.py: get_realtime_price が Layer 1 → yfinance fallback の順
- test_runner.py: PAPER_LIVE モード起動が確認文字列なしで通る

**含まない (Phase 8d 以降)**:
- kabucom.place_order / cancel_order / get_orders 本実装 (Phase 8d)
- REAL モード scheduler 分岐実装 (Phase 8d)
- 冪等性キー (UUID) 二重発注防止 (Phase 8d)
- 約定タイムアウト + リトライポリシー (Phase 8d)
- 100株テストの段階移行手順 (Phase 8d)

### Why
- 戦略の信頼性検証に **実マーケットデータが必須** (yfinance 按分は精度低)
- 実マネーリスクゼロで段階的検証可能
- Phase 8d (REAL) への準備として、データソースとの結合動作を先行検証
- PAPER_LIVE で 1〜2 週間運用 → 戦略の挙動を観察した上で REAL 移行判断できる

## Consequences (結果)

### ✅ Positive
- 戦略バックテスト精度の劇的向上 (1分足按分 → 実 tick)
- 取引時間内の戦略挙動を実マーケットで観察可能
- Phase 8d (REAL) への移行が **段階的かつ安全** に
- PAPER と REAL の中間段階で「データソースの差」「ロジックの差」を分離検証できる

### ⚠️ Negative / Trade-off
- **kabu Station 起動必須**: PAPER モードと違い、kabu Station デスクトップアプリが Windows 上で稼働している必要
- kabu API トークン (KABUCOM_API_PASSWORD) の SSM 登録必須
- 取引時間外は kabu Push が動かない (Layer 1 空) → yfinance fallback がパフォーマンスのため必要
- ユーザー側で kabu 証券口座開設 + API 利用申請完了している前提

### 🔄 Required Follow-up
- ✅ 着手前: Phase 8a 完了 + ADR-0007/0008/0009 整合確認
- 📋 着手中: 8c-1〜8c-7 順次実装
- 📋 完了後: pytest 全件 + 4 subagents PASS
- 📋 完了後: **ユーザー側で 1〜2 週間の PAPER_LIVE 運用検証** (本 ADR スコープ外)
- 📋 完了後: ADR-0013 (Phase 8d / REAL モード本実装) 起草

## Alternatives Considered (検討した代替案)

### 案 A: TRADE_MODE 二値維持 + 別フラグで切替
- **概要**: `TRADE_MODE=PAPER` のまま `MARKET_DATA_SOURCE=kabu|yfinance` で制御
- **却下理由**: 環境変数の組合せパターンが増え混乱しやすい。TRADE_MODE 三値の方が意図明確

### 案 B: PAPER_LIVE 飛ばして直接 REAL 実装
- **概要**: Phase 8c をスキップし Phase 8d (REAL) のみ
- **却下理由**: 実マネーで戦略バグが露呈すると損失。検証段階を踏まないのは要件 §6 fail-safe 思想と矛盾

### 案 C: Phase 8c と 8d を一括実装
- **概要**: PAPER_LIVE と REAL を同時に
- **却下理由**: 検証期間 (1〜2 週間) を経てから REAL 移行するのが安全。一括実装は検証サイクルを潰す

## Related (関連)

- 関連 ADR: [ADR-0009](0009-phase7-scope-tick-data.md) (Tick Data + WebSocket), [ADR-0011](0011-phase8a-scope-runner-and-doc-sync.md) (Runner)
- 後続 ADR: [ADR-0013](0013-phase8d-scope-real-order-placement.md) (REAL 発注本実装)
- 関連 Runbook: [RB-002](../runbooks/RB-002-kabu-api-down.md), [RB-010](../runbooks/RB-010-websocket-loop.md)
- Audit: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)

## Notes

### Phase 8c 推奨実装順序

1. **paper_trader.get_realtime_price refactor** — Layer 1 優先 + yfinance fallback
2. **kabu_push_client.start() PAPER_LIVE 許可**
3. **scheduler.job_paper_pump** 条件確認 (PAPER のみ動作で OK)
4. **scheduler.job_short_term_trade** PAPER + PAPER_LIVE 両対応
5. **scheduler.job_profit_sweep** 同上
6. **runner/main_trade.py** PAPER_LIVE 経路追加 + check_kabu_authenticate_for_live
7. **.env.example** 説明追記
8. テスト追加 (4 領域)
9. 4 subagents 検証

### Phase 8c 完了の Definition of Done
- TRADE_MODE=PAPER_LIVE で kabu Push 接続 → Layer 1 deque 蓄積動作確認 (または mock テスト)
- TRADE_MODE=PAPER_LIVE で paper_trader.execute_virtual_order が **Layer 1 から実 price** を読む
- TRADE_MODE=PAPER (現状) と TRADE_MODE=PAPER_LIVE (新規) のテストが両方 PASS
- TRADE_MODE=REAL は確認文字列必須維持 (CLAUDE.md 絶対禁止 1 整合)
- pytest 全件 PASS
- secrets-scanner / paper-trade-validator / discord-notify-validator / db-schema-reviewer 全件 PASS
- 本 ADR Status=Completed