# ADR-0010: Phase 6 スコープ = SRE/Operations 基盤 (個人ローカル運用版)

- **Status**: Completed (2026-05-09 実装完了 + DoD 検証完了)
- **Date**: 2026-05-09 (採択 + 同日完了)
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0006 (Phase 5 案 Bridge), ADR-0007 (DB Bucket Isolation), ADR-0008 (Tick Data 設計決定), ADR-0009 (Phase 7 案 Tick Data)

## DoD 検証結果 (2026-05-09)

| # | DoD 項目 | 結果 |
| --- | --- | --- |
| 1 | bucket 別キルスイッチ列追加 + assert_inactive_for_entry(bucket) 動作 | ✅ schema 4 列追加 + activate_bucket / deactivate_bucket / 15 件テスト PASS |
| 2 | bucket 別キルスイッチのテスト追加 | ✅ test_kill_switch_bucket.py 15 件 |
| 3 | scripts/backup_db.py 実行可能 + 30 日 rotate 動作 | ✅ test_backup_export.py 7 件 PASS |
| 4 | scripts/export_buckets.py bucket 別 JSON dump | ✅ test_backup_export.py 6 件 PASS |
| 5 | scheduler.py 新規ジョブ 2 件 (job_backup_daily / job_export_buckets_weekly) | ✅ 4 件テスト追加 + Discord 通知整合 |
| 6 | docs/operations/ 4 ファイル | ✅ INCIDENT_RESPONSE / SLI_SLO / OBSERVABILITY_STACK / POSTMORTEM_TEMPLATE |
| 7 | docs/runbooks/ 10 件 + README | ✅ RB-001〜010 全件 + index |
| 8 | pytest 全件 PASS | ✅ 191/191 PASS (Phase 5 完了時 159 → +32 件 = bucket KS 15 + backup/export 13 + scheduler 4) |
| 9 | 4 subagents PASS (db-schema / secrets / paper-trade / discord-notify) | ✅ 全件 PASS (Med 1 件 → 対応済 / Low 数件 → 後続対応へ) |
| 10 | 本 ADR Status=Completed | ✅ 本ファイル更新 |
| 11 | audit doc / taskboard.md Activity Log 追記 | ✅ |

**Subagent 指摘の対応**:
- paper-trade-validator (Med): job_backup_daily / job_export_buckets_weekly のテスト追加 ([test_scheduler.py](../../backend/tests/test_scheduler.py) +4 件)
- db-schema-reviewer (Doc sync, 後続対応): TECHNICAL_SPECIFICATION.md / DB_MIGRATION_GUIDE.md の bucket 別 KS 列追記は Phase 8 PG 移行作業時にまとめて実施
- paper-trade-validator (Low): scripts/ パス解決の脆弱性 / WAL モード並行実行 → Phase 8 で再評価

**残タスク (Phase 8 持ち越し)**:
- M9 runner/main_trade.py / backtest.py
- M11 Frontend 残機能 (多角的時間軸 / イントラデイ / 銘柄照会 / AI タイムライン)
- TimescaleDB hypertable 移行 (ADR-0007 Phase 8)
- REAL モード実発注パス (kabucom.place_order 本実装)
- TECHNICAL_SPECIFICATION.md / DB_MIGRATION_GUIDE.md の Phase 6/7 反映 (doc sync)
- ~~既存 issue: morning_sync 08:30 vs 仕様 09:00~~ → **誤記述だった** (REQUIREMENTS_DEFINITION.md §7 「朝8:30」が要件)。Phase 8a で訂正

---

## Context (背景)

Phase 4 (core/ 本実装) → Phase 5 (Operations 隙間埋め) → Phase 7 (Tick Data 実装) と進み、プロダクト本体の機能はほぼ完成。残るは **運用・障害対応の体系化** と **長期持ち越し項目 (M9 runner / M11 Frontend)**。

ハーネスタスクボード ([.claude/harness-taskboard.md](../../.claude/harness-taskboard.md)) の OPS-1/2 で「Phase 6 着手時にユーザー持参ドラフト + Claude 提案を共同レビューしてスコープ確定」と記録済。本セッションで以下も追加起票:
- ADR-0007 OQ-4: bucket 別キルスイッチを Phase 6 で追加
- IR-1: 障害時対応プロトコル (なんのために何を順番にチェックするか)

