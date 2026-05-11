# Incident Response Protocol — IR-1

> **Status**: v1 (2026-05-09 / Phase 6 / ADR-0010 §6-3)
> **Scope**: 個人ローカル運用版。商用 SRE のフルプロセスではなく、**「何のために何を順番にチェックするか」を最小単位で体系化**。
> **対象**: ATSUYA TANAKA (オーナー兼ソロ開発者) — 障害発生時の自分自身向け手順書。
> **関連**: [SLI_SLO.md](SLI_SLO.md), [OBSERVABILITY_STACK.md](OBSERVABILITY_STACK.md), [POSTMORTEM_TEMPLATE.md](POSTMORTEM_TEMPLATE.md), [docs/runbooks/](../runbooks/)

---

## 0. 設計思想

- **障害時に「考える時間ゼロ」** — 手順書通り順番に確認するだけで判断材料が揃う設計。
- **First Hour Response** に集中 — 1 時間で「症状把握 → 影響範囲特定 → 一次対処 (止血) 」まで完了させる。
- **盲動を避ける** — 「とにかく再起動」「とにかくキルスイッチ解除」は禁止。原因仮説 → 検証 → 対処の順を守る。
- **Kill Switch / TRADE_MODE / DB の不可逆操作は確認手順必須** (CLAUDE.md 絶対禁止 1, 2, 3 全件)。

---

## 1. 障害シナリオの 4 大分類

ADR-0007 §1.2 で識別した 4 つのシナリオ。Discord 通知 / 症状から **どれに該当するか** を最初に判断する。

| 分類 | 代表症状 | 影響範囲 | 一次対処 | 詳細 Runbook |
| --- | --- | --- | --- | --- |
| **F1. DB データ破損** | `data.db` 読込不可 / SQLite Error / FK 違反 | システム全体 | バックアップから復元 | [RB-008](../runbooks/RB-008-data-db-corruption.md) |
| **F2. ロジックバグ** | 異常な NaN / 想定外の例外 / 集計ズレ | 該当 bucket 中心 | bucket 別キルスイッチ ON | [RB-001](../runbooks/RB-001-kill-switch-fired.md) / [RB-007](../runbooks/RB-007-market-ticks-anomaly.md) |
| **F3. 外部 API 障害** | kabu / Discord / yfinance / SSM のタイムアウト | 依存 bucket のみ | API 監視 + リトライ | [RB-002](../runbooks/RB-002-kabu-api-down.md) / [RB-003](../runbooks/RB-003-aws-ssm-failure.md) / [RB-004](../runbooks/RB-004-discord-down.md) / [RB-010](../runbooks/RB-010-websocket-loop.md) |
| **F4. スキーマ migration 障害** | ALTER TABLE 失敗 / 起動時例外 | システム全体 | rollback or 手動修復 | (本番化前は data.db 削除で対応可) |

---

## 2. First Hour Response — 最初の 60 分でやること

時系列で必ず順番通り。

### Step 1 (0-5 分): 症状把握
- [ ] **Discord 通知を見る** — 一番情報が早い。`[COMPONENT]` プレフィクスから障害層を特定。
  - `[KillSwitch]` → F2 系の可能性 (or 真にドローダウン発生)
  - `[API:Kabucom]` / `[API:Bank]` → F3 系
  - `[DB]` / `[Scheduler] portfolio sync failed` → F1 or F2
  - `[KabuPush]` Disconnected → F3 (WebSocket 切断ループの可能性 → RB-010)
  - `[MarketData] flush_layer2 failed` → F1 (DB 書込不可)
- [ ] **System_Logs テーブル** を直近 1 時間で query → 同種エラーの頻度を確認
  ```sql
  SELECT timestamp, level, component, event FROM system_logs
   WHERE timestamp > datetime('now', '-1 hour')
   ORDER BY timestamp DESC LIMIT 50;
  ```

