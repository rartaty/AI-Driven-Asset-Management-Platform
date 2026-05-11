# DB Bucket Isolation 設計メモ

> **Status**: **Decided** (2026-05-09 設計議論クローズ)
> **Trigger**: ユーザー要望「証券口座の現金 / 投資信託 / 長期堅実積立 / 長期テンバガー狙い / 短期トレード の DB を別々で管理したい」
> **目的**: bucket 単位の障害隔離 (「投信が止まっても長期/短期は動く」)
> **決定**: 採択された決定は本ファイル末尾「§11 Decisions (確定)」と [ADR-0007](../adr/0007-db-bucket-isolation-decisions.md) を参照。
> **Decisions Log の正規格納先**: [taskboard.md](../../taskboard.md) (OQ-7 で確定)。

---

## 0. 用語定義

| 用語 | 定義 |
| --- | --- |
| **bucket** | 資金プールの論理単位。本プロジェクトでは 5 つ: `cash` / `passive` (投資信託) / `long_solid` (長期コア・堅実積立) / `long_growth` (長期サテライト・テンバガー狙い) / `short` (短期トレード) |
| **fault isolation** | ある障害が他の正常系に伝播せず、独立に稼働継続できる状態 |
| **Profit Sweep** | 短期トレード利益の 50% を毎日 14:50 に長期コア (`long_solid`) へ振替する仕様 ([REQUIREMENTS_DEFINITION.md §2.2](../REQUIREMENTS_DEFINITION.md)) |
| **ACID** | Atomicity / Consistency / Isolation / Durability。複数 DB 操作が「全て成功 or 全て失敗」を保証する性質 |

---

## 1. 問題ステートメント

### 1.1 ユーザーの一次目的
**「ある bucket の障害が他の bucket の稼働を止めないこと」**

### 1.2 想定する障害シナリオ (ユーザー確認済 = 全て対象)

| # | 障害 | 影響イメージ | 発生頻度 (推定) |
| --- | --- | --- | --- |
| F1 | DB データ破損 (ファイル破損・物理破損) | `data.db` 読込不可 → システム全停止 | 極稀 (年1未満想定) |
| F2 | 特定 bucket のロジックバグ | 投信ロジックが NaN 書込 → 集計クエリで例外連鎖 | 中 (新機能追加時など) |
| F3 | 外部 API 障害 (kabu / SSM / yfinance) | API 依存 bucket は稼働不可、非依存 bucket は稼働可 | 高 (月1〜数回) |
| F4 | スキーマ migration 障害 | 投信テーブル変更中に他 bucket の取引が止まる | 低〜中 (DB変更時) |

### 1.3 すでに整理済の関連要件

| 要件 | 出典 | 内容 |
| --- | --- | --- |
| 単一 PC ローカル運用 | [REQUIREMENTS_DEFINITION.md §A.1](../REQUIREMENTS_DEFINITION.md) | RTO/RPO 保証なし、Podman `restart: always` で自動再起動 |
| HW 冗長化対象外 | §A.2 | DR サイト・遠隔地保管も対象外 |
| 短期 → 長期コア Profit Sweep | §2.2 | 毎営業日 14:50、利益 50% 振替 |
| Tolerance Band ±5% | §2.2 | 株式内 60/40 (日次) と長期内 70/30 (週次) |
| キルスイッチ V2 | §6 | DD -3% でブロック、`is_kill_switch_active` 単一フラグ |
| PostgreSQL 15 が本番要件 | §3 | SQLite はローカル運用デフォルト、移行未実施 |

---

## 2. 論点分解 (4 トピック)

ユーザー要望「DB 別管理」は実は 4 つの独立した論点を含む。**順番に決めるのが正しいアプローチ**。

