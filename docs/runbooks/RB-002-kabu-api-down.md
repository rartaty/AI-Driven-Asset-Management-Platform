# RB-002: kabu Station API が応答しない

> **Severity**: High | **Category**: F3 (外部 API 障害)

## 1. 症状
- Discord: `[API:Kabucom] Authentication failed` / `get_positions failed` / `get_cash_balance failed`
- Discord: `[KabuPush] WebSocket disconnected` (REAL モード)
- `KabucomAPIClient.authenticate()` が False を返す or タイムアウト
- ポートフォリオ同期が空 dict を返す

## 2. 影響範囲
- REAL モード: 全 bucket の発注・残高取得が機能しない
- PAPER モード: 影響なし (yfinance pump は独立稼働)

## 3. 検知方法
```sql
SELECT timestamp, event FROM system_logs
 WHERE component LIKE '[API:Kabucom]%' OR component='[KabuPush]'
 ORDER BY timestamp DESC LIMIT 20;
```

## 4. 確認手順
- [ ] **kabu Station デスクトップアプリが起動しているか** (Windows トレイアイコン確認)
- [ ] **localhost:18080 へ ping** — `Test-NetConnection localhost -Port 18080`
- [ ] **手動でトークン取得試行**:
  ```powershell
  $body = @{ APIPassword = $env:KABUCOM_API_PASSWORD } | ConvertTo-Json
  Invoke-RestMethod -Uri "http://localhost:18080/kabusapi/token" -Method POST -Body $body -ContentType "application/json"
  ```
- [ ] **kabu 側のメンテナンス時間でないか** (証券会社サイトで確認)

## 5. 解消手順
1. kabu Station アプリの再起動 (Windows トレイ → 終了 → 再起動)
2. 再起動後に `KabucomAPIClient.authenticate()` を手動実行
3. WebSocket 切断ループは [RB-010](RB-010-websocket-loop.md) を参照
4. 復旧確認後、必要なら bucket 別キルスイッチを解除 ([RB-001](RB-001-kill-switch-fired.md))

## 6. エスカレーション
- メンテナンス時間: 待機のみ
- アプリ再起動でも復旧しない場合: kabu証券サポート (営業時間内のみ)
- API パスワード変更後: SSM `/projectbig/kabucom/api-password` を更新 → `scripts/register_secrets.py` 実行

## 関連
- [INCIDENT_RESPONSE.md §1 F3](../operations/INCIDENT_RESPONSE.md)
- [backend/src/api/kabucom.py](../../backend/src/api/kabucom.py)
- [backend/src/services/kabu_push_client.py](../../backend/src/services/kabu_push_client.py)
- ADR-0008 (Tick Data 設計決定)