# ADR-0013: Phase 8d スコープ = REAL モード実発注パス本実装

- **Status**: Proposed (Phase 8c 完了 + 1〜2 週間運用検証後に着手)
- **Date**: 2026-05-09 (起草) / Phase 8c 完了後に再評価
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0012 (Phase 8c PAPER_LIVE), ADR-0009 (Phase 7 Tick Data), ADR-0010 (Phase 6 SRE)

---

## Context (背景)

Phase 8c (PAPER_LIVE) 完成後、戦略は **kabu Push 実 tick で動作するが発注は仮想** という状態。本番運用 (実マネー使用) に移行するには `kabucom.place_order()` 等の発注経路を本実装する必要がある。

**CLAUDE.md 絶対禁止 1 領域**:
> TRADE_MODE=REAL 切替には明示ユーザー確認必須

このフェーズは**最大限慎重に**進める。検証段階を必ず踏み、誤発注リスクをコード/運用の両面でブロックする。

## Decision (決定)

**Phase 8d のスコープを REAL モード実発注パス本実装で確定**。期間目安 2〜3 日。

### スコープ詳細

#### 8d-1. kabucom.place_order() 本実装
```python
def place_order(
    self,
    symbol: str,
    qty: int,
    side: Literal["BUY", "SELL"],
    order_type: Literal["MARKET", "LIMIT"] = "MARKET",
    limit_price: Optional[int] = None,
    cash_or_margin: Literal["CASH", "MARGIN"] = "CASH",
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    kabu Station API /sendorder への POST。

    :param idempotency_key: UUID。同一 key の二重発注を防止 (DB で過去 key を確認)
    :return: {"order_id": str, "status": "ACCEPTED" | "REJECTED", "raw": <full response>}
    :raises ConnectionError: 認証失敗
    :raises ValueError: パラメータ不正
    """
```

実装ポイント:
- 認証 token を `X-API-KEY` ヘッダに付与
- 注文 JSON 構築 (Symbol / Side / OrderQty / OrdType / Price / FrontOrderType 等)
- HTTP POST → response 解析 → order_id 取得
- 失敗時の例外通知 + Discord notify_critical
- **冪等性キー (UUID) で二重発注防止**

#### 8d-2. kabucom.cancel_order(order_id) 追加
```python
def cancel_order(self, order_id: str) -> bool:
    """注文キャンセル (kabu Station API /cancelorder)。リスク管理用。"""
```

#### 8d-3. kabucom.get_orders() 追加
```python
def get_orders(self, status: Optional[str] = None) -> List[dict]:
    """発注済注文一覧 (kabu Station API /orders)。約定確認用。"""
```

#### 8d-4. scheduler.job_short_term_trade — REAL 分岐本実装

**Position Sizing は Phase 8c で実装した `_calculate_position_qty` を流用**。REAL でも同じロジックで qty 算出。

