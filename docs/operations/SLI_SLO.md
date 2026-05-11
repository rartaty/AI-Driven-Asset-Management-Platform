# SLI / SLO 定義 — 個人ローカル運用版

> **Status**: v1 (2026-05-09 / Phase 6 / ADR-0010 §6-4)
> **Scope**: 個人運用の最小限。商用 SLA レベルではなく「**何が壊れたら直すべきか**」の判断軸を明文化。
> **関連**: [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md), [OBSERVABILITY_STACK.md](OBSERVABILITY_STACK.md), [docs/runbooks/](../runbooks/)

---

## 0. 設計思想

- **SLI (Service Level Indicators)**: 何を測るか — システムの健全性を表す客観的指標。
- **SLO (Service Level Objectives)**: どのレベルを保証するか — 個人運用なので「可用性 99.99%」のような商用クラスは無理。**「最低限達成すべき / 違反したらすぐ直す」レベル**を定義。
- **SLI/SLO 違反 → 自動的に Runbook へリンク** — 観測 → 判断 → 行動 のパイプラインを短くする。

商用クラスの SRE では SLO budget / Error Budget Policy / Burn Rate alert 等を導入するが、個人運用ではオーバースペック。**「観測 + 違反検知 + Runbook 案内」までで止める**。

---

## 1. SLI 一覧 (測定対象)

### 1.1 短期判定レイテンシ (Critical)

| 項目 | 内容 |
| --- | --- |
| **何を測るか** | `services/market_data.py` Layer 1 deque から tick 取得 → vwap_short.evaluate_vwap_signal 戻り値 までの所要時間 |
| **要件** | §B.2 (kabu Push 受信後 ms 級判定) |
| **測定方法** | 各 evaluate_vwap_signal 呼び出しを時間計測 → System_Logs に記録 (Phase 7+ で計装追加) |
| **単位** | ms |

### 1.2 kabu API 接続成功率 (High)

| 項目 | 内容 |
| --- | --- |
| **何を測るか** | `KabucomAPIClient.authenticate()` および `KabuPushClient` WebSocket 接続の成功率 |
| **要件** | §6 fail-safe / Phase 7 (M10) |
| **測定方法** | System_Logs の `[API:Kabucom]` および `[KabuPush]` レベル分布 (ERROR の比率) |
| **単位** | % (1 日単位の成功率) |

### 1.3 Profit Sweep 完遂率 (Critical)

| 項目 | 内容 |
| --- | --- |
| **何を測るか** | 毎営業日 14:50 の `job_profit_sweep` が「全 short ポジションを決済」 + 「Discord 通知」まで完遂した日の比率 |
| **要件** | §6 オーバーナイトリスク排除 |
| **測定方法** | System_Logs で `[ProfitSweep] All paper positions closed` の出現を日次カウント |
| **単位** | % (営業日基準) |

### 1.4 Daily_Asset_Snapshot 永続化成功率 (High)

| 項目 | 内容 |
| --- | --- |
| **何を測るか** | 毎日 `job_sync_portfolio` が `write_daily_snapshot` を成功させた比率 |
| **要件** | §8 / Phase 5 P11 |
| **測定方法** | Daily_Asset_Snapshot のレコード数 (期間内営業日数 / 期待値) |
| **単位** | % |

### 1.5 Kill Switch 応答性 (Critical)

| 項目 | 内容 |
| --- | --- |
| **何を測るか** | DD ≤ -3% 検知から `is_kill_switch_active=True` 書込までの所要時間 |
| **要件** | §6 / 絶対禁止 3 |
| **測定方法** | check_drawdown_and_trigger の単体テスト (CI 相当) |
| **単位** | ms (本番では即時、テストで保証) |

### 1.6 Layer 1 → Layer 2 flush 成功率 (Medium)

| 項目 | 内容 |
| --- | --- |
| **何を測るか** | 毎分 `job_flush_market_ticks` が `flush_layer2` を成功させた比率 |
| **要件** | Phase 7 / ADR-0009 |
| **測定方法** | System_Logs `[MarketData] flush_layer2 failed` の出現頻度 |
| **単位** | % (取引時間内毎分基準) |

