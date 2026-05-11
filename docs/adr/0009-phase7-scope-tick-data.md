# ADR-0009: Phase 7 スコープ = Tick Data Pipeline 実装 (Foundation + WebSocket)

- **Status**: Completed (2026-05-09 実装完了 + DoD 検証完了)
- **Date**: 2026-05-09 (採択 + 同日完了)
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0001 (SQLite 一次採用), ADR-0006 (Phase 5 案 Bridge), ADR-0007 (DB Bucket Isolation), ADR-0008 (Tick Data 設計決定)

## DoD 検証結果 (2026-05-09)

| # | DoD 項目 | 結果 |
| --- | --- | --- |
| 1 | Market_Ticks schema 追加 + db-schema-reviewer PASS | ✅ Pass 6/Deviations 3 (PK 順序・Enum・コメント全て修正済) |
| 2 | services/market_data.py PAPER/REAL 両モード動作可能 | ✅ Layer 1/2 + LR-EMO + yfinance 変換 |
| 3 | WebSocket クライアント モックテスト PASS | ✅ 13/13 PASS (test_kabu_push_client.py) |
| 4 | vwap_short.evaluate_vwap_signal が Layer 1 から判定返却 | ✅ 5/5 新規テスト PASS |
| 5 | scheduler.job_short_term_trade が TODO → 実装 | ✅ Asset_Master query + signal + paper_trader 配線 |
| 6 | pytest 全件 PASS (Phase 7 範囲のテスト追加) | ✅ 159/159 PASS (Phase 5 完了時 112 → +47 件) |
| 7 | secrets-scanner / discord-notify / paper-trade-validator PASS | ✅ 3 subagents 全件 PASS (中軽微指摘は対応済) |
| 8 | 本 ADR Status=Completed | ✅ 本ファイル更新 |
| 9 | audit doc / taskboard.md Activity Log 追記 | ✅ |

**M10 (WebSocket クライアント) audit リスト残課題を解消**。短期戦略 vwap_short が初めて入力データソースを持ち機能化。

**Subagent 指摘の対応**:
- discord-notify-validator (Med): VWAP signal 発火時の Discord notify_trade 追加 (要件 §2.3 整合)
- discord-notify-validator (Low): WebSocket stop 通知追加
- paper-trade-validator (Low): job_profit_sweep の execute_virtual_order に db 引数追加 (一貫性)

**残タスク (Phase 8 持ち越し)**:
- M9 runner/main_trade.py / backtest.py
- M11 Frontend 残機能
- TimescaleDB hypertable 移行
- REAL モード実発注パス (kabucom.place_order 本実装)
- ~~morning_sync 08:30 vs 仕様 09:00~~ → **誤記述だった** (REQUIREMENTS_DEFINITION.md §7 「朝8:30」が要件、scheduler.py は要件通り)。Phase 8a で訂正

---

## Context (背景)

Phase 5 完了 (2026-05-09) 時点で短期トレード戦略 ([vwap_short.py](../../backend/src/strategy/vwap_short.py)) は VWAP/Z スコア計算ロジックを持つが、**入力データ源が空** で実質機能していない。歩み値 (Time & Sales) を kabu Push API から再構築する設計は ADR-0008 で確定済だが、実装は未着手。

設計議論クローズ後、Phase 6 (SRE/Operations 基盤) を先行する案も検討されたが、ユーザー判断により **歩み値実装が先行** することに決定。Phase 6 で整える運用品質より先に、プロダクト本体の完成度を上げる方針。

ADR-0008 §Notes の「Phase 7 着手時のチェックリスト」を本 ADR でスコープ化する。

## Decision (決定)

**Phase 7 のスコープを Tick Data Pipeline 実装で確定**。期間目安 2〜3 日。

### スコープ詳細

**含む (Phase 7)**:

#### 7-1. Market_Ticks スキーマ追加
- [backend/src/models/schema.py](../../backend/src/models/schema.py) に `Market_Ticks` テーブル追加:
  - `timestamp` (DateTime, PK), `ticker_symbol` (String, FK→Asset_Master, PK)
  - `last_price` (BigInteger), `cumulative_volume` (BigInteger), `delta_volume` (BigInteger)
  - `bid_price` (BigInteger nullable), `ask_price` (BigInteger nullable)
  - `side_inference` (String — `BUY_AGGR` / `SELL_AGGR` / `MID`)
  - `is_synthetic` (Boolean default=False — gap 補完か否か)
- 複合 index (ticker_symbol, timestamp) で時系列クエリ最適化
- `db-schema-reviewer` subagent でレビュー