「フル SRE ではなく個人ローカル運用版」という制約 (harness-taskboard.md OPS-2 既定) で進める。Prometheus/Jaeger 等の本格観測スタックは採用せず、既存の Discord + System_Logs DB + Frontend で完結させる。

## Decision (決定)

**Phase 6 のスコープを SRE/Operations 基盤 (個人ローカル運用版) で確定**。期間目安 2〜3 日。

### スコープ詳細

#### 6-1. bucket 別キルスイッチ追加 (ADR-0007 OQ-4 / コード)
- [User_Settings](../../backend/src/models/schema.py) に列追加:
  - `is_kill_switch_active_passive` / `_long_solid` / `_long_growth` / `_short` (Boolean default=False)
  - 既存 `is_kill_switch_active` (全体) は維持
- [core/kill_switch.py](../../backend/src/core/kill_switch.py) に `assert_inactive_for_entry(session, bucket=None)` の拡張:
  - bucket 引数なしの場合: 全体フラグのみチェック (既存挙動維持)
  - bucket 引数ありの場合: 全体フラグ ∨ bucket 別フラグの OR
- CLAUDE.md 絶対禁止 3 (キルスイッチ無断解除禁止) は **全フラグに適用**
- migration: SQLite で軽微 (ALTER TABLE ADD COLUMN × 4)
- テスト追加: bucket 別 ON/OFF / 全体 ON で全 bucket ブロック / 解除確認

#### 6-2. バックアップ運用 (ADR-0007 OQ-6 / コード)
- `scripts/backup_db.py` 新規:
  - 日次 14:55 (取引時間後) に `data.db` を `backups/data-YYYYMMDD.db` へコピー
  - 30 日分世代管理 (古いものは自動 rotate)
  - 物理コピー単位なので bucket 単位の論理復旧は次項で別途
- `scripts/export_buckets.py` 新規:
  - 週次 (金 16:00) に bucket 別 JSON 論理 dump
  - 出力: `backups/buckets/YYYYMMDD/{passive|long_solid|long_growth|short}.json`
- scheduler.py に新規ジョブ追加 (`job_backup_daily` / `job_export_buckets_weekly`)
- テスト追加: backup_db.py / export_buckets.py の動作 + rotate 動作

#### 6-3. IR-1 障害対応プロトコル (ドキュメント)
- `docs/operations/INCIDENT_RESPONSE.md` 新規:
  - 「何のために何を順番にチェックするか」を体系化
  - 障害種別 (DB 破損 / API 障害 / ロジックバグ / 通信途絶) ごとの 5W1H チェックリスト
  - "First Hour Response" — 最初の 1 時間で確認すべき項目順
  - Discord 通知から推定可能な障害種別の対応フロー
  - Kill Switch 発火時の **解除前チェックリスト** (CLAUDE.md 絶対禁止 3 整合)
  - ロールバック判断基準

#### 6-4. SLI/SLO 定義 (ドキュメント)
- `docs/operations/SLI_SLO.md` 新規:
  - SLI (Service Level Indicators) — 何を測るか
    - 短期判定レイテンシ (ms 級 / 要件 §B.2)
    - kabu API 接続成功率
    - Profit Sweep 完了率 (毎日 14:50 に完遂したか)
    - Daily_Asset_Snapshot 永続化成功率
  - SLO (Service Level Objectives) — どのレベルを保証するか
    - 個人運用なので商用クラスは無理だが、最低限の基準を明文化
  - 違反時のアクション (アラート閾値 + Runbook へリンク)

#### 6-5. Runbook 10 件 (ドキュメント)
- `docs/runbooks/` ディレクトリ新規 + 以下 10 件:
  - RB-001: Kill Switch が発火した
  - RB-002: kabu Station API が応答しない
  - RB-003: AWS SSM 認証失敗
  - RB-004: Discord 通知が届かない
  - RB-005: Daily_Asset_Snapshot 書込失敗
  - RB-006: Profit Sweep が時刻通りに完了しない
  - RB-007: Market_Ticks の delta_volume 異常検知
  - RB-008: data.db の破損疑い
  - RB-009: VIX gear が誤発火している
  - RB-010: WebSocket 切断ループ (再接続失敗連発)
