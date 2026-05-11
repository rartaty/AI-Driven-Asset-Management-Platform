# データベース移行マニュアル (SQLite → PostgreSQL) — 将来オプション

> **本PJの現行 DB は SQLite で確定**しています (個人ローカル運用前提・無料・無停止バックアップ可能)。
> 本ドキュメントは将来 **クラウド化 / マルチマシン同期 / 大規模並列書き込み** 等の理由で PostgreSQL への移行が必要になった場合の手順を記録するものです。
>
> 関連決定: [taskboard.md Decisions Log](../taskboard.md) / Memory `feedback_design_decisions.md` #4 (SQLite 継続容認) — 移行を実行する判断は別途 ADR で確定要。

---

## 0. 移行を判断するトリガー (どんな時に必要か)

以下のいずれかが現実になった時点で本ドキュメントの手順を実行検討:

- (a) 複数マシンから同時に DB へアクセスしたい (SQLite はファイルロック制約あり)
- (b) クラウド (AWS RDS / Aurora / Heroku Postgres 等) でホストする運用へ切替
- (c) 1秒間に数十件以上の同時書き込みが必要 (短期トレード戦略の高頻度化等)
- (d) JSONB 型 / フルテキスト検索 / partition 等の PostgreSQL 固有機能が必要
- (e) 数 GB を超える DB サイズで SQLite の性能限界に到達

逆に **以下の場合は PostgreSQL 移行を実行しない** こと:
- 単に「要件書に PostgreSQL と書いてあったから」という理由のみ
- データ量が数百 MB 以下で個人運用継続予定の場合 (SQLite で十分)

---

## 1. 移行手順 (4ステップ)

### Step 1. PostgreSQL サーバの準備
ローカル開発なら Docker / Podman:
```powershell
docker run -d --name projectbig-pg `
  -e POSTGRES_PASSWORD=mysecretpassword `
  -e POSTGRES_DB=bigtester_db `
  -p 5432:5432 `
  -v projectbig-pgdata:/var/lib/postgresql/data `
  postgres:15-alpine
```
クラウドなら AWS RDS / Aurora 等のマネージドサービスを使用。

### Step 2. `backend/.env` の `DATABASE_URL` を更新
**変更前 (SQLite — デフォルト)**
```env
# DATABASE_URL 未指定時は自動で sqlite:///./data.db が使われる
```

**変更後 (PostgreSQL の例)**
```env
# 書式: postgresql://[ユーザー名]:[パスワード]@[ホスト名]:[ポート]/[データベース名]
DATABASE_URL=postgresql://postgres:mysecretpassword@localhost:5432/bigtester_db
```
※ Docker Compose で起動する場合はホスト名を `db` (コンテナ名) などに変更。
※ パスワードは **平文埋め込み禁止** (CLAUDE.md 絶対禁止2)。AWS SSM 経由で取得し `os.getenv("DATABASE_URL")` で組み立て、または .env (gitignore済) 経由で読み込む。

### Step 3. PostgreSQL ドライバをインストール
```powershell
cd backend
.\venv\Scripts\Activate.ps1
pip install psycopg2-binary
```
※ 本番コンテナ環境では `Dockerfile` / `requirements.txt` に追記。

### Step 4. データ移行 (既存 SQLite データがある場合)
新規構築なら本ステップ不要。既存 `data.db` の内容を移行する場合:

#### 方法 A: SQLAlchemy で全テーブル INSERT (シンプル)
```python
# scripts/migrate_sqlite_to_pg.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.schema import Base

src = create_engine("sqlite:///./backend/src/data.db")
dst = create_engine(os.environ["DATABASE_URL"])

Base.metadata.create_all(dst)  # 空テーブル生成

SrcSession = sessionmaker(bind=src)
DstSession = sessionmaker(bind=dst)
src_s, dst_s = SrcSession(), DstSession()

for table in Base.metadata.sorted_tables:  # FK 順にソート
    for row in src_s.execute(table.select()).mappings():
        dst_s.execute(table.insert().values(**dict(row)))
