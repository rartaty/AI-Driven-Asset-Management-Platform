# ADR-0011: Phase 8a スコープ = Runner (M9) + Doc Sync (個人ローカル運用版)

- **Status**: Completed (2026-05-09 実装完了 + DoD 検証完了)
- **Date**: 2026-05-09 (採択 + 同日完了)
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0007 (DB Bucket Isolation), ADR-0008 (Tick Data 設計決定), ADR-0009 (Phase 7 / M10 解消), ADR-0010 (Phase 6 SRE/Operations)

---

## Context (背景)

Phase 6 (SRE/Operations) 完了後の残タスクを以下に整理:
- M9 runner/main_trade.py / backtest.py
- M11 Frontend 残機能 (4 component)
- TimescaleDB hypertable 移行 (PG 移行と統合)
- REAL モード実発注パス (kabucom.place_order)
- TECHNICAL_SPECIFICATION.md / DB_MIGRATION_GUIDE.md の Phase 6/7 反映
- 既存 issue: morning_sync 08:30 vs 仕様 09:00 (実は誤記述だった)

これらを一括 Phase 8 にすると規模過大。**Phase 8a (Runner + Doc Sync) を分離** し、M11 / PG / REAL モードは後続フェーズへ。

ユーザー要望「git 化以外進めて」を受け、git 化を除く軽量タスクを Phase 8a として実施。

## Decision (決定)

**Phase 8a スコープ確定**:

### 8a-1. morning_sync 記述訂正 (Quick win)
- ADR-0006/0009/0010 / taskboard.md / audit doc に記載の「morning_sync 08:30 vs 仕様 09:00」は **誤記述** だった
- 正規要件は [REQUIREMENTS_DEFINITION.md §7](../REQUIREMENTS_DEFINITION.md) 「**朝8:30**のトークン取得」
- scheduler.py の 08:30 は要件通り正しい → 訂正済

### 8a-2. TECHNICAL_SPECIFICATION.md §6 更新 (Quick win)
- 12 → 13 テーブル (Market_Ticks 追加)
- §6.4 `Market_Ticks` 仕様追記
- §6.5 `User_Settings` の bucket 別キルスイッチ列 4 個追記
- §6.6 残テーブル概要追加

### 8a-3. DB_MIGRATION_GUIDE.md §6 追加 (Quick win)
- Phase 6/7 の累積 schema 変更を ALTER TABLE 文として明文化
- 6.1 bucket 別キルスイッチ 4 列追加
- 6.2 Market_Ticks 新規テーブル
- 6.3 Phase 8 PG 移行で統合適用予定

### 8a-4. M9 Runner 実装
- `backend/src/runner/main_trade.py` 新規:
  - 起動前 preflight (TRADE_MODE / SSM / Kill Switch)
  - REAL モード起動に環境変数 `RUNNER_REAL_CONFIRM` 必須 (CLAUDE.md 絶対禁止 1 整合)
  - SIGINT/SIGTERM ハンドラで scheduler.shutdown 呼び出し
- `backend/src/runner/backtest.py` 新規:
  - vwap_short の rolling window バックテスト
  - 結果を JSON 出力 (stdout or --output)
  - tz-aware datetime (Market_Ticks.timestamp と整合)
- `backend/tests/test_runner.py` 新規 (11 件)

**含まない (Phase 8b 以降)**:
- M11 Frontend 残機能 (4 component / 機能別フェーズ分解推奨)
- REAL モード kabucom.place_order 本実装 (CLAUDE.md 絶対禁止 1 領域・別途慎重に)
- TimescaleDB hypertable 移行 (Phase 8c = PG 移行と統合)
- v6_long / passive_core のバックテスト (Phase 8b)

## DoD 検証結果 (2026-05-09)