#### 7-2. データ層 (services/market_data.py 新規)
- **Layer 1 (HOT in-memory)**: ticker 毎の `collections.deque(maxlen=N)` でリアルタイム保持
- **Layer 2 (WARM SQLite)**: 1 秒バッファ → bulk insert (非同期 / メイン path をブロックしない)
- **PAPER モード**: yfinance 1 分足取得 → 疑似 tick 変換 (volume 一様分布按分 / side='MID' 固定)
- **REAL モード**: kabu Push API 受信 (Phase 7-3 の WebSocket クライアント経由)
- 戦略向け公開 API:
  - `get_recent_ticks(ticker, n=100) -> List[Market_Ticks]`
  - `compute_vwap(ticker, window_seconds=300) -> float`
  - `compute_zscore(ticker, window_seconds=300) -> float`

#### 7-3. WebSocket クライアント (M10 解消)
- `websocket-client` 依存を [requirements.txt](../../backend/requirements.txt) に追加
- `services/kabu_push_client.py` 新規:
  - kabu Station Push API への WebSocket 接続 (localhost)
  - 自動再接続 (exponential backoff)
  - 受信 push → market_data.layer1_push() に転送
- TRADE_MODE=PAPER 時は接続せず、yfinance 周回ジョブ (5秒/分間隔) で代替
- gap 検出: `cumulative_volume` 単調性チェック → REST API 補完 + `is_synthetic=True`

#### 7-4. vwap_short 戦略の Layer 1 接続
- 既存 [vwap_short.py](../../backend/src/strategy/vwap_short.py) の `calculate_vwap_and_zscore` 等を維持
- 新規エントリポイント: `evaluate_vwap_signal(ticker_symbol)` を追加:
  - `services/market_data.get_recent_ticks(ticker)` から Layer 1 deque を取得
  - VWAP/Z スコア計算 + シグナル判定
- scheduler.job_short_term_trade に配線 (現状 TODO のまま)

#### 7-5. テスト追加
- `backend/tests/test_market_data.py` (新規):
  - Layer 1 deque の push/get の基本動作
  - PAPER モード時の yfinance 変換 (ボリューム按分 / side_inference)
  - gap 検出 + REST 補完 + `is_synthetic` フラグ
  - SQLite flush の bulk insert 動作 (in-memory)
- `backend/tests/test_kabu_push_client.py` (新規):
  - WebSocket 接続のモック (`websockets.client.connect` patch)
  - 切断時の再接続バックオフ
  - 受信メッセージのパース正常系・異常系
- `backend/tests/test_vwap_short_integration.py` (新規):
  - Layer 1 → vwap_short → シグナル判定の end-to-end (PAPER モード)

#### 7-6. scheduler.py 配線
- `job_short_term_trade` の TODO を実装:
  - Layer 1 deque から `get_recent_ticks` で取得
  - vwap_short.evaluate_vwap_signal で判定
  - シグナル発火 → paper_trader.execute_virtual_order (PAPER) / kabucom.place_order (REAL stub)
- `job_morning_sync` (08:30) で WebSocket 接続開始
- `job_profit_sweep` (14:50) で WebSocket 切断 (オーバーナイト不要)

**含まない (Phase 8 以降に持ち越し)**:
- TimescaleDB hypertable 移行 (Phase 8 = ADR-0007 の PG 移行と統合)
- Continuous Aggregates (1分/5分/1時間 OHLCV のマテビュー化)
- 圧縮ポリシー / Retention Policy
- 多角的時間軸 Frontend 表示 (M11 / Phase 7+ の機能別フェーズ)
- Frontend イントラデイチャート (M11)
- runner/main_trade.py / backtest.py (M9 / Phase 8+)
- 短期トレード REAL モードの実発注パス完成 (kabucom.place_order の本実装)

### Why
- vwap_short 戦略を初めて稼働可能にする
- M10 (WebSocket) 未解消が Phase 5 audit で残っていた最大の欠損
- ADR-0008 設計が固まっており、設計議論なしで即実装可能
- PAPER モードが yfinance ベースで完結 → 実 API 環境なしでも検証ループが回る

## Consequences (結果)

### ✅ Positive
- **vwap_short が初めて入力データを持つ** — 短期戦略が機能化
- M10 (WebSocket クライアント) を解消、audit リスト残課題を 1 件削減
- データ層責務の境界が明確化 (戦略は Layer 1 を読むだけ)
- Tick Data 規模 (実測値) が判明し、Phase 8 (PG/TimescaleDB 移行) のタイミング判断材料が揃う
- PAPER モードで完結するため、実 API なしでも開発・テスト可能