- 各 Runbook テンプレ: 症状 / 影響 / 確認手順 / 解消手順 / エスカレーション

#### 6-6. 観測性スタック (ドキュメント)
- `docs/operations/OBSERVABILITY_STACK.md` 新規:
  - 個人ローカル運用版の観測手段 (Prometheus/Jaeger 採用せず):
    - **ログ**: python-json-logger による構造化ログ + System_Logs テーブル DB 永続化 (Phase 4 完了済)
    - **メトリクス**: Daily_Asset_Snapshot / Trade_Tick_Log / Market_Ticks の DB クエリで代替
    - **アラート**: Discord (notify_critical / notify_system / notify_trade) 既存配線 (Phase 4/5/7 完了済)
    - **ダッシュボード**: Frontend dashboard + reports ページが代替 (M11 残機能で拡張可)
  - 商用観測スタックを採用しない理由 (要件 §A/§B 適正化方針整合)
  - 将来 (チーム化 / クラウド移行時) のスケールアップ提案

#### 6-7. ポストモーテムテンプレート (ドキュメント)
- `docs/operations/POSTMORTEM_TEMPLATE.md` 新規:
  - 5 Whys テンプレ
  - 障害発生時刻 / 影響範囲 / 初動 / 根本原因 / 再発防止
  - 個人運用なので簡素化 (フル SRE のような Blameless 等は対象外)

**含まない (Phase 8 以降に持ち越し)**:
- M9 runner/main_trade.py / backtest.py
- M11 Frontend 残機能 (多角的時間軸 / イントラデイ / 銘柄照会 / AI タイムライン)
- TimescaleDB hypertable 移行 (ADR-0007 + ADR-0008 統合計画)
- REAL モード実発注パス (kabucom.place_order 本実装)
- 商用観測スタック (Prometheus / Grafana / Jaeger) — 個人運用では不採用維持
- git 化 + SEC-7 (gitleaks) — Git for Windows 未インストール状態のため別途

### Why
- bucket 別キルスイッチで Phase 7 で機能化した短期戦略のリスクが個別管理可能になる
- IR-1 (障害対応プロトコル) はユーザー本セッションで具体的に要望された項目
- Runbook 整備で「障害時に頭で考える時間ゼロ」を達成 (要件 §C 運用保守自動化方針整合)
- SLI/SLO で「何が壊れたら直すべきか」の判断軸を明文化
- 観測性スタックドキュメントで「商用スタック不採用の根拠」を残す (将来のスケール時に判断材料)

## Consequences (結果)

### ✅ Positive
- bucket 単位で短期戦略を一時停止可能になり、Phase 7 で導入した自動発注のリスクが個別管理化
- 障害時に「何を確認すべきか」が手順書化 → 復旧時間 (MTTR) 短縮
- 個人運用フェーズで作っておけば、将来チーム化時の引き継ぎ資料になる
- Postmortem テンプレで失敗から学ぶサイクルを定型化

### ⚠️ Negative / Trade-off
- **期間 2〜3 日想定**: ドキュメント比重が大きい (Runbook 10 件 + SLI/SLO + IR + Postmortem + Observability)
- 個人ローカル運用版なので商用 SRE 標準とは隔たりあり (将来チーム化時に再整備必要)
- Runbook の質はメンテし続けないと劣化する (実際の障害経験を反映する継続的活動が必要)

### 🔄 Required Follow-up
- ✅ 着手前: Phase 4/5/7 完了 + 設計議論クローズ
- ✅ 着手前: ADR-0007 OQ-4 (bucket 別キルスイッチ Phase 6 採用) + IR-1 起票
- 📋 着手中: 6-1 (コード) → 6-2 (バックアップスクリプト) → 6-3〜6-7 (ドキュメント順次)
- 📋 完了後: pytest 全件 PASS + Phase 6 範囲のテスト追加
- 📋 完了後: secrets-scanner / paper-trade-validator / discord-notify-validator 全件 PASS
- 📋 完了後: ADR-0011 (Phase 8 = M9 runner / M11 Frontend / PG 移行) 起草