```python
if trade_mode == "REAL":
    is_buy = sig["signal"] == "BUY"
    # Phase 6 bucket 別 KS チェック
    from core.kill_switch import assert_inactive_for_entry, KillSwitchError
    try:
        assert_inactive_for_entry(db, bucket="Short")
    except KillSwitchError:
        continue  # 通知は kill_switch 内部で処理済

    # Phase 8c で実装した position sizing (buying_power × POSITION_SIZE_PCT + kabu 日次無料枠)
    qty = _calculate_position_qty(db, int(sig["current_price"]), bucket="Short")
    if qty == 0:
        logger.info(f"[VWAPSignal] {symbol}: insufficient buying_power or daily limit reached, skip REAL order")
        continue

    # REAL 専用追加上限 (1 取引あたり絶対上限) 段階移行用
    # KABU_DAILY_ORDER_LIMIT_YEN で日次累計は既に制御されているが、
    # 100 株テスト等で「1 取引あたり」を絞りたい場合に使用
    max_yen = int(os.getenv("REAL_MAX_YEN_PER_TRADE", "0"))  # default 0 = 制限なし
    if max_yen > 0 and qty * sig["current_price"] > max_yen:
        qty = int(max_yen // (sig["current_price"] * 100)) * 100
        if qty == 0:
            logger.warning(f"[VWAPSignal] {symbol}: REAL_MAX_YEN_PER_TRADE too tight, skip")
            continue

    # 冪等性キー
    import uuid
    idem_key = str(uuid.uuid4())

    try:
        result = kabucom_client.place_order(
            symbol=symbol, qty=qty, side="BUY" if is_buy else "SELL",
            order_type="MARKET", cash_or_margin="MARGIN",  # 信用 (空売り対応)
            idempotency_key=idem_key,
        )
        # Trade_Logs に記録 (約定確認は別ジョブで)
        db.add(Trade_Logs(
            ticker_symbol=symbol, action="BUY" if is_buy else "SELL",
            quantity=qty, price=int(result.get("average_price", 0)),
            decision_reason=sig["reason"],
        ))
        db.commit()
    except Exception as e:
        logger.error(f"[VWAPSignal] place_order failed for {symbol}: {e}")
        notify_critical(f"Order failed: {symbol} {sig['signal']}: {e}", component="API:Kabucom")
        # 連続失敗で短期 bucket KS 自動発火 (5 回連続失敗で発火など)
```

**REAL 専用追加安全装置 (Phase 8c 共通設計に加えて)**:
- `KABU_DAILY_ORDER_LIMIT_YEN` (Phase 8c 既定): 1 日の総売買代金上限。kabu 手数料無料枠遵守
- `REAL_MAX_YEN_PER_TRADE` (REAL 専用 / 段階移行用): 1 取引あたりの絶対上限。デフォルト **0 = 制限なし**。100株テスト期間中のみ有効化推奨
  - Step 3 (極小): `REAL_MAX_YEN_PER_TRADE=50000` (¥50K) で限定
  - Step 4 (中間): `REAL_MAX_YEN_PER_TRADE=200000` (¥200K)
  - Step 5 (通常運用): `REAL_MAX_YEN_PER_TRADE=0` (= 制限解除、KABU_DAILY_ORDER_LIMIT_YEN のみで制御)

#### 8d-5. scheduler.job_profit_sweep — REAL 分岐本実装
```python
if trade_mode == "REAL":
    # 全 short ポジション取得 (kabucom.get_positions)
    positions = kabucom_client.get_positions()
    short_positions = [p for p in positions if get_category(p["Symbol"]) == "Short"]

    for p in short_positions:
        try:
            # 反対売買 (現物 BUY/SELL or 信用 BUY_TO_COVER/SELL_TO_CLOSE)
            kabucom_client.place_order(
                symbol=p["Symbol"], qty=p["LeavesQty"],
                side="SELL" if p["IsBuy"] else "BUY",  # 反対側
                order_type="MARKET",
                idempotency_key=f"sweep-{date_today}-{p['Symbol']}",
            )
        except Exception as e:
            notify_critical(f"Profit sweep failed: {p['Symbol']}: {e}", component="ProfitSweep")
            # 重大: オーバーナイトリスク発生 → 全体 KS 発火検討
```

#### 8d-6. 冪等性キー (UUID) 二重発注防止
- `Trade_Logs` schema に `idempotency_key` 列追加 (Optional)
- place_order 前に DB で過去 key を確認
- 同一 key 検出時は新規発注スキップ + 警告ログ

#### 8d-7. 約定タイムアウト + リトライポリシー
- place_order 後 30 秒以内に約定確認 (`get_orders(order_id=...)`)
- タイムアウト時は **キャンセル試行** + 警告通知
- 連続 5 回失敗で短期 bucket KS 自動発火

#### 8d-8. テスト戦略
- **本物の発注は危険**: 全テストモック化必須
- responses or httpx-mock で kabu API のレスポンスをモック
- 異常系: timeout / 4xx (注文拒否) / 5xx (kabu 障害) を網羅
- 冪等性キー二重発注防止のリグレッションテスト
- Profit Sweep の実発注パスを mock で検証

