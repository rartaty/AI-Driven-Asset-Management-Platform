# ADR-0008: Tick Data Pipeline 設計決定 (TQ-1〜6 クローズ)

- **Status**: Accepted
- **Date**: 2026-05-09
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0001 (SQLite 一次採用), ADR-0007 (DB Bucket Isolation 決定)
- **Related Design Memo**: [TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md)

---

## Context (背景)

短期トレード戦略 ([SHORT_VWAP_DETAIL.md](../strategy/SHORT_VWAP_DETAIL.md)) の精度向上には、約定単位の連続データ (歩み値 / Time & Sales) が必要。kabu Station Push API は直接的な歩み値フィードを提供しないため、Push 受信を秒単位で集約して**疑似 tape を再構築する**方針となる。

設計メモ ([TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md)) で:
- 3 層構成 (HOT in-memory / WARM TimescaleDB / COLD compressed)
- Apache Iceberg と InfluxDB を比較検討 (個人スケールでは TimescaleDB が最適)
- 3 段階導入 (Phase A: SQLite / Phase B: PG+TimescaleDB / Phase C: 高度化)

を整理した上で 6 つの Open Questions を立てた。Phase 5 完了時点でこれらをクローズし、Phase 7 着手時の前提を確定する。

## Decision (決定)

### TQ-1: 短期ユニバース初期サイズ
**50 銘柄初期 / 100〜200 銘柄まで段階拡張**

- ボリューム見積: 50 × (1.8 MB/日) ≈ **90 MB/日**, 250営業日で **22 GB/年**
- SQLite で扱える範囲 (年次 22 GB なら適切な partition で運用可能)
- ユニバース増加は運用実績を見ながら漸進的に。100 → 200 銘柄拡張で 90 GB/年規模に到達したら Phase 8 (TimescaleDB 移行) を発火

### TQ-2: SQLite 先行 vs PG 待ち
**Phase A (SQLite + 日次 partition テーブル) で先行実装**

- ADR-0007 OQ-1 と整合 (PG 移行は Phase 8 へ持ち越し)
- 日次 partition テーブル (`market_ticks_YYYY_MM_DD`) で SQLite の index 性能劣化を緩和
- 単一テーブル + 複合 index (timestamp, ticker_symbol) も検討可能 (実装時に benchmark で判断)
- 早く検証ループを回し、実データ規模に基づいて Phase 8 を計画

### TQ-3: テーブル命名
**`Market_Ticks` (新規)**

- 既存 `Trade_Tick_Log` は **自ポジションの分単位 PnL** ログ → 別物
- `Market_Ticks` は **市場の歩み値再構築** ログ → 名前で明確に区別
- 両方を schema.py に併存

### TQ-4: PAPER 時の yfinance 変換責任
**データ層 (`services/market_data.py` 新規) が担当**

- 戦略 (strategy/vwap_short.py) は **Layer 1 in-memory deque から読むだけ**
- データ層が以下を吸収:
  - REAL モード: kabu Push 受信 → 1 秒バケット集約 → Market_Ticks 永続化
  - PAPER モード: yfinance 1 分足 → 疑似 tick 変換 (volume 一様分布按分 / side_inference='MID' 固定)
  - 戦略はモードを意識せず Layer 1 から読む

責任境界の利点:
- PAPER/REAL 切替がデータ層で完結 → 戦略コードの分岐ゼロ
- yfinance API 変更時の影響範囲が data 層に限定
- バックテスト時に静的フィクスチャからの再現も同じインターフェースで可能

### TQ-5: gap 検出時の fallback
**REST 補完 + `is_synthetic=True` フラグ**

- WebSocket 切断検知 → 自動再接続 (exponential backoff)
- 復帰時に `cumulative_volume` の単調性チェック → gap 検出
- gap 区間は kabu REST API から OHLCV を取得して補完
- 補完データには `is_synthetic=True` を付与
- **戦略は `is_synthetic=True` の tick を判定材料に使わない** (安全寄り)
- 完全欠損 (REST 補完も失敗) の場合は短期 bucket キルスイッチを発火

### TQ-6: `Market_Ticks` の bucket 配置
**common 配置 (bucket 隔離対象外)**

- ADR-0007 で確定した schema-per-bucket 構成において、`Market_Ticks` は **共有テーブル**
- 全 bucket が参照する銘柄市場データ (`Asset_Master` と同じ性質)
- bucket 別に Market_Ticks を持つと容量 4-5倍 + 同じデータの重複保管
- Phase 8 の PG 移行時は `common.market_ticks` schema 内に配置

## Consequences (結果)

### ✅ Positive
- Phase 7 着手時に **設計議論を再開する必要なし**、即実装に入れる
- データ層と戦略層の責任境界が明確
- yfinance 変換ロジックの場所が確定 (services/market_data.py)
- gap 補完時の安全策 (`is_synthetic=True`) で誤判定防止
- Phase 8 (PG 移行) の Market_Ticks 配置が確定

