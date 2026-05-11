# ADR-0007: DB Bucket Isolation 設計決定 (OQ-1〜7 クローズ)

- **Status**: Accepted
- **Date**: 2026-05-09
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0001 (SQLite 一次採用), ADR-0002 (動的比率), ADR-0005 (Phase 4 案 B), ADR-0006 (Phase 5 案 Bridge)
- **Related Design Memo**: [DB_BUCKET_ISOLATION_DESIGN.md](../architecture/DB_BUCKET_ISOLATION_DESIGN.md)

---

## Context (背景)

2026-05-09 にユーザーから「証券口座の現金 / 投資信託 / 長期堅実積立 / 長期テンバガー狙い / 短期トレード の DB を別々で管理したい」という要望が出された。当初は **物理的 DB ファイル分離** が想定されていたが、議論の中で:

- 真の目的は「**bucket 単位の障害隔離** (投信が止まっても他は動く)」
- 物理分離は Profit Sweep の跨 bucket ACID を破壊するリスク大
- 障害シナリオ (DB 破損 / ロジックバグ / 外部 API 障害 / migration 障害) はそれぞれ最適な対策レイヤーが異なる
- PostgreSQL 化は本番要件 ([REQUIREMENTS_DEFINITION.md §3](../REQUIREMENTS_DEFINITION.md)) だが、個人スケールでは即時不要

の整理に到達。設計メモ ([DB_BUCKET_ISOLATION_DESIGN.md](../architecture/DB_BUCKET_ISOLATION_DESIGN.md)) で 4 トピック (A: ACID / B: 論理分離 / C: 障害隔離 / D: PG 移行) に分解し、7 つの Open Questions を立てた。Phase 5 完了時点でこれらをクローズし方向性を確定する。

## Decision (決定)

### OQ-1: PG 移行を Phase 4 と統合して前倒しするか?
**No — Phase 8 (Tick Data 本格化と統合) へ持ち越し**

- 個人スケール (単一ユーザー / 単一 PC / <1GB データ) で SQLite が詰まっていない
- Phase 4/5 のテスト 112/112 PASS が SQLite で動作することを実証済
- Phase 6 (SRE/Operations 基盤) は SQLite で完結可能
- Tick Data Pipeline ([ADR-0008](0008-tick-data-pipeline-decisions.md)) で TimescaleDB が必要になる Phase 8 で **DB 移行 + bucket schema 分離 + Tick hypertable 化** を一括実施する方が技術的負債が少ない

### OQ-2: PG 物理構成は schema 分離 / 複数 DB / 複数インスタンス?
**schema-per-bucket (B3)**

移行時の物理構成:
```
projectbig_db (single PostgreSQL instance)
├─ common schema     — User_Settings / Asset_Master / Daily_Price_History /
│                      Financial_Reports / Market_Context / Target_Portfolio /
│                      System_Logs / Report_Archive / Cash_Pool_Status /
│                      Daily_Asset_Snapshot / Market_Ticks (Tick Data)
├─ passive schema    — Trade_Logs (filter: Passive)
├─ long_solid schema — Trade_Logs (filter: Long_Solid)
├─ long_growth schema — Trade_Logs (filter: Long_Growth)
└─ short schema      — Trade_Logs (filter: Short) + Trade_Tick_Log
```

- 跨 schema トランザクションで Profit Sweep の真 ACID 達成
- `pg_dump --schema=passive` で bucket 別バックアップ可能
- 複数インスタンスは要件 §A.2 (HW 冗長化対象外) と矛盾しオーバースペック

### OQ-3: アプリ層障害隔離は単一プロセス + try/except (C1) で十分?
**Yes — 既に Phase 4/5 で稼働中**

- [services/scheduler.py](../../backend/src/services/scheduler.py) の各 job (job_check_kill_switch / job_morning_sync / job_sync_portfolio / job_short_term_trade / job_profit_sweep / job_generate_ai_report / job_weekly_rebalance / job_quarterly_review) は try/except で囲まれており、片方の失敗が他に伝播しない設計
- [services/market_context.py](../../backend/src/services/market_context.py) も VIX gear 発火失敗時に Target_Portfolio 更新のみスキップ可能
- Podman `restart: always` ポリシー (要件 §A.1) でプロセスクラッシュは自動再開で吸収
- 複数プロセス化 (C4/C5) は個人ローカルでオーバースペック

### OQ-4: bucket 別キルスイッチを `User_Settings` に追加するか?
**Yes — Phase 6 で追加**

`User_Settings` テーブルに以下の列を追加:
```python
is_kill_switch_active = Column(Boolean, default=False)              # 既存 (全体)
is_kill_switch_active_passive = Column(Boolean, default=False)      # 新規
is_kill_switch_active_long_solid = Column(Boolean, default=False)   # 新規
is_kill_switch_active_long_growth = Column(Boolean, default=False)  # 新規
is_kill_switch_active_short = Column(Boolean, default=False)        # 新規
```

`assert_inactive_for_entry` 関数を `bucket` 引数対応に拡張:
- 全体フラグまたは bucket 別フラグの **OR** で発注ブロック
- CLAUDE.md 絶対禁止 3 (キルスイッチ無断解除禁止) は **全フラグに適用**
- migration: SQLite では ALTER TABLE ADD COLUMN で軽微対応可能

実装は Phase 6 (SRE/Operations 基盤) の最初のサブタスクとして着手予定。

### OQ-5: cash は独立 bucket とせず `Cash_Pool_Status` 統合で OK?
**Yes — 統合維持**

