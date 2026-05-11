"""
三菱UFJ eスマート証券 (旧auカブコム証券) API 連携クライアント
製品名: kabuステーション API (kabusapi)
公式仕様 (v1.9.0) 準拠 — backend/src/api/specs/kabu_STATION_API.yaml 参照

認証情報 (KABUCOM_API_PASSWORD) は AWS SSM 経由で取得 (要件 E.6.1, ADR-0003)。
core/secrets.py の get_secret() 経由で SSM パラメータを動的解決。
非機密設定 (KABUCOM_API_URL, TRADE_MODE) は環境変数経由で OK。

エラーハンドリング (要件 §6 / Phase 5 P5):
- 全エラーは [API:Kabucom] プレフィクス付きで logger.error に出力
- 重要障害は notify_system 経由で Discord 通知 (非ブロッキング)
"""

import logging
import os
import requests
from typing import Dict, Any, List

from core.secrets import get_secret
from core.discord import notify_system

logger = logging.getLogger(__name__)


class KabucomAPIClient:
    def __init__(self):
        # 非機密設定は環境変数経由 (URL / TRADE_MODE)
        self.base_url = os.getenv("KABUCOM_API_URL", "http://localhost:8080/kabusapi")
        try:
            self.password = get_secret("KABUCOM_API_PASSWORD")
        except Exception as e:
            logger.error(f"[API:Kabucom] Failed to resolve KABUCOM_API_PASSWORD via SSM/env: {e}")
            notify_system(
                f"Kabucom API password unresolved: {e}",
                component="API:Kabucom",
            )
            self.password = ""
        # TRADE_MODE=PAPER のとき API はダミーデータを返却 (要件 §3 / Phase 5: TEST_MODE deprecate)
        self.test_mode = os.getenv("TRADE_MODE", "PAPER").upper() == "PAPER"
        self.token = None

    def authenticate(self) -> bool:
        """
        APIパスワードを使用してトークンを取得する
        """
        if self.test_mode:
            self.token = "dummy_token_for_test_mode"
            return True

        if not self.password:
            raise ValueError(
                "KABUCOM_API_PASSWORD is not set "
                "(SSM path '/projectbig/kabucom/api-password' or environment variable)"
            )

        try:
            url = f"{self.base_url}/token"
            data = {"apipassword": self.password}
            response = requests.post(url, json=data, timeout=5)
            response.raise_for_status()

            self.token = response.json().get("Token")
            return bool(self.token)
        except Exception as e:
            logger.error(f"[API:Kabucom] Authentication failed: {e}")
            notify_system(f"Kabucom authentication failed: {e}", component="API:Kabucom")
            return False

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        保有残高（ポジション）一覧を取得する
        """
        if self.test_mode:
            # 安全装置: 通信せずにダミーデータを返す
            return [
                {"Symbol": "7203", "Name": "トヨタ自動車", "LeavesQty": 100, "Price": 3000, "CurrentPrice": 3100},
                {"Symbol": "8306", "Name": "三菱UFJ", "LeavesQty": 500, "Price": 1200, "CurrentPrice": 1180}
            ]

        if not self.token and not self.authenticate():
            raise ConnectionError("Failed to authenticate with Kabucom API")

        try:
            url = f"{self.base_url}/positions"
            headers = {"X-API-KEY": self.token}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"[API:Kabucom] get_positions failed: {e}")
            notify_system(f"Kabucom get_positions failed: {e}", component="API:Kabucom")
            return []

    def get_cash_balance(self) -> float:
        """
        買付余力（現金残高）を取得する
        """
        if self.test_mode:
            return 150000.0

        if not self.token and not self.authenticate():
            raise ConnectionError("Failed to authenticate with Kabucom API")

        # 実際には/wallet/cash 等の余力取得APIを叩く
        try:
            url = f"{self.base_url}/wallet/cash"
            headers = {"X-API-KEY": self.token}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return float(response.json().get("Cash", 0))
        except Exception as e:
            logger.error(f"[API:Kabucom] get_cash_balance failed: {e}")
            notify_system(f"Kabucom get_cash_balance failed: {e}", component="API:Kabucom")
            return 0.0

    def get_index(self, symbol: str) -> float:
        """
        指数データ（VIX等）を取得する。
        kabuStation APIの /board/{symbol} を想定。
        """
        if self.test_mode:
            if "VIX" in symbol.upper():
                return 18.5  # テスト用ダミー値
            return 0.0

        if not self.token and not self.authenticate():
            return 0.0

        try:
            url = f"{self.base_url}/board/{symbol}"
            headers = {"X-API-KEY": self.token}
            response = requests.get(url, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            return float(data.get("CurrentPrice", 0.0))
        except Exception as e:
            logger.error(f"[API:Kabucom] get_index failed for {symbol}: {e}")
            # 指数取得は VIX 取得の補助路で頻度高 → 通知抑制 (logger のみ)
            return 0.0