dst_s.commit()
```

#### 方法 B: SQL ダンプ経由 (大規模データ用)
1. `sqlite3 data.db .dump > dump.sql`
2. PostgreSQL 互換 SQL に変換 (`pgloader` 等のツール使用)
3. `psql -d bigtester_db -f dump_pg.sql`

### Step 5. アプリケーション再起動
FastAPI 再起動。SQLAlchemy が自動的に PostgreSQL へ接続。`Base.metadata.create_all()` 等で必要テーブルを生成 (`User_Settings`, `Asset_Master` 等)。

---

## 2. 仕組み — なぜコード変更が不要なのか

本システムは **SQLAlchemy 2.0 ORM** を採用しているため、Python クラス (例: `class User_Settings(Base):`) として定義されたテーブル情報を、SQLAlchemy が「接続先のDB種別 (SQLite / PostgreSQL)」を自動判別し、各 DB 固有の SQL に翻訳して通信します。

そのため:
- ✅ `models/schema.py` のクラス定義は変更不要
- ✅ サービス層・戦略層の DB 操作コードも変更不要
- ⚠️ 例外: SQLite 固有の SQL (例: `INSERT OR REPLACE`) や PostgreSQL 固有機能 (JSONB / partition) を直書きしている箇所のみ要修正

---

## 3. 移行後のクリーンアップ

- (a) `backend/src/data.db` を `backups/data-pre-migration-{YYYYMMDD}.db` へ退避 (削除前に必ず保存)
- (b) `.gitignore` の SQLite 行 (`*.sqlite*`, `data.db`) は維持 (再度ローカル検証時に SQLite に戻せるよう)
- (c) `taskboard.md` Activity Log + ADR (該当 Decisions) に「PostgreSQL 移行完了」を記録
- (d) `requirements.txt` から `sqlite3` を削除する必要は **なし** (Python 標準ライブラリで自動同梱)

---

## 4. ロールバック (PostgreSQL → SQLite に戻す場合)

PostgreSQL から SQLite へ戻すには Step 2 の `DATABASE_URL` を空にする (or 削除)。データは Step 4 の逆方向 (PG → SQLite) を実行。本PJの個人ローカル運用フェーズなら、移行を急がず慎重判断推奨。

---

## 5. 関連ドキュメント
- 現行 DB 設計: [infrastructure/DATA_FOUNDATION.md §4](infrastructure/DATA_FOUNDATION.md) (SQLite 主要テーブル)
- 13テーブル新スキーマ: [TECHNICAL_SPECIFICATION.md §6](TECHNICAL_SPECIFICATION.md) (Phase 7 で Market_Ticks 追加)
- DB Bucket Isolation 設計: [ADR-0007](adr/0007-db-bucket-isolation-decisions.md) (PG 移行時の schema-per-bucket 構成)
- Tick Data Pipeline 設計: [ADR-0008](adr/0008-tick-data-pipeline-decisions.md) (Phase 8 で Market_Ticks → TimescaleDB hypertable 化)
- 移行実行時 ADR: `docs/adr/ADR-XXX-postgres-migration.md` (Phase 8 で新規作成)

---

## 6. 既存 SQLite データへの schema 追加変更履歴 (Phase 6/7)

新規テーブル追加 / 列追加が累積している。既存 `data.db` を継続使用する場合、起動時の `Base.metadata.create_all()` で自動追加されるが、**事前にバックアップを取った上で適用**することを推奨。

### 6.1 Phase 6 (ADR-0010 §6-1) — bucket 別キルスイッチ列追加
[backend/src/models/schema.py](../backend/src/models/schema.py) `User_Settings` に 4 列を追加:

```sql
ALTER TABLE user_settings ADD COLUMN is_kill_switch_active_passive BOOLEAN DEFAULT 0;
ALTER TABLE user_settings ADD COLUMN is_kill_switch_active_long_solid BOOLEAN DEFAULT 0;
ALTER TABLE user_settings ADD COLUMN is_kill_switch_active_long_growth BOOLEAN DEFAULT 0;
ALTER TABLE user_settings ADD COLUMN is_kill_switch_active_short BOOLEAN DEFAULT 0;
```

事前バックアップ:
```powershell
Copy-Item backend/src/data.db backups/data-pre-phase6.db
```

### 6.2 Phase 7 (ADR-0008/0009) — Market_Ticks テーブル新規追加
新規テーブル追加のみ。既存テーブルへの破壊的変更ゼロ。`Base.metadata.create_all()` で自動生成可能。

```sql
CREATE TABLE market_ticks (
    ticker_symbol VARCHAR NOT NULL,
    timestamp DATETIME NOT NULL,
    last_price BIGINT NOT NULL,
    cumulative_volume BIGINT NOT NULL,
    delta_volume BIGINT NOT NULL DEFAULT 0,
    bid_price BIGINT,
    ask_price BIGINT,
    side_inference VARCHAR,         -- TickSide enum: 'BUY_AGGR' / 'SELL_AGGR' / 'MID'
    is_synthetic BOOLEAN DEFAULT 0,
    push_count INTEGER DEFAULT 1,
    PRIMARY KEY (ticker_symbol, timestamp),
    FOREIGN KEY (ticker_symbol) REFERENCES asset_master (ticker_symbol)
);
CREATE INDEX ix_market_ticks_ticker_ts ON market_ticks (ticker_symbol, timestamp);
```

### 6.3 Phase 8 (PG 移行) で統合適用予定
Phase 8 着手時は schema-per-bucket 構成 ([ADR-0007](adr/0007-db-bucket-isolation-decisions.md)) と TimescaleDB hypertable 化 ([ADR-0008](adr/0008-tick-data-pipeline-decisions.md)) を一括適用予定。本 §6 の変更は SQLite 期間中の累積として記録。
