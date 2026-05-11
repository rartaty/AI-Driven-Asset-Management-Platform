"""
銀行API連携クライアント (OpenCanvas / AnserParasol v1.1.0 準拠)
OIDC (OAuth 2.0) による認可フローと残高照会

認証情報 (BANK_API_CLIENT_ID / BANK_API_CLIENT_SECRET) は AWS SSM 経由で取得 (要件 E.6.1, ADR-0003)。
銀行口座は読取専用扱い (要件 §2.2: 出金機能は意図的に使用しない)。

エラーハンドリング (要件 §6 / Phase 5 P5):
- 全エラーは [API:Bank] プレフィクス付きで logger.error に出力
- 認証・残高取得失敗は notify_system 経由で Discord 通知 (非ブロッキング)
"""

import logging
import os
import requests
from typing import Dict, Any

from core.secrets import get_secret
from core.discord import notify_system

logger = logging.getLogger(__name__)


class OpenCanvasBankAPIClient:
    def __init__(self):
        # 非機密設定は環境変数経由 (URL / TRADE_MODE)
        self.base_url = os.getenv("BANK_API_BASE_URL", "https://api.opencanvas.ne.jp/anserparasol/v1")

        try:
            self.client_id = get_secret("BANK_API_CLIENT_ID")
        except Exception as e:
            logger.error(f"[API:Bank] Failed to resolve BANK_API_CLIENT_ID via SSM/env: {e}")
            notify_system(
                f"Bank API client_id unresolved: {e}",
                component="API:Bank",
            )
            self.client_id = ""

        try:
            self.client_secret = get_secret("BANK_API_CLIENT_SECRET")
        except Exception as e:
            logger.error(f"[API:Bank] Failed to resolve BANK_API_CLIENT_SECRET via SSM/env: {e}")
            notify_system(
                f"Bank API client_secret unresolved: {e}",
                component="API:Bank",
            )
            self.client_secret = ""
        # TRADE_MODE=PAPER のとき API はダミーデータを返却 (要件 §3 / Phase 5: TEST_MODE deprecate)
        self.test_mode = os.getenv("TRADE_MODE", "PAPER").upper() == "PAPER"
        self.access_token = None

    def _fetch_access_token(self) -> bool:
        """
        OIDCのトークンエンドポイントを叩き、アクセストークンを取得する (Client Credentials Flow等)
        """
        if self.test_mode:
            self.access_token = "dummy_bank_access_token_for_test_mode"
            return True

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Bank API Client ID/Secret is not set "
                "(SSM paths '/projectbig/opencanvas/client-id' & '/projectbig/opencanvas/client-secret' "
                "or environment variables)"
            )

        try:
            # ※実際のTokenエンドポイントは仕様書に依存します。ここでは一般的なOAuth2の形を実装。
            token_url = "https://api.opencanvas.ne.jp/oauth/token"
            data = {"grant_type": "client_credentials"}
            auth = (self.client_id, self.client_secret)

            response = requests.post(token_url, data=data, auth=auth, timeout=10)
            response.raise_for_status()

            self.access_token = response.json().get("access_token")
            return bool(self.access_token)
        except Exception as e:
            logger.error(f"[API:Bank] Authentication failed: {e}")
            notify_system(f"Bank API authentication failed: {e}", component="API:Bank")
            return False

    def get_account_balance(self, account_id: str = "default") -> float:
        """
        指定口座の残高を取得する（生活防衛費・投資信託用資金の確認）
        """
        if self.test_mode:
            # 安全装置: 通信せずにダミーの銀行残高を返す
            return 500000.0

        if not self.access_token and not self._fetch_access_token():
            raise ConnectionError("Failed to get Access Token for Bank API")

        try:
            # AnserParasol v1.1.0 準拠の残高照会エンドポイント (例: /accounts/{accountId}/balances)
            url = f"{self.base_url}/accounts/{account_id}/balances"
            headers = {"Authorization": f"Bearer {self.access_token}"}

            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()
            # 仕様に応じてレスポンスの構造から残高（金額）を抽出する
            balance_str = data.get("balance", {}).get("amount", "0")
            return float(balance_str)
        except Exception as e:
            logger.error(f"[API:Bank] get_account_balance failed: {e}")
            notify_system(f"Bank API get_account_balance failed: {e}", component="API:Bank")
            return 0.0
