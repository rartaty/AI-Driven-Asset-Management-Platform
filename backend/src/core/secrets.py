"""
シークレット管理のFacadeモジュール

アプリケーション層（APIクライアント等）からは、このモジュールのみを利用します。
内部で AWS SSM (core.aws_ssm) へのアクセスと、環境変数へのフォールバックを隠蔽します。
"""
import os
import logging
from core import aws_ssm

logger = logging.getLogger(__name__)

# 論理キーとAWS SSMパスのマッピング
_KEY_MAPPING = {
    "KABUCOM_API_PASSWORD": "/projectbig/kabucom/api-password",
    "BANK_API_CLIENT_ID": "/projectbig/opencanvas/client-id",
    "BANK_API_CLIENT_SECRET": "/projectbig/opencanvas/client-secret",
    "DISCORD_WEBHOOK_TRADING": "/projectbig/discord/webhook-trading",
    "DISCORD_WEBHOOK_SYSTEM": "/projectbig/discord/webhook-system",
    "DISCORD_WEBHOOK_ALERTS": "/projectbig/discord/webhook-alerts",
    "EDINET_API_KEY": "/projectbig/edinet/api-key",
    "ANTHROPIC_API_KEY": "/projectbig/anthropic/api-key",
    "ADMIN_API_TOKEN": "/projectbig/admin/api-token",  # Web 管理者トークン (WAF 監査 C3 / 要件 E.5.1)
}

def get_secret(logical_key: str) -> str:
    """
    論理キーを指定してシークレットを取得する。
    1. AWS SSM Parameter Store を試行
    2. 失敗した場合は同名の環境変数からフォールバック取得
    
    :param logical_key: 例) "KABUCOM_API_PASSWORD"
    :return: 取得したシークレット文字列
    :raises ValueError: どちらからも取得できなかった場合 (パスワードやキーが空のまま処理が進まないようにするため、必要なら呼び出し側でハンドリング)
    """
    ssm_path = _KEY_MAPPING.get(logical_key)
    
    if ssm_path:
        try:
            val = aws_ssm.get_secret(ssm_path)
            if val:
                return val
        except Exception as e:
            logger.warning(f"[Secrets] Failed to get '{logical_key}' from SSM path '{ssm_path}': {e}. Falling back to env vars.")
    else:
        logger.warning(f"[Secrets] Unknown logical key '{logical_key}'. Trying env vars directly.")

    # フォールバック処理
    env_val = os.getenv(logical_key, "")
    if env_val:
        return env_val
        
    raise ValueError(f"[Secrets] Could not resolve secret for '{logical_key}' from both SSM and environment variables.")
