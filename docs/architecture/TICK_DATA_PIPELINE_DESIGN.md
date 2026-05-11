# Tick Data (歩み値) パイプライン設計メモ

> **Status**: **Decided** (2026-05-09 設計議論クローズ)
> **Scope**: kabu Station Push API から歩み値 (時刻・価格・出来高) をリアルタイム再構築し、短期トレード (VWAP / Z スコア / レジーム判定) で利用するためのデータ層設計
> **Scale assumption**: **個人開発・単一 PC ローカル運用** ([REQUIREMENTS_DEFINITION.md §A/§B/§F](../REQUIREMENTS_DEFINITION.md) の適正化方針に準拠)
> **決定**: 採択された決定は本ファイル末尾「§9 Decisions (確定)」と [ADR-0008](../adr/0008-tick-data-pipeline-decisions.md) を参照。
> **関連**: [DB_BUCKET_ISOLATION_DESIGN.md](DB_BUCKET_ISOLATION_DESIGN.md) (Market_Ticks 配置先は common 確定)

---

## 1. 背景と問題意識

### 1.1 短期トレードロジックが要求するデータ
[backend/src/strategy/vwap_short.py](../../backend/src/strategy/vwap_short.py) 系の戦略は以下を必要とする:

- VWAP (Volume Weighted Average Price) — 累積 (price × volume) / 累積 volume
- VWAP からの Z スコア (乖離率の標準偏差正規化)
- レジーム判定 (トレンド・デイ vs 平均回帰・デイ) — 出来高プロファイル + 価格モメンタムから推定
- (将来) オーダーブック・インバランス — 板厚さ + 約定方向

これらは全て **約定単位の連続データ (歩み値)** に依存する。日足 OHLCV だけでは精度不足。

### 1.2 kabu Station API の制約

kabu Push API が直接的な「Time & Sales (歩み値)」フィードを提供しない。代わりに以下を Push:
- `CurrentPrice` (現在値)
- `TradingVolume` (当日累積出来高)
- `BidPrice` / `AskPrice`
- `CalcPrice` (計算用基準価格)

**結論**: Push 受信を 1 秒等の bucket に集約し、**疑似 tape を再構築する** 方式が必要。

### 1.3 再構築方針

```
Push 受信 (~1-10 events/sec/銘柄)
   ↓
(timestamp, ticker, last_price, cumulative_volume, bid, ask)
   ↓ 派生計算
delta_volume   = cumulative_volume - prev_cumulative_volume
side_inference = price >= ask ? 'BUY_AGGR'
               : price <= bid ? 'SELL_AGGR'
               : 'MID'
   ↓
疑似 tick レコード (1 秒粒度 or push 粒度)
```

完全な約定単位ではないが、VWAP / Z スコア / レジーム判定の精度には十分。

---

## 2. データ規模見積もり (個人開発スケール)

### 2.1 想定パラメータ
- ユニバース: **50〜100 銘柄** (短期トレード対象、個人運用想定)
- Push 頻度: 平均 1〜3 events/sec/銘柄 (流動性銘柄は 10/sec 超もあり)
- 取引時間: 9:00-11:30 + 12:30-15:00 = 5h = 18,000 秒/日

### 2.2 ボリューム

| 単位 | サイズ |
| --- | --- |
| 1 push event (~50 bytes: ts + ticker + 4-5 数値) | 50 B |
| 1 銘柄日次 (avg 2 events/sec) | 18,000 × 2 × 50 B ≈ **1.8 MB** |
| 50 銘柄日次 | **約 90 MB** |
| 100 銘柄日次 | **約 180 MB** |
| 50 銘柄年次 (250 営業日) | **22 GB/年** |
| 100 銘柄年次 | **45 GB/年** |

### 2.3 結論

**TimescaleDB (PG 拡張) で完結するレンジ** であり、Iceberg 等の本格データレイク技術は不要。圧縮 (Timescale 列指向圧縮 90%+) を効かせれば 5 年保管しても **<25 GB**。

---

## 3. 推奨アーキテクチャ (3 層)

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 1: HOT (in-memory)        ─ ms 級レイテンシ              │
│  - Python collections.deque (maxlen=N)                       │
│  - リアルタイム VWAP / Z スコア / 側推定                       │
│  - 戦略判定 path (発注決定はここで完結)                         │
│  - 永続化しない (プロセス再起動で消える)                        │
└──────────────────────────────────────────────────────────────┘
                       ↓ 定期 flush (1-5 秒)