### ⚠️ Negative / Trade-off
- SQLite 日次 partition は手動 cleanup ロジックが必要 (5 年保管後 drop)
- PAPER モード時の疑似 tick は精度低 (1 分内一様分布仮定) → 短期戦略バックテストで限界あり
- 50 銘柄からスタートはユニバース拡張時の運用判断が必要

### 🔄 Required Follow-up
- ✅ 本 ADR を起草・Status=Accepted
- ✅ 設計メモ ([TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md)) を Decided 状態に更新
- ✅ taskboard.md Decisions Log への転記
- 📋 Phase 7 着手時に以下を実装:
  - `Market_Ticks` テーブル schema.py 追加 + db-schema-reviewer 監査
  - `services/market_data.py` 新規 (Layer 1 deque + 永続化 + PAPER yfinance 変換)
  - WebSocket クライアント (M10 / `websocket-client` 依存追加)
  - Layer 1 ↔ 戦略の interface 確定 (vwap_short.py から data 層を参照)
- 📋 Phase 8 (PG 移行) 着手時に Market_Ticks を TimescaleDB hypertable 化

## Alternatives Considered (検討した代替案)

### 案 X: Apache Iceberg
- **概要**: データレイクテーブルフォーマット (Netflix 系)
- **却下理由**: 個人スケール (年間数十 GB) には catalog 運用 / metadata 管理コストが過大。PB スケール想定の OLAP 用途。OLTP (Profit Sweep の即時更新) と相性悪い

### 案 Y: InfluxDB
- **概要**: 時系列 DB
- **却下理由**: 跨テーブル ACID なし → Profit Sweep 等のリレーショナル整合性破壊。SQLAlchemy 非対応で polyglot 運用負荷。TimescaleDB (PG 拡張) が上位互換

### 案 Z: Plain Parquet + DuckDB
- **概要**: データレイク軽量版
- **却下理由**: TimescaleDB の continuous aggregates / 圧縮ポリシーで同等効果が得られる。エンティティを増やしたくない

### 案 W: Apache Kafka / Redis Streams
- **概要**: ストリーミング基盤
- **却下理由**: 単一プロセス運用なら in-memory deque で十分。複数プロセス化したら再検討

詳細比較は [TICK_DATA_PIPELINE_DESIGN.md §9](../architecture/TICK_DATA_PIPELINE_DESIGN.md) を参照。

## Related (関連)

- 設計メモ: [TICK_DATA_PIPELINE_DESIGN.md](../architecture/TICK_DATA_PIPELINE_DESIGN.md)
- DB Bucket Isolation 決定: [ADR-0007](0007-db-bucket-isolation-decisions.md)
- 既存 DB 選定: [ADR-0001](0001-sqlite-as-primary-db.md)
- 短期戦略詳細: [SHORT_VWAP_DETAIL.md](../strategy/SHORT_VWAP_DETAIL.md)
- 関連要件: [REQUIREMENTS_DEFINITION.md §B.2](../REQUIREMENTS_DEFINITION.md) (短期は ms 級レイテンシ要件)
- Audit: [docs/audit/2026-05-09-progress.md](../audit/2026-05-09-progress.md)
- Taskboard: [taskboard.md](../../taskboard.md)

## Notes

### Phase 7 (Tick Data Pipeline 実装) 着手時のチェックリスト
- [ ] `Market_Ticks` schema 追加 (timestamp, ticker_symbol, last_price, cumulative_volume, delta_volume, bid_price, ask_price, side_inference, is_synthetic)
- [ ] `db-schema-reviewer` subagent でレビュー
- [ ] `services/market_data.py` 実装 (deque + flush + REST fallback + PAPER 変換)
- [ ] WebSocket クライアント (`websocket-client` 依存追加 + M10 解消)
- [ ] vwap_short 戦略から `services/market_data.py` の deque を読むように改修 (戦略は Layer 1 のみ参照)
- [ ] gap 検出 → 短期 bucket キルスイッチ発火パスのテスト追加
- [ ] PAPER モード時の yfinance 1 分足変換テスト追加
- [ ] backend/tests に test_market_data.py 新規

### Phase 8 (PG + TimescaleDB 移行) 着手時のチェックリスト
- [ ] [ADR-0007](0007-db-bucket-isolation-decisions.md) の PG 移行と統合
- [ ] `Market_Ticks` を `common.market_ticks` schema 配下の hypertable 化
- [ ] Continuous Aggregates (1分 / 5分 / 1時間 OHLCV) 作成
- [ ] 圧縮ポリシー (7 日経過 → 列指向圧縮)
- [ ] Retention Policy (5 年 → drop)
- [ ] 既存 SQLite 日次 partition テーブルからのデータ移行スクリプト
