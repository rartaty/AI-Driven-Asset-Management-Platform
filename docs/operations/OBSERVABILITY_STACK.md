# Observability Stack — 個人ローカル運用版

> **Status**: v1 (2026-05-09 / Phase 6 / ADR-0010 §6-6)
> **Scope**: 個人ローカル運用での観測手段。商用 SRE の Prometheus + Grafana + Jaeger は採用せず、**既存の Discord + System_Logs + Frontend で完結** させる方針。
> **関連**: [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md), [SLI_SLO.md](SLI_SLO.md), [docs/runbooks/](../runbooks/)

---

## 0. 設計思想

- **観測手段を増やさない** — 個人運用では「観測スタックを運用する負担」が「障害検知の遅延コスト」を上回る
- **既存資産で完結** — Phase 4 (Discord), Phase 4 (System_Logs DB), Phase 5/7 (Frontend) で観測機能は揃っている
- **将来のスケールアップ余地** — チーム化 / クラウド移行時の拡張パスは §5 に記載

---

## 1. 観測 4 大柱 (商用 SRE) と本プロジェクトの実装

| 商用 SRE 4 大柱 | 商用例 | 本プロジェクト |
| --- | --- | --- |
| **Logs (構造化ログ)** | ELK / Loki / CloudWatch Logs | **python-json-logger + System_Logs DB** ([Phase 4](../adr/0005-phase4-scope-standard.md) 完了済) |
| **Metrics (メトリクス)** | Prometheus + Grafana | **Daily_Asset_Snapshot / Trade_Tick_Log / Market_Ticks の DB クエリ** で代替 |
| **Traces (分散トレース)** | Jaeger / Tempo | **採用せず** — 単一プロセス・単一 PC のため不要 |
| **Alerts (アラート)** | Alertmanager / PagerDuty | **Discord (notify_critical / notify_system / notify_trade)** ([Phase 4](../adr/0005-phase4-scope-standard.md) 完了済) |

---

## 2. ログ層 (Logs)

### 2.1 構造化ログ (Phase 4 完了済)
- [backend/src/core/logger.py](../../backend/src/core/logger.py)
- `python-json-logger` で全ログを JSON 形式に統一
- 環境変数 `LOG_FORMAT=json|text` で切替

### 2.2 永続化先
- **stdout / stderr** — 開発時の即時確認
- **System_Logs テーブル (DB)** — 監査・トレース用永続化 (DBLogHandler 経由)
- **`logs/*.log` ファイル** — 環境変数 `LOG_FILE_PATH` 指定時のみ

### 2.3 ログ検索パターン (運用上よく使う)
```sql
-- 直近 1 時間の ERROR / CRITICAL
SELECT timestamp, component, event, payload FROM system_logs
 WHERE level IN ('ERROR', 'CRITICAL') AND timestamp > datetime('now', '-1 hour')
 ORDER BY timestamp DESC;

-- 特定コンポーネントの直近イベント
SELECT timestamp, level, event FROM system_logs
 WHERE component LIKE '[KillSwitch]%' ORDER BY timestamp DESC LIMIT 50;

-- 障害発生時刻周辺の全イベント (ある秒前後 30 秒)
SELECT * FROM system_logs
 WHERE timestamp BETWEEN datetime('2026-05-09 14:50:00')
                     AND datetime('2026-05-09 14:50:30')
 ORDER BY timestamp;
```

### 2.4 ログ retention
- System_Logs DB: 無制限 (Phase 8 で retention policy 検討予定)
- ファイル: 手動 rotate (将来 logrotate 風スクリプト導入候補)

---

## 3. メトリクス層 (Metrics)

商用 SRE では Prometheus が時系列値 (Counter / Gauge / Histogram) を 15 秒間隔等で収集する。本プロジェクトは **DB テーブルがそれ自体メトリクスストア** として機能。

### 3.1 主要メトリクス源

| メトリクス | テーブル / クエリ | 粒度 |
| --- | --- | --- |
| 資産推移 | `daily_asset_snapshot` | 日次 |
| ポジション PnL | `trade_tick_log` | 分単位 |
| 歩み値 (price/volume) | `market_ticks` | 1 秒 bucket |
| API 健全性 | `system_logs` (component='[API:*]') | イベント毎 |
| Kill Switch 状態 | `user_settings.is_kill_switch_active*` | イベント毎 |
| Profit Sweep 完遂 | `system_logs` ([ProfitSweep]) | 日次 |