#### 8d-9. 段階移行手順書 (docs/operations/REAL_MODE_MIGRATION.md 新規)
1. **Step 1: Phase 8c 完了確認** + 1〜2 週間 PAPER_LIVE 運用ログ review (position sizing も検証)
2. **Step 2: kabu 口座準備**
   - 信用取引口座開設済確認
   - 自己資金 (最低限・例えば 100 万円程度) 入金
   - kabu Station アプリ起動 + API トークン認証確認
3. **Step 3: TRADE_MODE=REAL 起動 (極小ロット制限テスト)**
   - `RUNNER_REAL_CONFIRM='I_UNDERSTAND_REAL_TRADING_RISK'` 環境変数設定
   - **`REAL_MAX_YEN_PER_TRADE=50000`** で 1 取引 ¥50K 上限に絞る (¥500 銘柄なら 100株のみ)
   - **`POSITION_SIZE_PCT=0.01`** で 1% に絞る (¥10K target → 上限 ¥50K で抑え込まれる)
   - `KABU_DAILY_ORDER_LIMIT_YEN=200000` で日次 ¥20万 に絞る (kabu 手数料無料枠は ¥100万 だがテスト初期は更に小さく)
   - `python -m runner.main_trade` で起動
   - **最初の 1〜数日は朝のみ確認、午後は監視**
4. **Step 4: 1 週間運用 → 段階的に上限緩和**
   - `REAL_MAX_YEN_PER_TRADE=200000` に拡大 (¥200K)
   - `POSITION_SIZE_PCT=0.05` に拡大 (5%)
   - `KABU_DAILY_ORDER_LIMIT_YEN=500000` に拡大 (¥50万)
5. **Step 5: 通常ロット運用** (デフォルト値 `REAL_MAX_YEN_PER_TRADE=0` / `POSITION_SIZE_PCT=0.10` / `KABU_DAILY_ORDER_LIMIT_YEN=1000000`)

#### 8d-10. ADR + ドキュメント
- 本 ADR Status=Completed 更新
- INCIDENT_RESPONSE.md §4 の REAL モード解除条件に関する記述強化
- RB-001 (Kill Switch) に REAL モード復旧の特記事項追記

### Why
- Phase 8c (PAPER_LIVE) で戦略の信頼性が検証された後に、実マネー運用へ
- **CLAUDE.md 絶対禁止 1 領域** のため、確認文字列 + 段階移行 + 冪等性 + テスト充実 を多重防御
- 失敗時の Kill Switch 自動発火で損失を最小化

## Consequences (結果)

### ✅ Positive
- 本番稼働で実マネー運用が可能になる (プロダクトの最終ゴール)
- 冪等性キー + 段階移行で誤発注・二重発注リスクを最小化
- Profit Sweep の REAL 経路完成で要件 §6 オーバーナイトリスク排除が実マネーで動作

### ⚠️ Negative / Trade-off
- **実マネー運用 = 損失リスク**。戦略バグや市場急変で実損が発生し得る
- kabu API 仕様の正確な把握が必須 (誤った FrontOrderType 等で意図と異なる注文)
- テストの代用度に限界 (本物の発注テストは危険でできない)

### 🔄 Required Follow-up
- 📋 着手前 (必須): Phase 8c 完了 + **1〜2 週間 PAPER_LIVE 運用検証** + 戦略の信頼性確認
- 📋 着手前: kabu API 公式仕様 ([backend/src/api/specs/kabu_STATION_API.yaml](../../backend/src/api/specs/kabu_STATION_API.yaml)) 全件読込
- 📋 着手中: 8d-1〜8d-9 順次実装 (8d-9 最後に手順書化)
- 📋 完了後: 4 subagents 全件 PASS
- 📋 完了後: ユーザー側 100株テスト → 段階拡大 (本 ADR スコープ外)
- 📋 完了後: 1 ヶ月運用後にポストモーテム整備

