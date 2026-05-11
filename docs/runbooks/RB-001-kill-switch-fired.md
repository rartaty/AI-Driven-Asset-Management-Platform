# RB-001: Kill Switch が発火した

> **Severity**: High | **Category**: F2 (ロジックバグ) or F1 (DB 破損) or 真にドローダウン

## 1. 症状
- Discord に `🚨 [AUTO] kill switch activated. reason: ...` または `🚨 [AUTO] kill switch activated for bucket=...`
- 新規エントリー全件が `KillSwitchError` でブロック
- システムは継続稼働するが買付不能 (決済は許可・非対称オーバーライド)

## 2. 影響範囲
- 全体発火: 全 bucket の新規エントリーがブロック
- bucket 別発火: 該当 bucket のみ
- 既存ポジションの決済は影響なし

## 3. 検知方法
```sql
-- Kill Switch 状態
SELECT * FROM user_settings WHERE id=1;

-- 直近 1 時間の発動ログ
SELECT timestamp, level, component, event, payload FROM system_logs
 WHERE component='[KillSwitch]' AND timestamp > datetime('now', '-1 hour')
 ORDER BY timestamp DESC;
```

## 4. 確認手順
- [ ] **発動理由を確認** — System_Logs の payload `reason` フィールド
- [ ] **ドローダウン由来か?** — Daily_Asset_Snapshot で peak vs 現在値の比率
  ```sql
  SELECT date, bank_balance, buying_power, trust_value, long_solid_value, long_growth_value, short_term_market_value FROM daily_asset_snapshot ORDER BY date DESC LIMIT 30;
  ```
- [ ] **生活防衛費由来か?** — `bank_balance < LIVING_EXPENSES_THRESHOLD` (デフォルト 1,000,000)
- [ ] **ロジックバグ由来か?** — 直前のリリース変更履歴を確認

## 5. 解消手順
1. **真の DD なら**: 待機。市場回復を観測。手動解除しない。
2. **生活防衛費由来なら**: 銀行残高を補充するまで解除しない。
3. **ロジックバグ由来なら**:
   - 該当ロジックを修正 → テスト → デプロイ
   - **修正後に手動解除** (§6 解除確認)

## 6. 解除確認 (CLAUDE.md 絶対禁止 3 整合)

[INCIDENT_RESPONSE.md §4](../operations/INCIDENT_RESPONSE.md) のチェックリスト全件 ✅ 後に:
```python
from core.kill_switch import deactivate, deactivate_bucket, DEACTIVATE_CONFIRMATION_PHRASE

# 全体解除
deactivate(session, reason="<incident-id> 修復完了 + IR §4.1 全項目 OK", confirmation=DEACTIVATE_CONFIRMATION_PHRASE)

# bucket 別解除
deactivate_bucket(session, bucket="Short", reason="...", confirmation=DEACTIVATE_CONFIRMATION_PHRASE)
```

## 7. エスカレーション
- 連続発火 (1 日 3 回以上) → ロジックバグ強疑い → コード review + ポストモーテム必須
- 解除直後の再発火 → 修復が不完全 → IR §4 の経過観察延長

## 関連
- [INCIDENT_RESPONSE.md §4](../operations/INCIDENT_RESPONSE.md)
- [docs/operations/KILL_SWITCH_V2_SPEC.md](../operations/KILL_SWITCH_V2_SPEC.md)
- ADR-0007 OQ-4 (bucket 別キルスイッチ)