```
┌─────────────────────────────────────────────────────────┐
│ A. データ整合性 (Profit Sweep の ACID 保証)             │
│    └ DB 技術選定で決まる (SQLite vs PostgreSQL)         │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ B. 設計上の整理 (bucket 別の論理分離)                    │
│    └ スキーマ設計で決まる (テーブル分割 or 既存 enum)    │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ C. ランタイム障害隔離 (片方が止まっても他は生きる)        │
│    └ アプリ層 / プロセス層で決まる (DB だけでは解けない) │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│ D. 要件適合 (PostgreSQL 移行をいつやるか)                │
│    └ Phase 計画で決まる                                  │
└─────────────────────────────────────────────────────────┘
```

各トピックの選択肢と推奨は §3〜§6 で詳述。

---

## 3. Topic A: Profit Sweep の ACID 保証

### 3.1 何が問題か

短期 (`short`) と長期コア (`long_solid`) を **別々のデータストア** に置くと、Profit Sweep の振替処理が「全て成功 or 全て失敗」を保証できない。

```
理想: 短期から ¥5,000 減算 ∧ 長期コアに ¥5,000 加算  ← 同一トランザクション
リスク: 短期減算 commit 後にプロセス死 → 長期側に未反映 → ¥5,000 消失
```

### 3.2 選択肢

| 案 | 仕組み | ACID 保証 | 実装コスト |
| --- | --- | --- | --- |
| **A1. SQLite 単一ファイル** | 全 bucket を `data.db` に | ✅ 完全 (現状) | ゼロ (現状) |
| A2. SQLite 複数ファイル + 冪等2段階書込 | UUID + `status='PENDING'` + 起動時リカバリ | ⚠️ 結果整合 (eventually consistent) | 高 (リカバリロジック・テスト) |
| A3. SQLite 複数ファイル + Outbox パターン | 各 bucket DB に events テーブル、外部 worker が中継 | ⚠️ 結果整合 | 高 |
| **A4. PostgreSQL 単一インスタンス + schema** | bucket 別 schema、跨 schema トランザクション | ✅ 完全 | 中 (PG 環境構築) |
| A5. PostgreSQL 複数 DB + 2PC (PREPARE TRANSACTION) | XA 風分散トランザクション | ✅ 完全 (理論上) | 高 (2PC 運用は罠多い) |
| A6. Event Sourcing | 全変更をイベントログ、bucket 別 projection | ✅ ログ整合 | 非常に高 |

### 3.3 評価

- **A1** は現状維持で最も安全。ただしユーザー要望と矛盾。
- **A4** は ACID + 論理分離 + 要件適合の三立。**第一推奨**。
- **A2/A3** は SQLite 縛りで分離したい場合の妥協案。冪等性キー必須。
- **A5** は PG でも複数 DB に分けたい場合のみ。2PC は本番運用での罠 (timeout・orphan) が多く避けたい。
- **A6** は将来監査要件が厳しくなった場合のオプション。今は overkill。

---

## 4. Topic B: bucket 別の論理分離 (設計整理)

### 4.1 現状

[backend/src/models/schema.py](../../backend/src/models/schema.py) で **すでに論理分離済**:

- `Asset_Master.category` enum: `passive` / `long_solid` / `long_growth` / `short`
- `Daily_Asset_Snapshot` の bucket 別カラム: `trust_value`, `long_solid_value`, `long_growth_value`, `short_term_*`

「物理分離 (テーブル/DB を分ける)」と「論理分離 (1テーブル + discriminator)」は別の話。

### 4.2 選択肢

| 案 | 構造 | クエリ |
| --- | --- | --- |
| **B1. discriminator 維持 (現状)** | 1 テーブル + `category` 列 | `WHERE category='passive'` |
| B2. bucket 別テーブル (1 DB 内) | `Trade_Logs_Trust`, `Trade_Logs_LongSolid`, ... | テーブル直指定、UNION で集計 |
| B3. bucket 別 schema (PG) | `trust.trade_logs`, `long_solid.trade_logs` | `SET search_path` または full path |
| B4. bucket 別 DB ファイル (SQLite) | `trust.db`, `long_solid.db`, ... | engine bind 切替 |

### 4.3 評価

