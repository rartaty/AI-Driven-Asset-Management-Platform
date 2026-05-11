# RB-004: Discord 通知が届かない

> **Severity**: Med | **Category**: F3 (外部 API 障害)

## 1. 症状
- Discord に通知が届かない (Push アプリ / Web どちらも)
- ログに `[Notify] failed to resolve webhook URL` / `[Notify] HTTP error: 401` 等
- メイン処理は動いている (非ブロッキング設計のため)

## 2. 影響範囲
- 通知のみ。トレード本体は継続稼働 (Phase 4 の `core/discord.py` 非ブロッキング設計)
- ただし**障害検知が遅延する** (障害の二次被害化リスク)

## 3. 検知方法
```sql
SELECT timestamp, event FROM system_logs
 WHERE component='[Notify]' OR event LIKE '%webhook%'
 ORDER BY timestamp DESC LIMIT 20;
```
- Discord アプリ側: チャンネル `#alerts` / `#trading` / `#system` の最終受信日時を確認

## 4. 確認手順
- [ ] **Webhook URL の有効性**:
  ```powershell
  $url = aws ssm get-parameter --name /projectbig/discord/webhook-system --with-decryption --query Parameter.Value --output text
  Invoke-RestMethod -Uri $url -Method POST -Body (@{content="ping"} | ConvertTo-Json) -ContentType "application/json"
  ```
- [ ] **Discord サーバ側で webhook を削除していないか** (誤操作 / セキュリティ理由)
- [ ] **SSM パラメータが存在するか** ([RB-003](RB-003-aws-ssm-failure.md))
- [ ] **Discord ステータスページ**: https://discordstatus.com/

## 5. 解消手順
1. **Webhook 失効**: Discord サーバ → チャンネル設定 → 統合 → 新規 Webhook 作成 → SSM 更新
2. **SSM 不整合**: `scripts/register_secrets.py` で再登録
3. **Discord 障害**: 待機 (代替通知手段はないので障害検知だけログ依存)

## 6. エスカレーション
- Discord 障害が長期化: 一時的にローカルログ + ポーリング監視 (Frontend dashboard) で代用
- アカウント BAN / 認証問題: Discord サポート

## 関連
- [backend/src/core/discord.py](../../backend/src/core/discord.py)
- [.claude/agents/discord-notify-validator.md](../../.claude/agents/discord-notify-validator.md)