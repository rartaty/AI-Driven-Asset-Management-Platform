# ADR-0006: Phase 5 スコープ = 案 Bridge (Operations 隙間埋め)

- **Status**: Completed (2026-05-09 実装完了 + DoD 検証完了)
- **Date**: 2026-05-09 (採択 + 同日完了)
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0001 (DB 選定), ADR-0002 (動的比率), ADR-0003 (SSM Standard), ADR-0004 (ハーネス9層), ADR-0005 (Phase 4 案 B Standard)

## DoD 検証結果 (2026-05-09)

| # | DoD 項目 | 結果 |
| --- | --- | --- |
| 1 | system.py / reports.py に `Depends(verify_admin_token)` 配線 (E.5.1 完全達成) | ✅ |
| 2 | VIXギア発火・四半期定期 → Target_Portfolio 書込が動作 | ✅ services/target_portfolio.py 新規 + market_context.py から自動配線 |
| 3 | portfolio_sync.py が各 bucket を Daily_Asset_Snapshot に書込 (旧 schema バグゼロ) | ✅ write_daily_snapshot 実装 + scheduler.job_sync_portfolio 経由で永続化 |
| 4 | passive_core.calculate_rebalance_amounts が Target_Portfolio から動的比率を読込 | ✅ scheduler.job_weekly_rebalance で get_active_ratios + 60/40 株式内分解 |
| 5 | kabucom/opencanvas の `print()` 残存ゼロ (logger.error + notify_system) | ✅ 全 8 箇所置換完了 |
| 6 | 環境変数で `TEST_MODE` 残存ゼロ (TRADE_MODE=PAPER/REAL に統一) | ✅ 4 ファイル統一 + .env.example deprecate |
| 7 | pytest 全件 Pass + Phase 5 範囲のテスト追加 | ✅ 112/112 PASS (新規 24 件: test_portfolio_sync 11 + test_target_portfolio 13) |
| 8 | secrets-scanner / discord-notify-validator / paper-trade-validator 全て PASS | ✅ subagent 3 件全て PASS |
| 9 | 本 ADR の Status を Completed に更新 | ✅ 本ファイル更新 |
| 10 | audit doc / harness-taskboard.md Activity Log 追記 | ✅ docs/audit/2026-05-09-progress.md 追記 |

**追加対応 (paper-trade-validator High finding)**:
- PAPER モード買付がキルスイッチを通過していたバグを修正 ([paper_trader.py](../../backend/src/services/paper_trader.py) execute_virtual_order に db 引数 + assert_inactive_for_entry チェック追加)
- 関連テスト 2 件追加 (test_paper_buy_blocked_when_kill_switch_active / test_paper_sell_allowed_when_kill_switch_active)
- routers/system.py の test/buy エンドポイントに db 注入

**残タスク (Phase 6 候補)**:
- ~~既存 issue: morning_sync が 08:30 (REQUIREMENTS.md §4.3 は 09:00)~~ → **誤記述だった**: REQUIREMENTS_DEFINITION.md §7 で「朝8:30のトークン取得」と明記されており、scheduler.py の 08:30 は要件通り正しい (Phase 8a で訂正)
- M9 runner / M10 WebSocket / M11 Frontend 残機能 (ADR-0006 から持ち越し継続)
- 設計議論クローズ (DB Bucket / Tick Data)
- IR-1 障害対応プロトコル

**関連**: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)

---

## Context (背景)

Phase 4 (案 B Standard / [ADR-0005](0005-phase4-scope-standard.md)) の DoD 検証を 2026-05-09 に実施したところ、当初 [docs/audit/2026-05-03-pre-phase4.md](../audit/2026-05-03-pre-phase4.md) で「Critical High M リスト」として持ち越し対象だった項目の多くが、実は **既に実装済** であることが判明した:

| audit 項目 | audit 当時 | 2026-05-09 実態 |
| --- | --- | --- |
| M1 Kill Switch (drawdown) | 未実装 | ✅ 実装済 (Phase 4) |
| M2 生活防衛費保護 | 未実装 | ✅ 実装済 (kill_switch.py に統合 + test PASS) |
| M3 VIXギア発火判定 | 未実装 | ✅ 実装済 (market_context.py:44 evaluate_vix_gear + test PASS) |
| M5 朝08:30 / 週次/年次 rebalance | 未実装 | ✅ 実装済 (scheduler.py に登録 + test PASS) |
| M7 EDINET XBRL | 未実装 | ✅ 実装済 (api/edinet.py 完備 + test PASS) |
| M4 FastAPI 認証 | 未実装 | ⚠️ partial (portfolio/reports/analytics 配線済、system.py 未配線) |
| P9 動的比率対応 | 旧固定 50/30/20 | ⚠️ partial (calculate_rebalance_amounts は引数で動的対応可、Target_Portfolio 連携が未配線) |