- **B1** は dev 簡素・cross-bucket 集計簡単。ロジックバグでテーブル全体が壊れるリスクあり。
- **B2** は中間案。同一 DB 内なのでトランザクション簡単。テーブル数増 (12 → 16〜20)。
- **B3** は PG 採用なら最良。schema 単位の権限・バックアップ・migration が独立化。
- **B4** は SQLite で物理隔離する唯一手段だが、Topic A の ACID 問題を引き起こす。

### 4.4 「現金 (cash) bucket」の特殊扱い

現金は **値1個 (`brokerage_cash`)** で表現可能。独立テーブル/DB/schema にする実利が乏しい:
- 保有銘柄なし (Holdings テーブル不要)
- 取引履歴なし (Trade_Logs 不要)
- 既存 `Cash_Pool_Status` テーブルで完結

**推奨**: cash は独立 bucket として扱わず、`Cash_Pool_Status` (common) に統合。

---

## 5. Topic C: ランタイム障害隔離

### 5.1 「DB 分離 = 障害隔離」は誤解

ユーザー目的「投信が止まっても他は動く」を達成するために必要なのは **アプリ層・プロセス層の隔離**。DB 層だけでは:

- DB 分離してもアプリプロセスが 1 つなら、投信ロジックの例外で全 bucket スレッドが落ちる
- DB 分離しても外部 API 障害は回避できない
- DB 分離しても schema migration 中の他 bucket への影響は防げない (アプリが migration 待ち)

### 5.2 障害シナリオ別の本当の対策

| シナリオ | 対策 (層) | 具体策 |
| --- | --- | --- |
| **F1. DB 破損** | バックアップ層 | 日次 `pg_dump --schema=trust`。bucket 単位で pinpoint restore 可能。 |
| **F2. bucket ロジックバグ** | アプリ層 | bucket 別 try/except + bucket 別キルスイッチ。投信例外で投信のみ停止。 |
| **F3. 外部 API 障害** | アプリ層 | API 単位の Circuit Breaker + 依存マップ。kabu 落ちたら kabu 依存 bucket のみ停止。 |
| **F4. schema migration** | DB 層 | PG schema 別 migration (`alembic --schema=trust upgrade`)。他 schema は無影響。 |

### 5.3 選択肢 (アプリ層)

| 案 | 構造 | 隔離強度 |
| --- | --- | --- |
| **C1. 単一プロセス + bucket 別 try/except** | 1 Python プロセス、5 bucket worker (関数or class) | 中 (ロジック例外は隔離可、プロセス死は全停) |
| C2. 単一プロセス + bucket 別スレッド | 1 Python プロセス、5 thread | 中 (GIL あり、CPU 並列メリット薄) |
| C3. 単一プロセス + bucket 別 asyncio タスク | 1 Python プロセス、5 task | 中 (現状の APScheduler と相性◎) |
| C4. 複数プロセス + IPC (multiprocessing) | 5 子プロセス + メッセージキュー | 高 (process 死を局所化) |
| C5. マイクロサービス (5 コンテナ) | 5 Podman container + REST/Queue | 最高 (本格分散) |

### 5.4 評価

- **C1/C3** は実装コスト最小。投信ロジックバグ (F2) には十分対応。プロセス死 (Python interpreter crash) には無力だが、`Podman restart: always` で再起動を吸収。
- **C4** は process-level isolation。値段は IPC 設計の複雑さ (Profit Sweep を IPC 経由で確実にやる)。
- **C5** は要件 §A.2 (HW 冗長化対象外) と矛盾。個人ローカルではオーバースペック。

### 5.5 キルスイッチ拡張

bucket 単位の停止フラグを追加:
```sql
-- 現状: 全体1個
is_kill_switch_active BOOLEAN

-- 拡張案: 全体 + bucket 別
is_kill_switch_active BOOLEAN              -- 既存
is_kill_switch_active_passive BOOLEAN
is_kill_switch_active_long_solid BOOLEAN
is_kill_switch_active_long_growth BOOLEAN
is_kill_switch_active_short BOOLEAN
```