### Step 2 (5-15 分): 影響範囲特定
- [ ] **どの bucket が止まっているか** を確認:
  ```sql
  SELECT
    is_kill_switch_active,
    is_kill_switch_active_passive,
    is_kill_switch_active_long_solid,
    is_kill_switch_active_long_growth,
    is_kill_switch_active_short
  FROM user_settings WHERE id=1;
  ```
- [ ] **取引中のポジション** を確認 (PAPER モードなら paper_trader.positions / REAL なら kabucom.get_positions):
  - 想定外のポジションが残っていないか
  - 未決済のポジションがあるか (Profit Sweep が完遂しなかった証拠)
- [ ] **Daily_Asset_Snapshot** で資産推移を確認 — 直近 7 日と比較して異常値がないか

### Step 3 (15-30 分): 一次対処 (止血)
影響を「これ以上広げない」ことが目標。原因究明はこの後。

- [ ] **Kill Switch が必要か判断**:
  - F1 (DB 破損) → 全体キルスイッチ ON 推奨
  - F2 (ロジックバグ) → 該当 bucket だけキルスイッチ ON
  - F3 (API 障害) → 依存 bucket のキルスイッチ ON (kabu 落ちなら全 bucket)
  - F4 (migration 障害) → プロセス停止 (Podman: `podman compose down`)
- [ ] **Kill Switch ON のコード経路** (CLAUDE.md 絶対禁止 3 整合 — ON は誰でも可能、OFF は明示確認必須):
  ```python
  from core.kill_switch import activate, activate_bucket
  # 全体停止
  activate(session, reason="<incident-id> <症状>", manual=True)
  # bucket 別
  activate_bucket(session, bucket="Short", reason="...", manual=True)
  ```

### Step 4 (30-60 分): 原因仮説と検証
- [ ] **エラーログを time series で並べる** — 最初に落ちたのはどのコンポーネントか
- [ ] **依存関係を辿る** — 直接エラーの原因ではなく、上流で何が起きたかを見る
  - 例: `[Scheduler] portfolio sync failed` の原因 → `[API:Kabucom] get_positions failed` の原因 → kabu Station プロセス停止
- [ ] **仮説を立て検証** (修復前に必ず)
  - DB 破損疑い → `sqlite3 data.db "PRAGMA integrity_check;"` で確認
  - API 疑い → 直接 endpoint へ手動リクエスト (kabu: localhost:18080/kabusapi/...)
  - SSM 疑い → `aws ssm get-parameter --name /projectbig/...` で手動取得確認
- [ ] **修復実行は確認手順を経た後に**:
  - DB 復元 → [RB-008](../runbooks/RB-008-data-db-corruption.md)
  - Kill Switch 解除 → §4 解除前チェックリスト

---

## 3. Discord 通知から推定可能な障害種別フロー

```
Discord 通知受信
        ↓
    プレフィクス確認
        ↓
┌───────────────┬───────────────┬───────────────┬───────────────┐
│ [KillSwitch]  │ [API:*]       │ [Scheduler]   │ [MarketData]  │
│ (F2/F1)       │ (F3)          │ (F1/F2/F3)    │ (F1/F2)       │
└───────────────┴───────────────┴───────────────┴───────────────┘
        ↓                ↓                ↓                ↓
   ドローダウン     kabu/SSM/Discord   どの job 失敗?   tick 異常?
   Living費 不足?    自動再接続中?      sync_portfolio   delta_volume<0
   解除前確認        RB-002 / 003 /     → portfolio_sync  → Push 不安定
   (§4)              004 / 010          → DB? API? logic? → RB-007
        ↓                ↓                ↓                ↓
   §4 確認後解除    Runbook 該当       Runbook 該当       Runbook 該当
```

---

## 4. Kill Switch 解除前チェックリスト

> ⚠️ CLAUDE.md 絶対禁止 3: **キルスイッチ無断解除禁止**。本セクションは解除手順の「権限付与」ではなく「**満たさなければ解除してはいけない条件**」の一覧。

### 4.1 必須前提 (全件 ✅ になるまで解除しない)