┌──────────────────────────────────────────────────────────────┐
│ Layer 2: WARM (TimescaleDB hypertable)                       │
│  - market_ticks テーブル                                     │
│  - 直近 7-30 日 (生データ) を保持                              │
│  - 当日復習・直近バックテストで利用                            │
│  - TimescaleDB Continuous Aggregates で 1分/5分/1時間 OHLCV   │
│    マテビュー化 (自動更新)                                     │
└──────────────────────────────────────────────────────────────┘
                       ↓ 自動移行 (Compression Policy)
┌──────────────────────────────────────────────────────────────┐
│ Layer 3: COLD (TimescaleDB compressed chunks)                │
│  - 7-30 日経過したパーティションを列指向圧縮                    │
│  - 90%+ 圧縮で年間数 GB に収まる                               │
│  - 過去バックテスト・特徴量抽出で利用 (秒級レイテンシで十分)     │
│  - Retention Policy で 5-10 年で自動 drop                    │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 各層のレイテンシ要件と SLA

| 層 | レイテンシ要件 | 失敗時の影響 |
| --- | --- | --- |
| Layer 1 | **ms 級** (ms 以内に判定) | 当日トレード判断停止 → 短期 bucket Kill Switch |
| Layer 2 | 秒級 | 永続化遅延のみ (Layer 1 が継続稼働するため売買は止まらない) |
| Layer 3 | 分級 | バックテスト遅延のみ (運用に直接影響なし) |

### 3.2 設計原則
- **Layer 1 は永続層をブロックしない** — 永続書込失敗で売買 path を止めない
- **Layer 2 への flush は非同期** — `asyncio.Queue` か別スレッドで集約・bulk insert
- **Layer 3 は TimescaleDB の自動運用機能に任せる** — 手動 ETL は書かない

---

## 4. スキーマ案

### 4.1 生 tick テーブル (Layer 2/3)

```python
# backend/src/models/schema.py に追加想定

class Market_Ticks(Base):
    """
    歩み値 (Time & Sales) の再構築データ
    kabu Station Push API の連続 push を 1 秒単位等で集約・派生計算
    TimescaleDB hypertable 化対象 (PG 移行後)
    """
    __tablename__ = "market_ticks"

    timestamp = Column(DateTime(timezone=True), primary_key=True)
    ticker_symbol = Column(String, ForeignKey("asset_master.ticker_symbol"), primary_key=True, index=True)

    last_price = Column(BigInteger, nullable=False)            # 円単位整数
    cumulative_volume = Column(BigInteger, nullable=False)     # 当日累積出来高
    delta_volume = Column(BigInteger, nullable=False)          # 前push との差分
    bid_price = Column(BigInteger, nullable=True)
    ask_price = Column(BigInteger, nullable=True)
    side_inference = Column(String, nullable=True)             # 'BUY_AGGR' / 'SELL_AGGR' / 'MID'

    __table_args__ = (
        Index("ix_market_ticks_ticker_ts", "ticker_symbol", "timestamp"),
    )
```

### 4.2 TimescaleDB hypertable 化 (PG 移行後)

```sql
-- backend/src/services/migrations/post_pg/001_market_ticks_hypertable.sql

SELECT create_hypertable(
    'market_ticks',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day'
);

-- 7 日経過したチャンクを自動圧縮
ALTER TABLE market_ticks SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker_symbol'
);

SELECT add_compression_policy('market_ticks', INTERVAL '7 days');

-- 5 年経過したチャンクを自動削除 (個人運用で十分)
SELECT add_retention_policy('market_ticks', INTERVAL '5 years');
```

### 4.3 Continuous Aggregate (1 分足 OHLCV)

```sql
CREATE MATERIALIZED VIEW market_ticks_1min
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', timestamp) AS bucket,
    ticker_symbol,
    first(last_price, timestamp) AS open,
    max(last_price) AS high,
    min(last_price) AS low,
    last(last_price, timestamp) AS close,
    sum(delta_volume) AS volume,
    sum(last_price::numeric * delta_volume) / NULLIF(sum(delta_volume), 0) AS vwap
FROM market_ticks
GROUP BY bucket, ticker_symbol
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'market_ticks_1min',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute'
);
```

5 分足・1 時間足も同様のパターンで作成。**バックテスト時の集計クエリが秒未満で返る**。

---

## 5. 実装段階 (個人開発に合わせた段階導入)

### Phase A: SQLite で動作する最小構成 (現フェーズで実装可能)
1. Push 受信 → in-memory deque (Layer 1)
2. 1 秒バッファリング → SQLite `market_ticks` に bulk insert (Layer 2)
3. 戦略は Layer 1 (in-memory) のみから読む
4. バックテスト・復習は SQLite SQL で十分