絶対禁止 3 (キルスイッチ無断解除禁止) は **全フラグに適用**。

---

## 6. Topic D: PostgreSQL 移行 (要件適合)

### 6.1 現状

- 要件: PostgreSQL 15 ([REQUIREMENTS_DEFINITION.md §3](../REQUIREMENTS_DEFINITION.md))
- 実装: SQLite (`backend/src/data.db`)
- 移行手順: [docs/DB_MIGRATION_GUIDE.md](../DB_MIGRATION_GUIDE.md) (作成済?要確認)
- 関連: ハーネス Phase 4 (core/ 本実装) と統合予定

### 6.2 移行を「いつやるか」

| 案 | タイミング | メリット | デメリット |
| --- | --- | --- | --- |
| **D1. 本件と統合 (前倒し)** | Topic A/B/C と同時に実施 | ACID + schema 分離 + 障害隔離が一発で揃う | スコープ膨張・テスト全面見直し |
| D2. Phase 4 で別 step として | 本件は SQLite のまま、PG は Phase 4 で別途 | スコープ最小 | SQLite 設計を作って即捨てる無駄 |
| D3. 後回し | 個人運用は SQLite で永続 | 環境構築不要 | 要件と乖離継続 |

### 6.3 評価

- **D1** が最も筋が通る。SQLite で複雑な冪等2段階書込を作るより、PG schema トランザクションで素直に書く方が技術的負債が少ない。

---

## 7. 障害シナリオ × 設計案 マトリクス

各設計案がどの障害をカバーするかの早見表。

| 設計案 | F1 DB破損 | F2 ロジックバグ | F3 API障害 | F4 migration障害 |
| --- | --- | --- | --- | --- |
| **現状 (SQLite 1ファイル)** | ❌ 全停止 | ❌ DB全体破損リスク | ⚠️ アプリで救済必要 | ❌ 全停止 |
| **SQLite + bucket別ファイル** | ⚠️ 投信のみ復旧で済む | ⚠️ 他 bucket 連鎖は防げる | ⚠️ アプリで救済必要 | ⚠️ 投信のみ migration 中停止 |
| **SQLite + アプリ層隔離 (C1)** | ❌ 全停止 | ✅ 投信のみ停止 | ✅ 依存 bucket のみ停止 | ❌ 全停止 |
| **PG + schema 分離 + アプリ層隔離 (推奨)** | ⚠️ schema 単位 restore 可 | ✅ 投信のみ停止 | ✅ 依存 bucket のみ停止 | ✅ schema 別 migration |
| **PG + 複数インスタンス (C4 + 物理分離)** | ✅ 投信インスタンスのみ復旧 | ✅ 完全隔離 | ✅ 依存 bucket のみ停止 | ✅ インスタンス別 migration |
| **マイクロサービス (C5)** | ✅ サービス単位復旧 | ✅ 完全隔離 | ✅ 依存サービスのみ停止 | ✅ サービス別 migration |

凡例: ✅ = 解決 / ⚠️ = 部分的 / ❌ = 解決せず

### 7.1 マトリクスからの示唆

- **DB 層だけ** (SQLite ファイル分離) では F2/F3 を解けない → アプリ層対策が必須
- **アプリ層だけ** (try/except) では F1/F4 を解けない → DB 層対策も必要
- **両方やる** のが正解。**PG schema + アプリ層 try/except** が最もコスト効率良い

---

## 8. 業界パターン参考 (簡易メモ)

### 8.1 Bulkhead Pattern (Microsoft / Hystrix)
船の隔壁のように、リソース (スレッドプール・接続プール) を機能別に分割し、ある機能の障害が他に伝播しないようにする。今回の Topic C C1〜C4 はすべて Bulkhead の実装バリエーション。

### 8.2 Saga Pattern
分散トランザクションの代替。長期トランザクションを「ローカルトランザクション + 補償アクション」の連鎖で表現。Profit Sweep を SQLite 複数ファイルでやるなら Saga 風実装になる。