- [ ] **発動原因の特定が完了している** (推定ではなく証拠ベース)
- [ ] **症状が再発しないことが確認できている** (修復後の検証テスト or 様子見観測 30 分以上)
- [ ] **Daily_Asset_Snapshot の現在値が peak から -3% 以内** (ドローダウン回復確認)
- [ ] **生活防衛費 (`bank_balance`) が `LIVING_EXPENSES_THRESHOLD` 以上** ※ M2 連動
- [ ] **Profit Sweep / Quarterly Review 等の不整合が残っていない**
- [ ] **Discord 過去 1 時間の `[CRITICAL]` / `[ERROR]` 通知がゼロ** (関連コンポーネント)
- [ ] **TRADE_MODE が PAPER である** (REAL モードでの解除は **特に慎重に**、CLAUDE.md 絶対禁止 1 整合)

### 4.2 解除実行 (確認文字列必須)

```python
from core.kill_switch import deactivate, deactivate_bucket, DEACTIVATE_CONFIRMATION_PHRASE

# 全体解除
deactivate(
    session,
    reason="<incident-id> 修復完了 + §4.1 全項目確認済 (timestamp)",
    confirmation=DEACTIVATE_CONFIRMATION_PHRASE,
)

# bucket 別解除
deactivate_bucket(
    session, bucket="Short",
    reason="...",
    confirmation=DEACTIVATE_CONFIRMATION_PHRASE,
)
```

### 4.3 解除後の経過観察
- 解除直後の 30 分: スケジューラジョブが正常完了するか
- 解除後 1 時間: 想定外のポジションが取られていないか
- 解除後 1 日: Daily_Asset_Snapshot の異常値発生がないか

---

## 5. ロールバック判断基準

「直前のリリース / 設定変更が原因かもしれない」という疑いが出た時、**rollback すべきか継続調査すべきか** の判断軸。

### 5.1 即時 rollback すべき条件 (これらに該当したら速攻 rollback)
- 直前変更でスキーマ migration が失敗 (起動時例外連発)
- 直前変更で **資金が誤った bucket に振り替えられた** (Profit Sweep 等の金銭フロー破壊)
- 直前変更で kill switch / 認証 / SSM 系が動かない (安全装置の損傷)

### 5.2 継続調査して問題なら rollback する条件
- 戦略シグナル発火率が異常に高い / 低い (ロジック誤動作の可能性)
- VIX gear / Target_Portfolio の発火タイミングが想定外
- Layer 1 deque の tick が空になる頻度が高い

### 5.3 rollback ステップ
1. **現状を保存** — 修復後の検証用に「壊れた状態」のスナップショットを取る:
   - `cp backend/src/data.db backups/incident-<ID>-data.db`
   - System_Logs を CSV エクスポート
2. **直前の安定バージョンに戻す** — git 化前 (本プロジェクト 2026-05-09 時点で git 未導入) は 手動 file コピー
3. **TRADE_MODE=PAPER 確認**
4. **Kill Switch 全体 ON** で動作確認 (発注を物理的に止めた状態)
5. **数時間観察** → 問題再発しないこと確認 → §4 解除手順

---

## 6. ポストモーテム作成

修復完了後、必ず [POSTMORTEM_TEMPLATE.md](POSTMORTEM_TEMPLATE.md) に基づきポストモーテムを書く。
個人運用なので簡素化: **5 Whys + 再発防止策の 1 枚物** で OK。

ポストモーテム未作成の障害は学びにならない (CLAUDE.md 不可逆操作 §作業終了時のチェック整合)。

---

## 7. エスカレーション (個人運用なので限定的)

- **AWS 課金関連** → AWS Console / Billing Alert (個人アカウント)
- **金融機関 API ロックアウト** → kabu証券サポート / OpenCanvas サポート (営業時間内のみ)
- **法務疑義** (要件 §9 Disclaimer 関連) → 自己判断 / 場合により法律相談

---

## 8. 改訂履歴

| Date | Who | Change |
| --- | --- | --- |
| 2026-05-09 | Claude | 初版作成 (Phase 6 / ADR-0010 §6-3 / IR-1) |