### 3.2 メトリクス計装の追加ポイント (将来候補)

商用 Prometheus 風の計装が必要になったら以下を検討:
- 短期判定レイテンシ ([SLI 1.1](SLI_SLO.md)) を System_Logs に明示記録
- WebSocket 切断回数 / 再接続成功率 (System_Logs から集計可)
- Kill Switch 解除頻度 (監査用)

### 3.3 ダッシュボード
- **Frontend dashboard** ([page.tsx](../../frontend/src/app/page.tsx)) が代替
- 多角的時間軸チャート ([routers/portfolio.py](../../backend/src/routers/portfolio.py) `/chart/{timeframe}`) で日次 / 月次 / 分単位を可視化
- M11 Frontend 拡張で SLI ダッシュボード化を将来検討

---

## 4. アラート層 (Alerts)

### 4.1 Discord 通知 (現行)
| 通知種別 | 関数 | 用途 | チャンネル |
| --- | --- | --- | --- |
| `notify_critical()` | core/discord.py | Kill Switch / 致命系 | `#alerts` |
| `notify_system()` | core/discord.py | System / Operations | `#system` |
| `notify_trade()` | core/discord.py | 約定 / Signal | `#trading` |

### 4.2 アラート発火パス (Phase 6 まで完成済)
- Kill Switch 発火 → `notify_critical` ([core/kill_switch.py](../../backend/src/core/kill_switch.py))
- API 障害 → `notify_system` ([api/kabucom.py](../../backend/src/api/kabucom.py), [api/opencanvas.py](../../backend/src/api/opencanvas.py))
- Profit Sweep / Backup / Quarterly Review → `notify_system` ([scheduler.py](../../backend/src/services/scheduler.py))
- VWAP Signal 発火 → `notify_trade` ([scheduler.job_short_term_trade](../../backend/src/services/scheduler.py))

### 4.3 アラート抑制 (Future)
個人運用では「同一エラー連発で Discord が騒がしい」問題が稀に発生。Phase X で:
- 連続発火の rate limiting (5 分以内同種エラー = 1 通知に集約)
- アラート優先度別チャンネル分離

---

## 5. 商用観測スタックを採用しない理由

| 観点 | 商用採用時 | 個人運用 |
| --- | --- | --- |
| 構築コスト | Prometheus + Grafana + Alertmanager の運用管理 | 構築不要 (既存資産で完結) |
| ストレージ | 数 TB/年の時系列 DB | < 100 MB/年の SQLite |
| アラート受信 | PagerDuty / OpsGenie | Discord (個人なら十分) |
| ダッシュボード | Grafana | Frontend dashboard |
| 単一プロセス | Prometheus が monitoring の monitoring まで管理 | プロセス死は Podman restart で回復 |
| 学習コスト | Prometheus query language (PromQL) 習熟必要 | SQL 1 言語で完結 |

**判断**: 要件 §A.1 (RTO/RPO 保証なし・単一 PC) / §A.2 (HW 冗長化対象外) の方針通り、商用観測スタックは個人運用フェーズではオーバースペック。

---

## 6. 将来拡張パス (チーム化 / クラウド移行時)

| トリガー | 拡張アクション |
| --- | --- |
| ユーザー数 > 1 | OpenTelemetry 計装 + Tempo/Jaeger でトレース可視化 |
| データ量 > 数 TB | TimescaleDB hypertable + Continuous Aggregates ([ADR-0007](../adr/0007-db-bucket-isolation-decisions.md) Phase 8) |
| クラウド移行 | CloudWatch Logs + Metrics / Datadog / New Relic 検討 |
| アラート受信者 > 1 | PagerDuty / OpsGenie / Discord 多チャンネル戦略 |
| SLO budget 自動運用 | Prometheus + Grafana + Alertmanager で Burn Rate alert |

---

## 7. 改訂履歴

| Date | Who | Change |
| --- | --- | --- |
| 2026-05-09 | Claude | 初版作成 (Phase 6 / ADR-0010 §6-6 / 個人運用版観測スタック) |