> SQLite だと TimescaleDB 機能 (hypertable / continuous aggregate / 圧縮) は使えない。代わりに **手動で日次 partition テーブル** (`market_ticks_2026_05_09`) を作るか、単一テーブル + index でしのぐ。年次 22-45 GB は SQLite でも動くが、index 性能が劣化するため Phase B で PG 移行が望ましい。

### Phase B: PostgreSQL + TimescaleDB 移行
1. [DB_MIGRATION_GUIDE.md](../DB_MIGRATION_GUIDE.md) に基づく PG 移行と同期
2. `market_ticks` を hypertable 化
3. Continuous Aggregate 群 (1分/5分/1時間) を作成
4. 圧縮ポリシー + retention 設定

### Phase C: 高度化 (将来オプション)
- Layer 1 を Redis Streams / Apache Kafka に置き換え (複数プロセス間で push 共有したい場合)
- Layer 3 を Parquet + DuckDB に書き出してバックテスト基盤と分離
- Iceberg 化 (チーム化 / クラウド移行 / 監査要件が浮上した時のみ)

---

## 6. 押さえるべき実装課題

### 6.1 Push 接続の信頼性
- WebSocket 切断検知 → 自動再接続 (exponential backoff)
- 切断中の欠損データ:
  - 復帰時に `cumulative_volume` の単調性チェック
  - gap 検出時は kabu REST API から OHLCV を fetch して埋める fallback
  - gap 中の決済予定 tick は **戦略判定に使わない** (mark as `is_synthetic=True`)

### 6.2 cardinality 設計
- `ticker_symbol` を hypertable の segmentby に指定 → 圧縮効率最大化
- ユニバース変更 (新規銘柄追加・削除) は `Asset_Master` 側で管理し、tick テーブルは銘柄数に依存しない設計

### 6.3 タイムスタンプ精度
- kabu Push の timestamp は秒精度の場合あり (要確認)
- 同一秒内の複数 push は (ticker, timestamp, sequence_no) 等で衝突回避
- 実装時に **PRIMARY KEY を (ticker, timestamp, seq) に拡張** する選択肢

### 6.4 PAPER モード時の代替データソース
[REQUIREMENTS_DEFINITION.md §3](../REQUIREMENTS_DEFINITION.md) の「PAPER は yfinance / REAL は kabu」原則に従い、PAPER モードでは:
- yfinance の 1 分足を疑似 tick として供給
- ボリュームは 1 分単位で按分 (1 分内一様分布と仮定)
- side_inference は `MID` 固定 (yfinance に bid/ask なし)

これにより戦略コードは **「データソースを意識せず Layer 1 から読むだけ」** で済む。

### 6.5 短期 bucket キルスイッチとの連携
- Layer 1 が空 (push 一度も受信していない) で発注しないチェック
- `delta_volume < 0` (累積出来高が減った) は異常 → 短期 bucket キルスイッチ作動
- `cumulative_volume` 巻き戻りは kabu 側のリセット (場前) なら正常、市場時間中なら異常

### 6.6 永続化失敗時の挙動
- Layer 2 (DB) flush 失敗 → ログ + Discord 通知のみ、Layer 1 は継続稼働
- DB 復旧後に Layer 1 のリングバッファ内容を再 flush
- リングバッファサイズ超過分は **断念** (永続より売買継続を優先)

---

## 7. 既存設計との整合

### 7.1 現状の `Trade_Tick_Log` テーブル

[schema.py](../../backend/src/models/schema.py) に既に存在する `Trade_Tick_Log` は以下:

```python
class Trade_Tick_Log(Base):
    """6.2 短期トレード イントラデイログ (1〜5分間隔)"""
    timestamp, ticker_symbol, unrealized_pnl, realized_pnl, regime_type
```

これは **自分のポジションの PnL 推移** を追うもので、**市場 tick (生データ)** とは別物。両者は混同せず:
- `Trade_Tick_Log` (既存) — 自ポジションの分単位 PnL
- `Market_Ticks` (新規) — 市場の歩み値再構築

### 7.2 Daily_Price_History との関係
日足 OHLCV ([Daily_Price_History](../../backend/src/models/schema.py)) は引き続き **EOD バッチで yfinance / kabu REST から取得**。Tick から再集計しても良いが、別ソースとしての validation 用に残す。

---

## 8. Open Questions — **全件 Closed (2026-05-09)**