### 1.7 バックアップ成功率 (Medium)

| 項目 | 内容 |
| --- | --- |
| **何を測るか** | 毎営業日 `job_backup_daily` の成功率 |
| **要件** | §A.2.6 / §C.1 / Phase 6 P6-2 |
| **測定方法** | `backups/data-YYYYMMDD.db` の存在確認 (期間内営業日 vs 期待値) |
| **単位** | % |

---

## 2. SLO (目標値) — 個人運用版

| # | SLI | SLO | 違反時アクション |
| --- | --- | --- | --- |
| 1.1 | 短期判定レイテンシ | **p95 < 50ms** (1 秒以内に余裕あり) | RB-007 (Layer 1 異常) → vwap_short プロファイル |
| 1.2 | kabu API 接続成功率 | **日次 > 95%** (取引時間 5h × 1 push/sec ≈ 18000 リクエスト中 < 900 失敗) | [RB-002](../runbooks/RB-002-kabu-api-down.md) |
| 1.3 | Profit Sweep 完遂率 | **月次 100%** (1 件でも未完遂は障害) | [RB-006](../runbooks/RB-006-profit-sweep-incomplete.md) |
| 1.4 | Daily_Asset_Snapshot 永続化 | **月次 100%** (1 日でも欠損は障害) | [RB-005](../runbooks/RB-005-snapshot-write-failure.md) |
| 1.5 | Kill Switch 応答性 | **テストで p99 < 10ms** | テスト失敗 → Phase X リグレッション |
| 1.6 | Layer 1 → 2 flush | **日次 > 99%** (取引時間 5h × 60 = 300 件中 < 3 件失敗) | [RB-005](../runbooks/RB-005-snapshot-write-failure.md) (関連) |
| 1.7 | バックアップ成功率 | **月次 100%** | [RB-008](../runbooks/RB-008-data-db-corruption.md) (関連) |

### 2.1 SLO 違反検知方法

- **自動検知 (現状)**: `job_check_kill_switch` (15:30) 内で簡易チェック → 違反なら Discord 通知
- **手動レビュー (週次)**: 金 16:00 の Weekly Bucket Export 時に SLI ダッシュボードを目視確認 (Frontend 拡張で M11 統合予定)
- **原則**: SLO 違反 = Runbook 起動の合図 (Incident Response §1 の Discord 通知から推定可能な障害種別フロー と整合)

---

## 3. 商用 SLO との違い (なぜ簡素化したか)

| 商用 SRE | 本プロジェクト | 理由 |
| --- | --- | --- |
| Error Budget / Burn Rate | 採用せず | 個人運用で Burn Rate alert を運用するコストの方が高い |
| 99.9% 系 SLO | 採用せず | 単一 PC 運用の物理制約 (要件 §A.1) で達成不能・誤導的 |
| MTTR/MTBF 自動計測 | 手動レビュー | 障害頻度が低いので測定統計が貯まらない |
| Service Level Indicator Dashboard (Grafana等) | Frontend で代替 | 観測スタックを増やさない方針 ([OBSERVABILITY_STACK.md](OBSERVABILITY_STACK.md)) |
| User-facing SLA | 不要 | 個人運用 (要件 §E.1.1.1: 他者公開禁止) |

---

## 4. 将来拡張 (チーム化 / クラウド移行時)

- Prometheus + Grafana 導入 → Burn Rate alert 自動化
- SLI 計装を各サービスに埋め込み (OpenTelemetry)
- Error Budget Policy 策定
- カスタムダッシュボード (Frontend M11 完成後)

---

## 5. 改訂履歴

| Date | Who | Change |
| --- | --- | --- |
| 2026-05-09 | Claude | 初版作成 (Phase 6 / ADR-0010 §6-4 / SLI 7 件 + SLO 個人運用版) |