## Alternatives Considered (検討した代替案)

### 案 A: place_order のみ実装、Profit Sweep は後回し
- **却下理由**: Profit Sweep が REAL で動かないと **オーバーナイトリスクが発生** (要件 §6 違反)。同時実装が必須

### 案 B: 段階移行手順書を運用後に作成
- **却下理由**: 手順書なしで本番運用すると誤操作リスク。事前ドキュメント化が安全

### 案 C: 冪等性キーは将来拡張で
- **却下理由**: REAL モードで二重発注はそのまま実損になる。最小限の二重発注防止は必須

## Related (関連)

- 前提 ADR: [ADR-0012](0012-phase8c-scope-paper-live-mode.md) (Phase 8c PAPER_LIVE)
- CLAUDE.md 絶対禁止 1: TRADE_MODE=REAL 切替には明示確認必須
- 関連 Runbook: [RB-001](../runbooks/RB-001-kill-switch-fired.md), [RB-002](../runbooks/RB-002-kabu-api-down.md), [RB-006](../runbooks/RB-006-profit-sweep-incomplete.md)
- 関連要件: [REQUIREMENTS.md §5](../REQUIREMENTS.md), [REQUIREMENTS_DEFINITION.md §6](../REQUIREMENTS_DEFINITION.md)

## Notes

### Phase 8d 推奨実装順序

1. **kabu API 仕様読込** (kabu_STATION_API.yaml の /sendorder / /cancelorder / /orders 全件)
2. **Trade_Logs schema に idempotency_key 列追加**
3. **kabucom.place_order 本実装** + mock テスト充実
4. **kabucom.cancel_order / get_orders 追加**
5. **scheduler.job_short_term_trade REAL 分岐本実装**
6. **scheduler.job_profit_sweep REAL 分岐本実装**
7. **連続失敗時の短期 bucket KS 自動発火** (リトライポリシー)
8. **段階移行手順書 (REAL_MODE_MIGRATION.md) 作成**
9. **INCIDENT_RESPONSE.md §4 の REAL モード解除条件強化**
10. 4 subagents 検証

### Phase 8d 完了の Definition of Done
- kabucom.place_order / cancel_order / get_orders が mock テスト全件 PASS
- TRADE_MODE=REAL で scheduler.job_short_term_trade が実発注パスに到達 (mock で検証)
- TRADE_MODE=REAL で job_profit_sweep が全 short 反対売買を発注 (mock で検証)
- 冪等性キー二重発注防止が動作 (リグレッションテスト)
- 連続発注失敗で短期 bucket KS 自動発火 (テスト)
- docs/operations/REAL_MODE_MIGRATION.md が完成し、ステップ手順が明確
- secrets-scanner / paper-trade-validator / discord-notify-validator / db-schema-reviewer 全件 PASS
- 本 ADR Status=Completed
- ユーザー側 100株テスト着手は本 ADR の DoD 外 (運用フェーズの判断)

### REAL モード起動の物理ロック (多重防御)

1. **環境変数**: `TRADE_MODE=REAL` + `RUNNER_REAL_CONFIRM='I_UNDERSTAND_REAL_TRADING_RISK'` 必須
2. **runner preflight**: SSM 認証 / Kill Switch / kabu authenticate 全件成功必須
3. **冪等性キー**: 同一 UUID の二重発注は DB で物理ブロック
4. **連続失敗 KS**: place_order 5 回連続失敗で短期 bucket KS 自動発火
5. **CLAUDE.md 絶対禁止 1**: コード上はこれらでロック、運用上は明示ユーザー確認

つまり TRADE_MODE=REAL を環境変数設定するだけでは起動しない (runner preflight で 4 段階チェック)、起動できても誤発注は冪等性キー / KS で物理的にブロックされる。多重防御 = fail-safe 思想。