| # | 質問 | Status | 決定 |
| --- | --- | --- | --- |
| TQ-1 | 短期ユニバースの初期サイズは何銘柄? | **Closed** | **50 銘柄初期 / 100〜200 銘柄まで段階拡張** (年間 22 GB 想定) |
| TQ-2 | Phase A (SQLite) で実装するか、PG 移行を待つか | **Closed** | **Phase A (SQLite + 日次 partition)** で先行実装 ([ADR-0007](../adr/0007-db-bucket-isolation-decisions.md) と整合) |
| TQ-3 | tick テーブルを `Market_Ticks` という命名で良いか | **Closed** | **Yes (`Market_Ticks`)** — 既存 `Trade_Tick_Log` (自ポジ PnL) と意味的に別物として明確に区別 |
| TQ-4 | PAPER モード時の yfinance → 疑似 tick 変換責任 | **Closed** | **データ層 (services/market_data.py 新規)** が担う。戦略は Layer 1 in-memory deque から読むだけ |
| TQ-5 | gap 検出時の fallback 強度 | **Closed** | **REST 補完 + `is_synthetic=True` フラグ**。synthetic tick は戦略判定材料に使わない (安全寄り) |
| TQ-6 | `Market_Ticks` を bucket 隔離対象とするか | **Closed** | **No (common 配置)** — 全 bucket が参照する銘柄市場データ。`Asset_Master` と同等の共有テーブル扱い |

---

## 9. 検討対象としなかった案

念のため記録 (将来の蒸し返し時の参照用)。

| 案 | 不採用理由 |
| --- | --- |
| **InfluxDB** | ACID なし、SQLAlchemy 非対応、ポリグロット運用負荷。TimescaleDB が上位互換 |
| **Apache Iceberg** | 個人スケール (年間数十 GB) には catalog 運用 / metadata 管理コストが過大。チーム化・クラウド化が現実味を帯びたら再検討 |
| **Parquet + DuckDB (cold layer のみ)** | TimescaleDB 内圧縮で同等効果。エンティティを増やしたくない |
| **Apache Kafka / Redis Streams** | 単一プロセス運用なら in-memory deque で十分。複数プロセス化したら再検討 |
| **専用 OLAP DB (ClickHouse 等)** | 個人運用でメンテ対象を増やす価値が薄い |

---

## 9.5 Decisions (確定)

### 9.5.1 短期 (Phase 7 で実装)
- **テーブル**: `Market_Ticks` を新規追加 ([schema.py](../../backend/src/models/schema.py) 拡張)
- **配置**: 共有テーブル (bucket 隔離対象外)。Asset_Master と同じ扱い
- **PK**: (timestamp, ticker_symbol) の複合キー (同一秒内の衝突は seq_no で拡張余地)
- **ストレージ**: SQLite + 日次 partition テーブル (`market_ticks_YYYY_MM_DD`) または単一テーブル + index (年次データ量で判断)
- **ユニバース**: 50 銘柄からスタート、運用で 100〜200 まで段階拡張可能
- **データ層責務**: `services/market_data.py` (新規) が以下を吸収:
  - kabu Push 受信 → 1 秒バケット集約 → Market_Ticks 永続化
  - PAPER モードの yfinance 1 分足 → 疑似 tick 変換 (volume 一様分布按分)
  - gap 検出 → REST API 補完 (`is_synthetic=True` フラグ付与)
- **戦略との境界**: Layer 1 in-memory deque + ms 級 VWAP/Z 計算は戦略側、永続化と REST 補完はデータ層

### 9.5.2 中期 (Phase 7 後半)
- データ規模が SQLite で詰まり始めたら PG + TimescaleDB 移行を検討
- 移行は [ADR-0007](../adr/0007-db-bucket-isolation-decisions.md) の Phase 8 計画と統合

### 9.5.3 長期 (Phase 8 想定)
- TimescaleDB hypertable 化 (`common.market_ticks`)
- Continuous Aggregates で 1 分 / 5 分 / 1 時間 OHLCV を自動生成
- 圧縮ポリシー (7 日経過 → 列指向圧縮 90%+)
- Retention Policy (5 年 → 自動 drop)

詳細は [ADR-0008](../adr/0008-tick-data-pipeline-decisions.md) 参照。

---

## 10. 改訂履歴

| Date | Who | Change |
| --- | --- | --- |
| 2026-05-09 | Claude | 初版作成。3 層構成 + TimescaleDB 推奨 + Phase A/B/C 段階導入 |
| 2026-05-09 | Claude | TQ-1〜6 全件クローズ・Status を Decided に変更・§9.5 Decisions (確定) を追加。ADR-0008 起草 |