### 8.3 Outbox Pattern
DB トランザクション内で「やるべきこと」をテーブルに記録し、別 worker が実行。`status='PENDING'` 方式 (Topic A 案 A2/A3) はこの簡易版。

### 8.4 Event Sourcing
状態を「現在値」ではなく「全変更イベント」で保存。bucket 別 projection (材料化ビュー) を作成。監査要件が厳しい金融系で採用例多い (Goldman Sachs SecDB 等)。本件には overkill。

### 8.5 Database-per-Service (Microservices)
各サービスが独自 DB を持つ原則。サービス間データ共有は API 経由のみ。本プロジェクトは単一ユーザー・単一 PC なのでオーバースペック。

### 8.6 Schema-per-Tenant (Multi-tenant SaaS)
PostgreSQL の schema 機能で論理分離。バックアップ・migration を tenant 単位で分離可能。**本件の bucket = tenant とみなせば直接的に適用可能**。

---

## 9. 推奨パスと意思決定ツリー

```
Q1: 本件は Phase 4 (core/ 本実装) と並行で進められる規模か?
├─ Yes → Topic D1 採用 (PG 移行 + 本件統合)
│        └─ Q2 へ
└─ No  → Topic D2 (SQLite で暫定実装、PG は後)
         └─ A2 + B4 + C1 (SQLite 複数ファイル + 冪等書込 + アプリ隔離)
            ※ ただし PG 移行時に作り直しになる無駄あり

Q2: PG 上の物理構成は?
├─ A4 + B3: 1 インスタンス + bucket 別 schema (推奨)
│           └─ アプリ層は C1/C3
│              障害カバレッジ: F1⚠️ F2✅ F3✅ F4✅
└─ A5 + 複数 DB: 1 インスタンス + bucket 別 DB
                 └─ 2PC 必須、運用罠多い、避ける

Q3: アプリ層構造は?
├─ C1: 単一プロセス + try/except (最小工数、推奨)
├─ C3: asyncio task (現状 APScheduler と統合しやすい)
└─ C4: 複数プロセス (F1 もカバーしたい場合のみ)
```

### 9.1 第一推奨 (バランス案)

```
Topic A: A4 (PostgreSQL + 跨schema トランザクション)
Topic B: B3 (bucket 別 schema = common / passive / long_solid / long_growth / short)
        + cash は common.cash_pool_status に統合 (独立 schema 不要)
Topic C: C1 (単一プロセス + bucket 別 try/except + bucket 別キルスイッチ)
Topic D: D1 (Phase 4 と統合して PG 移行を前倒し)
```

カバレッジ: F1⚠️ (schema 単位 restore 可) / F2✅ / F3✅ / F4✅

### 9.2 軽量案 (PG 環境構築を避けたい場合)

```
Topic A: A1 (SQLite 単一ファイル維持)
Topic B: B1 (discriminator 維持) + 新 Position_Holdings テーブル追加
Topic C: C1 (単一プロセス + bucket 別 try/except + bucket 別キルスイッチ)
Topic D: D2 (PG 移行は Phase 4 で別途)
```

カバレッジ: F1❌ / F2✅ / F3✅ / F4❌

### 9.3 究極案 (オーバースペックだが最強隔離)

```
Topic A: A4 (PostgreSQL 跨 schema)
Topic B: B3 (bucket 別 schema)
Topic C: C5 (マイクロサービス 5 コンテナ)
Topic D: D1 (Phase 4 統合)
```

カバレッジ: F1✅ / F2✅ / F3✅ / F4✅
要件 §A.2 (HW 冗長化対象外) と矛盾。**採用非推奨**。

---

## 10. 未決事項 (Open Questions) — **全件 Closed (2026-05-09)**