### ⚠️ Negative / Trade-off
- **期間 2〜3 日想定**: Phase 5 (1〜2 日) より大きく、複数領域 (schema / services / scheduler / tests) を跨ぐ
- WebSocket 関連テストはモック (`websocket-client` の挙動シミュレート) で、本物の kabu Station API 接続テストは別途必要
- yfinance 疑似 tick は精度低 (1 分内一様分布仮定) → REAL モードでの本格バックテストには限界あり
- bucket 別キルスイッチ (Phase 6 候補) が未実装の状態で短期戦略を稼働 → 全体キルスイッチでカバー

### 🔄 Required Follow-up
- ✅ 着手前: ADR-0008 (Tick Data 設計決定) 確定
- ✅ 着手前: Phase 5 完了 + 設計議論クローズ完了
- 📋 着手中: Phase 7 サブタスクの順序決定 (推奨: 7-1 schema → 7-2 services/market_data.py → 7-5 tests → 7-3 WebSocket → 7-4 vwap_short 接続 → 7-6 scheduler 配線)
- 📋 完了後: pytest 全件 PASS + db-schema-reviewer / paper-trade-validator / discord-notify-validator 全件 PASS
- 📋 完了後: ADR-0010 (Phase 8 = SRE/Operations or PG/TimescaleDB 移行) 起草

## Alternatives Considered (検討した代替案)

### 案 A: Phase 6 (SRE/Operations) 先行
- **概要**: bucket 別キルスイッチ + バックアップ + IR-1 を先に整える
- **却下理由**: 観測対象 (短期戦略) が機能していない状態で運用基盤を作っても効果薄。プロダクト本体を先に完成させる方が筋

### 案 B: Phase 7a (PAPER のみ) と Phase 7b (REAL/WebSocket) に分割
- **概要**: yfinance ベースの PAPER 実装で先に検証、WebSocket は別フェーズ
- **却下理由**: M10 (WebSocket) を Phase 7 に含めないと audit リストに残課題が残る。yfinance 単独ではバックテスト精度が出ない

### 案 C: Phase 8 (PG + TimescaleDB) を含めた一括実施
- **概要**: ADR-0007 の PG 移行と統合
- **却下理由**: スコープ膨張・1 週間規模で集中力分散。Phase 7 の SQLite 実装でデータ規模を実測してから Phase 8 を計画する方が判断精度高い

## Related (関連)

- 設計メモ: [TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md)
- Tick Data 設計決定: [ADR-0008](0008-tick-data-pipeline-decisions.md)
- DB Bucket Isolation: [ADR-0007](0007-db-bucket-isolation-decisions.md)
- Phase 5 完了: [ADR-0006](0006-phase5-scope-bridge.md)
- 短期戦略詳細: [SHORT_VWAP_DETAIL.md](../strategy/SHORT_VWAP_DETAIL.md)
- 関連要件: [REQUIREMENTS_DEFINITION.md §B.2](../REQUIREMENTS_DEFINITION.md) (短期は ms 級レイテンシ要件)
- Audit: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)
- Taskboard: [taskboard.md](../../taskboard.md)

## Notes

### Phase 7 推奨実装順序 (依存関係順)

1. **7-1 Market_Ticks schema 追加** (db-schema-reviewer 監査も)
2. **7-2 services/market_data.py 実装** (Layer 1 deque + Layer 2 flush + PAPER 変換)
3. **7-5 test_market_data.py 追加** (実装と並行)
4. **7-3 WebSocket クライアント** (services/kabu_push_client.py + 依存追加 + テスト)
5. **7-4 vwap_short 戦略接続** (evaluate_vwap_signal 追加)
6. **7-6 scheduler 配線** (job_short_term_trade 実装 / job_morning_sync で WebSocket 接続)
7. 統合テスト + 3 subagents 検証
8. ADR Status=Completed 更新

### Phase 7 完了の Definition of Done
- `Market_Ticks` schema が schema.py に存在し db-schema-reviewer PASS
- `services/market_data.py` が PAPER/REAL 両モードで動作可能
- WebSocket クライアントがモックテスト PASS (本物の kabu Station 接続は別途)
- vwap_short.evaluate_vwap_signal が Layer 1 deque から読み判定を返す
- scheduler.job_short_term_trade が TODO → 実装に変わる
- pytest 全件 PASS (現 112 件 + Phase 7 範囲のテスト追加)
- secrets-scanner / paper-trade-validator / discord-notify-validator / db-schema-reviewer 全件 PASS
- 本 ADR Status=Completed 更新
- audit doc / taskboard.md Activity Log 追記