| # | DoD 項目 | 結果 |
| --- | --- | --- |
| 1 | morning_sync 記述訂正 (4 ADR + taskboard) | ✅ 全件訂正済 |
| 2 | TECHNICAL_SPECIFICATION.md §6 (13 テーブル化) | ✅ §6.4-6.6 追記完了 |
| 3 | DB_MIGRATION_GUIDE.md §6 (累積 schema 変更履歴) | ✅ §6.1-6.3 追記完了 |
| 4 | runner/main_trade.py preflight + scheduler 起動 | ✅ |
| 5 | runner/backtest.py vwap_short rolling window | ✅ |
| 6 | pytest 全件 PASS | ✅ 202/202 PASS (Phase 6 完了時 191 → +11 件) |
| 7 | secrets-scanner / paper-trade-validator / discord-notify-validator PASS | ✅ 全件 PASS (paper-trade Med/Low → 修正済) |
| 8 | 本 ADR Status=Completed | ✅ |
| 9 | audit doc / taskboard.md Activity Log 追記 | ✅ |

**Subagent 指摘の対応**:
- paper-trade-validator (Med): main_trade.py の `is_active()` 例外を `PreflightError` でラップ (修正済)
- paper-trade-validator (Low): backtest.py の datetime を tz-aware (UTC) に変換 (修正済)

## Consequences (結果)

### ✅ Positive
- M9 runner audit リスト残課題を解消
- 本番稼働 (REAL) への entry point が確立 (preflight で誤起動防止)
- バックテスト基盤が稼働 → Runbook 復旧後の検証が可能 ([RB-008](../runbooks/RB-008-data-db-corruption.md))
- TECHNICAL_SPECIFICATION.md が現実装と整合 (audit 過小評価事案の再発防止)
- DB_MIGRATION_GUIDE.md に累積 migration 履歴が文書化 → Phase 8 PG 移行時の参照資料

### ⚠️ Negative / Trade-off
- v6_long / passive_core のバックテスト未実装 (vwap_short のみ)
- REAL モード本番発注パス未完成 (kabucom.place_order が stub のまま)
- Frontend M11 4 component 残存

### 🔄 Required Follow-up
- 📋 Phase 8b 候補: v6_long / passive_core バックテスト + Frontend M11 (機能別)
- 📋 Phase 8c 候補: REAL モード kabucom.place_order 本実装 (CLAUDE.md 絶対禁止 1 領域・特に慎重)
- 📋 Phase 9 候補: PG + TimescaleDB 移行 (ADR-0007 + ADR-0008 統合)

## Alternatives Considered (検討した代替案)

### 案 A: 全残タスクを Phase 8 に一括
- **概要**: Runner + M11 + REAL + PG 移行 を一括実装
- **却下理由**: 規模過大 (1 週間以上)、集中力分散、REAL モードと PG 移行は別軸でリスク高

### 案 B: doc sync のみ
- **概要**: TECHNICAL_SPECIFICATION + DB_MIGRATION_GUIDE のみ更新
- **却下理由**: M9 audit 残課題が残る、本番稼働 entry が不在のままになる

## Related (関連)

- 関連 ADR: ADR-0007 / 0008 / 0009 / 0010
- 関連 Runbook: [RB-008](../runbooks/RB-008-data-db-corruption.md) (バックテスト復旧検証)
- Audit: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)
- Taskboard: [taskboard.md](../../taskboard.md)

## Notes

### Phase 8a 完了の Definition of Done
- `runner/main_trade.py` + `runner/backtest.py` + `runner/__init__.py` が schema.py と整合動作
- `test_runner.py` で 11 件以上のテスト PASS
- 全 ADR/taskboard/audit から「08:30 vs 09:00」の誤記述が除去されている
- `TECHNICAL_SPECIFICATION.md §6` に 13 テーブル + bucket KS 列が反映済
- `DB_MIGRATION_GUIDE.md §6` に Phase 6/7 累積 migration が文書化
- pytest 全件 PASS (202/202)
- 3 subagents 全件 PASS (Med/Low 修正済)