つまり Phase 4 の実装範囲は ADR-0005 で確定したスコープを **超えており**、当初想定の Phase 5 残課題リストは大幅に縮小可能。一方で:
- **Target_Portfolio テーブルは schema.py だけが参照** — VIXギア判定後の書込処理 / passive_core 等から読込する処理が未実装
- **portfolio_sync.py:66** に旧 schema バグ (`trust_value: bank_balance` という意味的に破綻したマッピング) が残存
- **system.py** に auth dependency 未配線
- **kabucom.py** に `print()` 5 箇所残存 (discord-notify-validator が指摘)

これらは「機能の穴」というより「**完成度の隙間**」であり、Phase 5 として一括処理するのが筋。

加えて、当初 audit で Phase 5 候補とされた以下は **より大規模な作業** で、別フェーズが妥当:
- M9 runner/ (本番化前作業 — PAPER 継続中は不要)
- M10 WebSocket クライアント ([Tick Data Pipeline 設計](../architecture/TICK_DATA_PIPELINE_DESIGN.md) クローズと統合すべき)
- M11 Frontend 残機能 9 項目中 7 件 (規模大・機能別フェーズ分解推奨)

## Decision (決定)

**Phase 5 のスコープを 案 Bridge (Operations 隙間埋め) で確定**: Phase 4 完成度の隙間を塞ぎ、動的ポートフォリオ運用の閉じたループを完成させる。期間目安 1〜2 日程度。

### スコープ詳細

**含む (Phase 5)**:
1. **M4 完全配線**: [system.py](../../backend/src/routers/system.py) に `Depends(verify_admin_token)` 追加 (mock_data.py は本番化時 cleanup 候補として保留)
2. **P9 動的比率の閉じたループ**:
   - VIXギア発火判定 (`evaluate_vix_gear`) の結果を `Target_Portfolio` テーブルに書込
   - 四半期定期見直しジョブを scheduler.py に追加 (1月/4月/7月/10月)
   - `calculate_rebalance_amounts` が `Target_Portfolio` から最新の動的比率を読込
3. **P11 portfolio_sync 整合修正**:
   - `trust_value: bank_balance` の旧 schema バグ修正
   - 各 bucket 評価額を kabuステーション API から正しく集計
   - `Daily_Asset_Snapshot` への日次書込ジョブ完成
4. **kabucom.py / opencanvas.py クリーンアップ**:
   - `print()` 5 箇所 → `logger.error()` + `notify_system()` (discord-notify-validator 指摘対応)
   - エラーメッセージの ".env" 文脈削除 (SSM/環境変数 fallback 整合)
5. **TEST_MODE / TRADE_MODE 二重整理**:
   - 命名統一 (`TRADE_MODE` を正、`TEST_MODE` を deprecate)
   - 全ファイルで参照を統一
6. **テスト追加**:
   - Target_Portfolio 書込・読込テスト
   - portfolio_sync 修正後の各 bucket 集計テスト
   - system.py auth 配線テスト

**含まない (Phase 6 以降に持ち越し)**:
- M9 runner/main_trade.py / backtest.py (本番化前作業 / Phase 7 候補)
- M10 WebSocket クライアント (Tick Data Pipeline 設計クローズと統合)
- M11 Frontend 残機能 (多角的時間軸 / イントラデイ / 銘柄照会 / AI タイムライン等 — 機能別フェーズ分解)
- 設計議論クローズ ([DB_BUCKET_ISOLATION_DESIGN.md](../architecture/DB_BUCKET_ISOLATION_DESIGN.md) / [TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md))
- Phase 6 (SRE/Operations 基盤・OPS-1/OPS-2) — IR-1 (障害対応プロトコル) 含む
- mock_data.py の本番化前 cleanup
- git 化 (本日 2026-05-09 時点で Git 未インストール、ユーザー側設定後に再開予定)

**含めない理由 (案 Bridge 選定根拠)**:
- 案 Heavy (M9/M10/M11 + Frontend 残全部) は 1〜2 週間規模で集中力分散
- 案 Skip (Phase 5 をスキップして Phase 6 直行) は完成度の穴を残し、Phase 6 で SRE 基盤を作っても観測対象 (Target_Portfolio 連携等) が未完成のまま
- 案 Bridge は「動的ポートフォリオの閉じたループ完成」を 1〜2 日で達成し、Phase 6 (SRE) や設計議論クローズに移行する地ならしになる

### Why
- audit 過小評価により残課題が縮小し、本来の Phase 5 (M2/M3/M5/M7 の M リスト塞ぎ) は既に Phase 4 で達成済 → 「次フェーズ」の意味を再定義する必要あり
- Target_Portfolio 連携が未配線では「動的ポートフォリオ」の要件 ([REQUIREMENTS_DEFINITION.md §2.2](../REQUIREMENTS_DEFINITION.md)) が形骸化する
- portfolio_sync.py の旧 schema バグは UI 表示までの全パスを破綻させるため、優先度高
- discord-notify-validator が指摘した kabucom.py の print() は API 障害通知抜けに直結 (個人運用でも痛い)

## Consequences (結果)