## Alternatives Considered (検討した代替案)

### 案 A: フル SRE 採用 (Prometheus / Grafana / Jaeger)
- **概要**: 商用クラスの観測スタック導入
- **却下理由**: 個人運用ではオーバースペック。要件 §A/§B 適正化方針と矛盾。Discord + DB + Frontend で個人運用は十分

### 案 B: コードのみ (bucket switch + backup) で Operations docs はスキップ
- **概要**: 6-1, 6-2 だけ実装し 6-3〜6-7 は将来送り
- **却下理由**: IR-1 (ユーザー要望) を解決できない。障害対応プロトコルなしでは Phase 7 で導入した自動発注が運用負荷を増やす

### 案 C: ドキュメントのみ (コード変更なし) で 6-1, 6-2 を Phase 8 へ
- **概要**: bucket switch / backup を Phase 8 に持ち越し、Phase 6 は docs 専念
- **却下理由**: bucket 別キルスイッチは ADR-0007 で Phase 6 採用と既決定。順序逆転は ADR 整合性を崩す

### 案 D: Phase 6 を 6a (コード) + 6b (ドキュメント) に分割
- **概要**: コードを先に実装、ドキュメントは別フェーズ
- **却下理由**: 採用可だが、IR-1 等のドキュメントはユーザーの強い要望のため一括完了が望ましい。分割すると追跡負担が増える

## Related (関連)

- 設計メモ: [DB_BUCKET_ISOLATION_DESIGN.md](../architecture/DB_BUCKET_ISOLATION_DESIGN.md) §12 (Phase 6 でのフォローアップ)
- DB Bucket 決定: [ADR-0007](0007-db-bucket-isolation-decisions.md) OQ-4 / OQ-6
- Phase 5 完了: [ADR-0006](0006-phase5-scope-bridge.md)
- Phase 7 完了: [ADR-0009](0009-phase7-scope-tick-data.md)
- ハーネスタスクボード: [.claude/harness-taskboard.md](../../.claude/harness-taskboard.md) OPS-1/OPS-2
- Audit: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)
- Taskboard: [taskboard.md](../../taskboard.md)

## Notes

### Phase 6 推奨実装順序 (依存関係順)

1. **6-1 bucket 別キルスイッチ** — schema.py + kill_switch.py + テスト追加 (db-schema-reviewer 監査)
2. **6-2 バックアップスクリプト** — scripts/backup_db.py + scripts/export_buckets.py + scheduler 配線 + テスト
3. **6-3 IR-1 障害対応プロトコル** — docs/operations/INCIDENT_RESPONSE.md
4. **6-4 SLI/SLO** — docs/operations/SLI_SLO.md
5. **6-5 Runbook 10 件** — docs/runbooks/RB-001〜010
6. **6-6 観測性スタック** — docs/operations/OBSERVABILITY_STACK.md
7. **6-7 Postmortem テンプレ** — docs/operations/POSTMORTEM_TEMPLATE.md
8. 統合テスト + 3 subagents 検証
9. ADR Status=Completed

### Phase 6 完了の Definition of Done
- User_Settings に bucket 別 kill_switch 列が追加されており、assert_inactive_for_entry が bucket 引数で動作
- bucket 別キルスイッチのテスト追加 (新規 4-6 件)
- scripts/backup_db.py が実行可能で 30 日 rotate 動作確認
- scripts/export_buckets.py が bucket 別 JSON dump を出力
- scheduler.py に新規ジョブ 2 件 (job_backup_daily / job_export_buckets_weekly) が登録
- docs/operations/ 配下に IR / SLI/SLO / Observability / Postmortem の 4 ファイル
- docs/runbooks/ 配下に RB-001〜010 の 10 ファイル
- pytest 全件 PASS (現 159 + Phase 6 範囲)
- secrets-scanner / paper-trade-validator / discord-notify-validator / db-schema-reviewer 全件 PASS
- 本 ADR Status=Completed
- audit doc / taskboard.md Activity Log 追記
