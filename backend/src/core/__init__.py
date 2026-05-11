"""
core/ — 共通基盤層 (Cross-cutting Infrastructure)

- aws_ssm: AWS SSM Parameter Store からの秘匿情報取得
- discord: Discord Webhook 経由の通知
- logger: python-json-logger 構造化ログ設定
- (将来) kill_switch / rate_limiter / object_pool

参照: docs/infrastructure/DATA_FOUNDATION.md §5〜§7
"""