### ✅ Positive
- 「動的ポートフォリオ・投資判断ファースト原則」 ([ADR-0002](0002-dynamic-portfolio-investment-first.md)) の閉じたループが初めて稼働可能になる
- portfolio_sync 修正により Frontend dashboard の表示精度が現実値と整合
- Phase 6 (SRE/Operations 基盤) の前提となる「観測対象が完成している状態」を整える
- 軽微クリーンアップ (print/エラーメッセージ/環境変数) を一括処理してメンテナビリティ向上

### ⚠️ Negative / Trade-off
- **期間 1〜2 日想定**: Phase 4 (3 日) より小さく、しかし複数領域を跨ぐためレビュー範囲は広い
- **テスト追加負荷**: Target_Portfolio 連携・portfolio_sync 集計の正確性検証
- **mock_data.py 等の cleanup は持ち越し**: 本番化時に残課題として再浮上する

### 🔄 Required Follow-up
- ✅ 着手前: Phase 4 DoD 完了確認 ([ADR-0005](0005-phase4-scope-standard.md) Status=Completed)
- ✅ 着手前: 残課題の精度確認 (audit 過小評価判明)
- 📋 着手中: Phase 5 サブタスクの順序決定 (推奨: M4 → P11 → P9 → クリーンアップ → 環境変数整理)
- 📋 完了後: pytest 全件 Pass + 各サブエージェント検証
- 📋 完了後: ADR-0007 (Phase 6 SRE/Operations 基盤・OPS-1/OPS-2 統合) 起草
- 📋 完了後: 設計議論クローズの再開判断 (DB Bucket / Tick Data 各 OQ/TQ)

## Alternatives Considered (検討した代替案)

### 案 Heavy (Frontend 残機能 + WebSocket + runner/ 全部)
- **概要**: M9/M10/M11 を Phase 5 に全て含める・規模 1〜2 週間
- **却下理由**: 集中力分散・WebSocket は Tick Data Pipeline 設計クローズ後の方が最適・Frontend は機能別フェーズ分解が筋

### 案 Skip (Phase 5 をスキップ)
- **概要**: Phase 4 完成済を以て Phase 6 (SRE) に直行
- **却下理由**: Target_Portfolio 連携未配線・portfolio_sync バグ残存のまま観測基盤を作っても監視対象が破綻している

### 案 Cleanup-only (軽微修正のみ)
- **概要**: kabucom.py print + 環境変数整理 + system.py auth のみ
- **却下理由**: P9/P11 を後回しにすると「動的ポートフォリオの閉じたループ」が欠落したまま。設計議論や次フェーズに進みにくい

## Related (関連)

- 監査スナップショット: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)
- Phase 4 ADR: [ADR-0005](0005-phase4-scope-standard.md)
- 設計議論 (Phase 5 では扱わない): [DB_BUCKET_ISOLATION_DESIGN.md](../architecture/DB_BUCKET_ISOLATION_DESIGN.md), [TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md)
- ハーネスタスクボード: [.claude/harness-taskboard.md](../../.claude/harness-taskboard.md) (OPS-1/2 は Phase 6 候補)
- 関連要件: [REQUIREMENTS_DEFINITION.md §2.2](../REQUIREMENTS_DEFINITION.md) (ポートフォリオ見直しタイミング)

## Notes

### Phase 5 推奨実装順序 (依存関係順)

1. **M4 完全配線** (system.py に auth) — 軽微・他作業のブロッカーではない・先に塞ぐ
2. **P11 portfolio_sync 整合修正** — Daily_Asset_Snapshot 書込ロジック完成。後続の P9 が依存
3. **P9 Target_Portfolio 連携** — VIXギア発火 → Target_Portfolio 書込 / passive_core が読込 / 四半期ジョブ追加
4. **kabucom/opencanvas クリーンアップ** — print → logger / エラーメッセージ修正 (独立)
5. **TEST_MODE / TRADE_MODE 整理** — 全ファイル横串・最後にやるのが安全
6. **統合テスト** — pytest 全件 + secrets-scanner + discord-notify-validator + paper-trade-validator (新規 Target_Portfolio フローを paper-trade-validator で検証)

### Phase 5 完了の Definition of Done
- system.py に `Depends(verify_admin_token)` 配線済 (E.5.1 完全達成)
- VIXギア発火・四半期定期 → Target_Portfolio 書込が動作 (PAPER モード end-to-end 検証)
- portfolio_sync.py が各 bucket 評価額を正しく Daily_Asset_Snapshot に書込 (旧 schema バグゼロ)
- passive_core.calculate_rebalance_amounts が Target_Portfolio の最新 effective_date レコードから target_ratios を読込
- kabucom.py / opencanvas.py に `print()` 残存ゼロ (`logger.error()` + `notify_system()` で統一)
- 環境変数で `TEST_MODE` 残存ゼロ (`TRADE_MODE=PAPER/REAL` に統一・deprecate 期間 0)
- pytest 全件 Pass + Phase 5 範囲のテスト追加
- secrets-scanner / discord-notify-validator / paper-trade-validator 全て PASS
- 本 ADR の Status を Completed に更新
- audit doc / harness-taskboard.md Activity Log 追記