- 証券口座キャッシュは値1個 (`brokerage_cash`) で表現可能、独立テーブル不要
- 既に [Cash_Pool_Status](../../backend/src/models/schema.py#L162-L172) に銀行残高 (`bank_balance`) と統合管理済
- Phase 5 P11 で portfolio_sync.py が `buying_power` フィールドで適切に扱うよう改修完了
- 独立テーブル化しても fault isolation 効果ゼロ

### OQ-6: F1 (DB 破損) 対策はバックアップ運用で十分か?
**Yes — 要件 §C.1 既定の運用で十分**

| 対策 | 頻度 | 復旧粒度 | 実装 |
| --- | --- | --- | --- |
| `data.db` 物理コピー | 日次 14:55 (取引時間後) | 全 bucket (1日前まで) | scripts/backup_db.py 新規 (Phase 6) |
| bucket 別 JSON 論理 dump | 週次 (金 16:00 など) | bucket 単位の論理復旧可能 | scripts/export_buckets.py 新規 (Phase 6) |
| 世代管理 | 30日分保持 | RPO 1日 | rotate ロジック内蔵 |

物理 DB 分離を採用しても、SQLite 単一プロセス内のクラッシュで全 bucket 道連れになる可能性は変わらない。アプリ層 (OQ-3) + バックアップ運用で必要十分。

### OQ-7: 本件の Decisions Log 移管先は taskboard.md か harness-taskboard.md か?
**taskboard.md を新規作成**

- CLAUDE.md / [.claude/behaviors.md S0](../../.claude/behaviors.md) / [backend/AGENTS.md](../../backend/AGENTS.md) が `taskboard.md` を参照しているが **未作成だった** (本セッションで作成済)
- ハーネス層 (Claudeをどう動かすか) は `.claude/harness-taskboard.md`、プロダクト層 (DB / 設計 / 実装) は `taskboard.md` で分離
- 本 Decisions Log は taskboard.md Decisions Log セクションへ転記

## Consequences (結果)

### ✅ Positive
- SQLite 維持により Phase 6 (SRE/Operations) を低い前提条件で開始可能
- bucket 別キルスイッチ追加で「片方が止まっても他は動く」を **アプリ層で実現**
- PG 移行を Tick Data 本格化 (Phase 8) と統合することで、移行の意義が明確
- Phase 5 で完成した動的ポートフォリオ運用が壊れない
- `taskboard.md` 新規作成で CLAUDE.md / behaviors.md の参照整合性が回復

### ⚠️ Negative / Trade-off
- **PG 移行のメリット (跨 schema ACID / 並列書込) は Phase 8 まで保留**
- bucket 別キルスイッチの schema migration が Phase 6 のサブタスクとして増える (軽微)
- Tick Data 規模が想定より早く拡大した場合、Phase 8 を前倒しする必要

### 🔄 Required Follow-up
- ✅ 本 ADR を起草・Status=Accepted
- ✅ 設計メモ ([DB_BUCKET_ISOLATION_DESIGN.md](../architecture/DB_BUCKET_ISOLATION_DESIGN.md)) を Decided 状態に更新
- ✅ taskboard.md 新規作成 + Decisions Log への転記
- 📋 Phase 6 (SRE/Operations 基盤) の ADR-0009 起草時に bucket 別キルスイッチ追加を含める
- 📋 Phase 8 (PG 移行) 着手時に schema-per-bucket 構成と Profit Sweep 跨 schema トランザクション設計を本 ADR から発展させる

## Alternatives Considered (検討した代替案)

### 案 A: SQLite ファイル分離 (5 ファイル)
- **概要**: 当初ユーザー要望ベースの物理分離
- **却下理由**: SQLite は跨ファイル ACID なし → Profit Sweep 中の資金消失リスク。要件 §6 fail-safe 違反

### 案 B: PG 即時移行 (Phase 4 と統合)
- **概要**: 本番要件に即合わせる
- **却下理由**: 個人スケールで SQLite が詰まっていない。Phase 4 スコープ膨張・テスト全面再構築の負荷大

### 案 C: マイクロサービス (5 コンテナ)
- **概要**: process-level isolation で完全分離
- **却下理由**: 要件 §A.2 (HW 冗長化対象外 / 個人ローカル運用) と矛盾しオーバースペック

### 案 D: PG 複数インスタンス (5 PG プロセス)
- **概要**: process-level isolation を PG レベルで実現
- **却下理由**: ローカル PC で 5 インスタンス起動はリソース過剰。2PC が必要となり運用罠多い

## Related (関連)

- 設計メモ: [DB_BUCKET_ISOLATION_DESIGN.md](../architecture/DB_BUCKET_ISOLATION_DESIGN.md)
- Tick Data 設計: [ADR-0008](0008-tick-data-pipeline-decisions.md), [TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md)
- 既存 DB 選定: [ADR-0001](0001-sqlite-as-primary-db.md)
- Phase 4/5 完了状況: [ADR-0005](0005-phase4-scope-standard.md), [ADR-0006](0006-phase5-scope-bridge.md)
- Audit: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)
- Taskboard: [taskboard.md](../../taskboard.md)

## Notes

### Phase 8 (PG 移行) 着手時のチェックリスト
- [ ] `db-schema-reviewer` subagent で schema-per-bucket 案レビュー
- [ ] `docs/DB_MIGRATION_GUIDE.md` を SQLite → PG (schema-per-bucket) に更新
- [ ] Profit Sweep を跨 schema トランザクションに書き換え
- [ ] `paper_trader.py` / `portfolio_sync.py` の bucket 集計ロジック維持確認 (Asset_Master.category 駆動)
- [ ] Tick Data の TimescaleDB hypertable 化 (ADR-0008 と同期)
- [ ] テスト全件 PASS + secrets-scanner / paper-trade-validator / discord-notify-validator
