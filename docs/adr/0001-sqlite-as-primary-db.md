# ADR-0001: SQLite を本番 DB として採用 (PostgreSQL は将来オプション)

- **Status**: Accepted
- **Date**: 2026-05-03
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: なし (起点 ADR)

---

## Context (背景)

要件書群 (REQUIREMENTS.md / REQUIREMENTS_DEFINITION.md / TECHNICAL_SPECIFICATION.md) は当初 **PostgreSQL 15** を本番 DB として指定していた。一方で、本PJは個人 PC 上で稼働するソロ運用システムであり、実装は SQLAlchemy 2.0 ORM 経由で **SQLite (`backend/src/data.db`)** で稼働中 (Phase 2 完了時点で 12 テーブル全て移行済み)。

要件と実装の乖離が長期化していたが、コスト・運用負荷・将来性の観点で再評価したところ:
- 個人ローカル運用では SQLite の制約 (同時書き込み・サイズ) が問題にならない
- PostgreSQL を強行すると Docker / Podman / バックアップ運用 / パッチ適用負荷が無駄に増える
- SQLAlchemy ORM 経由なので将来 PostgreSQL に移行する場合のコスト変更も低い (DATABASE_URL 1行差替え)

判断を保留すると、Phase 4 (`core/` 本実装) で各モジュールがどちらの DB を前提にするかが曖昧になり、コードレビューの度に議論が再燃する。

## Decision (決定)

**本PJ の本番 DB は SQLite (`backend/src/data.db`) で確定する。** PostgreSQL 15 は「将来クラウド化・マルチマシン同期・大規模並列書込が必要になった場合のオプション」として扱う。

### 決定の骨子
- backend/src/data.db を正式 DB と位置付ける
- 全要件文書 (6 ファイル・15 箇所) で PostgreSQL 言及を SQLite ベースに書き換え (R-1 完了)
- DB_MIGRATION_GUIDE.md は「将来移行オプション」として役割反転
- SQLAlchemy 2.0 Mapped Style で書き、PostgreSQL 互換 SQL を生成し続ける (移行容易性は維持)
- ファイルベースのため `data.db` は `.gitignore` 必須 (個人取引データ含むため)

### Why
- 個人ローカル運用ではコスト・運用負荷の観点で SQLite が圧倒的優位
- PostgreSQL 移行を強行する明確なトリガー (クラウド化等) が現時点でない
- ORM 経由のため将来移行は容易・覆しやすい決定

## Consequences (結果)

### ✅ Positive
- 月額コスト $0 (PostgreSQL コンテナ・RDS 等の運用費なし)
- バックアップ = ファイルコピー (停止不要・無停止スナップショット可)
- Python 標準ライブラリ `sqlite3` で動作 (追加依存ゼロ)
- 開発速度向上 (Docker 起動なしでテスト可)

### ⚠️ Negative / Trade-off
- 同時書き込み制約 (1 プロセスのみ書き込み可) — ソロ運用では問題にならない
- 大規模データ (>数 GB) で性能劣化 — 個人運用では到達困難
- JSONB 等の PostgreSQL 固有機能が使えない — 必要になったら移行検討

### 🔄 Required Follow-up
- ✅ 完了: 要件書 6 ファイル・15 箇所の SQLite 反映 (R-1, 2026-05-03)
- 📋 Phase 4: SQLite ファイルバックアップジョブを APScheduler に追加
- 📋 別タスク: `data.db` を OneDrive 同期から除外 (個人取引データ漏洩防止)

## Alternatives Considered (検討した代替案)

### 案 X: PostgreSQL 15 (要件書通り)
- **概要**: Docker/Podman で PostgreSQL コンテナを立てて常駐運用
- **却下理由**: 個人ローカル運用に対してオーバースペック・運用負荷増・コスト増。利点 (同時書込・大規模性能) が現フェーズで不要

### 案 Y: SQLite を継続使用しつつ要件書は PostgreSQL のまま放置
- **概要**: 「乖離は将来的に解消」のスタンス維持
- **却下理由**: 要件と実装の乖離が判断基準を曖昧化させ、コードレビュー・新規実装時に毎回判断を迫られる。Phase 4 着手前にこの曖昧さを解消したい

### 案 Z: DuckDB (SQLite 系の分析特化 DB)
- **概要**: 列指向の OLAP 用途で高速
- **却下理由**: トランザクション中心の本PJ用途では SQLite で十分・実績豊富

## Related (関連)

- ✅ DB_MIGRATION_GUIDE.md: [docs/DB_MIGRATION_GUIDE.md](../DB_MIGRATION_GUIDE.md) (将来移行オプション)
- ✅ 監査スナップショット: [docs/audit/2026-05-03-pre-phase4.md](../audit/2026-05-03-pre-phase4.md)
- 関連 Memory: `~/.claude/projects/.../memory/feedback_design_decisions.md` #4 (SQLite 継続容認)
- 関連 Activity Log: [taskboard.md](../../taskboard.md) 2026-05-03 R-1 行

## Notes

将来 PostgreSQL 移行を実行する判断トリガー (再掲): `DB_MIGRATION_GUIDE.md §0` 参照。
- (a) 複数マシン同時アクセス
- (b) クラウド (RDS / Aurora) 採用
- (c) 1秒間に数十件以上の同時書込
- (d) JSONB / 全文検索 / partition 必要
- (e) DB 数 GB 超
