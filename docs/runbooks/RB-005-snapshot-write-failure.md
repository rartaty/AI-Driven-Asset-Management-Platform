# RB-005: Daily_Asset_Snapshot 書込失敗

> **Severity**: High | **Category**: F1 (DB 破損) or F2 (ロジックバグ)

## 1. 症状
- Discord: `[Scheduler] portfolio sync failed: <reason>`
- `Daily_Asset_Snapshot` テーブルに当日レコードがない
- Frontend dashboard の chart_data が古い日付で止まっている

## 2. 影響範囲
- Frontend 表示精度低下
- 翌日のドローダウン計算が空履歴で動作 (Kill Switch 無効化リスク)
- 週次 / 四半期見直しの判断材料欠落

## 3. 検知方法
```sql
-- 当日のレコード有無
SELECT date FROM daily_asset_snapshot WHERE date = date('now');

-- 直近 7 日の連続性確認
SELECT date FROM daily_asset_snapshot WHERE date >= date('now', '-7 days') ORDER BY date;
```
直近のエラーログ:
```sql
SELECT timestamp, event FROM system_logs
 WHERE event LIKE '%portfolio sync failed%' OR event LIKE '%write_daily_snapshot%'
 ORDER BY timestamp DESC LIMIT 10;
```

## 4. 確認手順
- [ ] **DB アクセス可能か** ([RB-008](RB-008-data-db-corruption.md))
- [ ] **kabu / OpenCanvas API 可能か** ([RB-002](RB-002-kabu-api-down.md), [RB-003](RB-003-aws-ssm-failure.md))
- [ ] **`portfolio_sync.py` の最近の変更** — Phase 5 P11 でスキーマ変更があったため類似ロジック確認
- [ ] **disk full / 書込権限 不足** — `data.db` ファイル属性確認

## 5. 解消手順
1. 上流原因 (DB / API) を解消
2. 手動で `job_sync_portfolio` 再実行 — `POST /api/v1/system/test/profit_sweep` ではなく **scheduler.system_scheduler.job_sync_portfolio()** を REPL/Notebook から
3. 過去日の欠損は **遡及計算しない** (POST 設計上正確に再現できない)
4. 連続欠損 (3 日以上) は週次 export からの再構築検討

## 6. エスカレーション
- DB 破損が原因なら [RB-008](RB-008-data-db-corruption.md)
- Logic 由来 → ロジック修正 → リグレッションテスト → ポストモーテム

## 関連
- [backend/src/services/portfolio_sync.py](../../backend/src/services/portfolio_sync.py)
- [backend/src/services/scheduler.py](../../backend/src/services/scheduler.py) `job_sync_portfolio`
- ADR-0006 (Phase 5 P11)