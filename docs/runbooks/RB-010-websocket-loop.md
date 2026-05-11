# RB-010: WebSocket 切断ループ (再接続失敗連発)

> **Severity**: High | **Category**: F3 (外部 API 障害)

## 1. 症状
- Discord: `[KabuPush] WebSocket disconnected (code=...)` が連続発火
- ログに `[KabuPush] Reconnecting in 60.0s (attempt N)` が頻発 (N が大きい)
- Layer 1 deque に新規 tick が積まれない
- 短期戦略のシグナル発火が完全停止

## 2. 影響範囲
- REAL モード短期トレード全停止
- PAPER モード: 影響なし (yfinance pump で代替経路稼働)

## 3. 検知方法
```sql
SELECT timestamp, event FROM system_logs
 WHERE component='[KabuPush]' AND event LIKE '%disconnect%'
 ORDER BY timestamp DESC LIMIT 30;

-- 切断頻度
SELECT COUNT(*) FROM system_logs
 WHERE component='[KabuPush]' AND event LIKE '%disconnect%'
   AND timestamp > datetime('now', '-1 hour');
```

## 4. 確認手順
- [ ] **kabu Station アプリの稼働確認** ([RB-002](RB-002-kabu-api-down.md))
- [ ] **WebSocket URL が正しいか** — `KABUCOM_WS_URL` 環境変数 (デフォルト `ws://localhost:18080/kabusapi/websocket`)
- [ ] **トークン有効期限** — kabu トークンは時間経過で失効 (`KabucomAPIClient.authenticate()` で再取得)
- [ ] **再接続バックオフが上限張り付きか** — 60 秒に到達していたら明らかに長期障害
- [ ] **ネットワーク** — ローカルなのでほぼ問題ないが、Windows ファイアウォール / VPN の影響を確認

## 5. 解消手順
1. **kabu Station アプリ再起動** (一番効くケースが多い)
2. **`KabuPushClient` の手動 stop/start**:
   ```python
   from services.kabu_push_client import get_client, reset_client
   reset_client()
   client = get_client()
   client.start()
   ```
3. **トークン再取得後に再接続**:
   ```python
   from api.kabucom import KabucomAPIClient
   c = KabucomAPIClient()
   c.authenticate()
   ```
4. **緊急止血**: 短期 bucket キルスイッチ ON ([RB-001](RB-001-kill-switch-fired.md))
5. **gap 検出後の REST 補完** — Phase 7 設計の `is_synthetic=True` 自動補完を確認

## 6. エスカレーション
- kabu Station アプリのバージョン非互換 → kabu証券サポート
- WebSocket プロトコル変更 → 公式仕様 (kabu_STATION_API.yaml) 再確認
- TLS / 証明書問題 (kabu Push が将来 wss:// に変わる場合) → websocket-client ライブラリの ssl 設定

## 関連
- [backend/src/services/kabu_push_client.py](../../backend/src/services/kabu_push_client.py)
- [backend/src/api/specs/kabu_STATION_API.yaml](../../backend/src/api/specs/kabu_STATION_API.yaml)
- ADR-0008 (Tick Data 設計決定) / ADR-0009 (Phase 7 スコープ)