| # | 質問 | Status | 決定 |
| --- | --- | --- | --- |
| OQ-1 | Phase 4 と統合して PG 移行を前倒しするか? | **Closed** | **No** — Phase 8 へ持ち越し (Tick Data 本格化と統合) |
| OQ-2 | PG 物理構成は schema 分離 or 複数 DB or 複数インスタンス? | **Closed** | **schema-per-bucket (B3)** — 移行時に採用 |
| OQ-3 | アプリ層は単一プロセス + try/except (C1) で十分か? | **Closed** | **Yes (C1)** — Phase 4/5 で既に kill_switch / scheduler の try/except が稼働 |
| OQ-4 | bucket 別キルスイッチを `User_Settings` に追加するか? | **Closed** | **Yes** — Phase 6 で User_Settings に列追加 |
| OQ-5 | cash は独立 bucket とせず `Cash_Pool_Status` 統合で OK か? | **Closed** | **Yes (統合維持)** — 値1個で表現可能・独立テーブル不要 |
| OQ-6 | F1 (DB 破損) への対策はバックアップ運用で十分か? | **Closed** | **Yes** — 要件 §C.1 既定の SQLite 日次コピー + bucket 別 JSON 論理 dump で十分 |
| OQ-7 | 本件の Decisions Log 移管先は? | **Closed** | **taskboard.md を新規作成** (各所が参照しているが未作成だった) |

---

## 11. Decisions (確定)

### 11.1 短期 (Phase 6 まで継続)
- **DB エンジン**: SQLite 維持。Phase 6 (SRE/Operations) は SQLite で完結可能。
- **bucket 構造**: 既存の `category` enum (Passive / Long_Solid / Long_Growth / Short) による論理分離を維持。物理分離はしない。
- **障害隔離**: アプリ層 try/except (C1) で実現。bucket 別キルスイッチを User_Settings に追加 (Phase 6 で実装):
  - `is_kill_switch_active` (全体)
  - `is_kill_switch_active_passive` / `_long_solid` / `_long_growth` / `_short`
- **cash bucket**: `Cash_Pool_Status` 統合維持。独立化しない。
- **DB 破損対策**: 日次 `data.db` コピー + 週次 bucket 別 JSON 論理 dump。

### 11.2 中期 (Phase 7+)
- Tick Data Pipeline ([ADR-0008](../adr/0008-tick-data-pipeline-decisions.md)) を **SQLite + 日次 partition で先行実装**。
- ボリュームが PG/TimescaleDB を要求するスケールに到達した時点で次フェーズへ。

### 11.3 長期 (Phase 8 想定)
- **PG 移行 + schema-per-bucket** を Tick Data 本格化と同時実施:
  - `common` schema: User_Settings / Asset_Master / Daily_Price_History / Financial_Reports / Market_Context / Target_Portfolio / System_Logs / Report_Archive / Cash_Pool_Status / Daily_Asset_Snapshot
  - bucket 別 schema は **Trade_Logs を category 別に分割** (passive / long_solid / long_growth / short の各 schema に Trade_Logs テーブル)
  - Profit Sweep は跨 schema トランザクションで真の ACID
  - Tick Data は TimescaleDB hypertable へ移行 (`common.market_ticks`)

詳細は [ADR-0007](../adr/0007-db-bucket-isolation-decisions.md) 参照。

---

## 12. Phase 6 でのフォローアップ

| 項目 | 内容 |
| --- | --- |
| bucket 別キルスイッチ | User_Settings 列追加 + assert_inactive_for_entry の bucket 引数対応 + テスト |
| バックアップ運用 | `scripts/backup_db.py` 作成 (日次 data.db コピー世代管理 + 週次 JSON 論理 dump) |
| 障害対応プロトコル | bucket 単位の停止判断・解除前チェックリスト (IR-1 と統合) |

---

## 改訂履歴

| Date | Who | Change |
| --- | --- | --- |
| 2026-05-09 | Claude | 初版作成。Topic A/B/C/D 整理・障害シナリオマトリクス・推奨パス3案を提示 |
| 2026-05-09 | Claude | OQ-1〜7 全件クローズ・Status を Decided に変更・§11 Decisions (確定) と §12 Phase 6 でのフォローアップを追加。ADR-0007 起草 |
