"""
AWS SSM Parameter Store クライアント (秘匿情報取得)

参照仕様: docs/infrastructure/DATA_FOUNDATION.md §5
関連 ADR: docs/adr/0003-aws-ssm-standard-tier.md (SSM Standard tier 採用)
要件: REQUIREMENTS_DEFINITION.md §E.6.1 (シークレット動的取得必須)

設計方針:
- すべての API キー・トークン・Webhook URL は SSM Parameter Store (KMS暗号化) から動的取得
- コード内・.env ファイルへの平文保存は禁止 (CLAUDE.md 絶対禁止2)
- 復号値は TTL 付き in-memory キャッシュに保持 (デフォルト 24h、KMS リクエスト最小化)
- put_parameter は Tier='Standard' を強制 (Advanced tier 課金回避・5重防御 #1)
- AWS 管理 KMS キー (alias/aws/ssm) を使用 (カスタム CMK 課金回避・5重防御 #5)
- 取得失敗時は fail-fast (例外伝播) — 起動時に検出してシステム停止

5重防御 (ADR-0003):
1. アプリ層: put_parameter ラッパーで Tier='Standard' 強制 (本ファイル)
2. アプリ層: 書き込み関数を本ファイル 1 箇所に集約
3. IAM ポリシー: ssm:Tier=Advanced を deny 条件で拒否 (AWS Console 設定)
4. AWS Budgets: 月額 $0.50 で Discord 通知、$1 で IAM 自動 deny (AWS Console 設定)
5. キャッシュ: 24h TTL で KMS リクエスト数を最小化 (本ファイル)
"""

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


# ===== 定数 =====
_DEFAULT_TTL_SEC = 24 * 60 * 60  # 24時間 (ADR-0003)
_AWS_MANAGED_KMS_KEY = "alias/aws/ssm"  # 課金回避のため AWS 管理キー固定
_PROJECT_PATH_PREFIX = "/projectbig/"  # 本PJ用プレフィクス (SEC-3 命名規約)


# ===== TTL Cache =====
_cache: Dict[str, Tuple[str, float]] = {}  # path -> (value, expires_at)


# ===== Boto3 Client (lazy init) =====
_ssm_client = None


def _get_client():
    """boto3 SSM クライアントを遅延初期化 (テスト時のモック差し替え容易性のため)。"""
    global _ssm_client
    if _ssm_client is None:
        region = os.getenv("AWS_REGION", "ap-northeast-1")
        _ssm_client = boto3.client("ssm", region_name=region)
    return _ssm_client


# ===== Public API =====
def get_secret(path: str, ttl_sec: int = _DEFAULT_TTL_SEC) -> str:
    """
    SSM Parameter Store からシークレット値を取得する (TTL キャッシュ付き)。

    :param path: SSM パラメータパス (例: "/projectbig/kabucom/password")
    :param ttl_sec: キャッシュ有効期間 (秒)。デフォルト 24h。
    :return: 復号済みシークレット値
    :raises RuntimeError: SSM 取得に失敗した場合 (fail-fast)
    """
    now = time.time()
    if path in _cache:
        value, expires_at = _cache[path]
        if now < expires_at:
            return value

    try:
        response = _get_client().get_parameter(Name=path, WithDecryption=True)
    except (ClientError, BotoCoreError) as e:
        logger.error(f"[AWS_SSM] get_parameter failed for path '{path}': {e}")
        raise RuntimeError(f"[AWS_SSM] Secret not found or access denied for '{path}': {e}") from e

    value = response["Parameter"]["Value"]
    _cache[path] = (value, now + ttl_sec)
    return value


def put_secret(path: str, value: str, overwrite: bool = True) -> None:
    """
    SSM Parameter Store にシークレット値を書き込む。

    Tier='Standard' / KeyId='alias/aws/ssm' を強制し、Advanced tier 課金・カスタム KMS 課金を防止 (ADR-0003)。
    本PJで唯一の SSM 書き込みエントリポイント。他経路からの直接 put_parameter は禁止。

    :param path: SSM パラメータパス。`/projectbig/` プレフィクス必須
    :param value: 保存値 (4KB 以内、Standard tier 制約)
    :param overwrite: 既存パラメータを上書きするか (デフォルト True)
    :raises ValueError: パスプレフィクス違反 / 値サイズ超過
    :raises RuntimeError: SSM 書き込み失敗
    """
    if not path.startswith(_PROJECT_PATH_PREFIX):
        raise ValueError(
            f"[AWS_SSM] Path must start with '{_PROJECT_PATH_PREFIX}'. Got: '{path}'"
        )
    if len(value.encode("utf-8")) > 4096:
        raise ValueError(
            f"[AWS_SSM] Value exceeds 4KB Standard tier limit "
            f"({len(value.encode('utf-8'))} bytes). Refusing to escalate to Advanced tier."
        )

    try:
        _get_client().put_parameter(
            Name=path,
            Value=value,
            Type="SecureString",
            KeyId=_AWS_MANAGED_KMS_KEY,  # 5重防御 #5: AWS 管理キー固定
            Tier="Standard",             # 5重防御 #1: Standard tier 強制
            Overwrite=overwrite,
        )
    except (ClientError, BotoCoreError) as e:
        logger.error(f"[AWS_SSM] put_parameter failed for path '{path}': {e}")
        raise RuntimeError(f"[AWS_SSM] Failed to write secret '{path}': {e}") from e

    # 書き込み後は該当キャッシュを無効化 (次回 get で最新取得)
    _cache.pop(path, None)
    logger.info(f"[AWS_SSM] put_parameter succeeded for path '{path}' (Tier=Standard)")


def describe_with_prefix(prefix: str = _PROJECT_PATH_PREFIX) -> List[Dict[str, object]]:
    """
    指定プレフィクス配下のパラメータ一覧をメタデータ込みで取得する。

    ローテーション期限チェック (SEC-9 / scheduler.py の check_secret_rotation ジョブ) で使用。
    シークレット値自体は取得しない (LastModifiedDate / Name のみ)。

    :param prefix: 検索プレフィクス。デフォルトは本PJ全体 `/projectbig/`
    :return: [{"Name": str, "LastModifiedDate": datetime, "Type": str, ...}, ...]
    :raises RuntimeError: SSM 取得失敗
    """
    try:
        paginator = _get_client().get_paginator("describe_parameters")
        results: List[Dict[str, object]] = []
        for page in paginator.paginate(
            ParameterFilters=[
                {"Key": "Name", "Option": "BeginsWith", "Values": [prefix]}
            ]
        ):
            results.extend(page.get("Parameters", []))
        return results
    except (ClientError, BotoCoreError) as e:
        logger.error(f"[AWS_SSM] describe_parameters failed for prefix '{prefix}': {e}")
        raise RuntimeError(f"[AWS_SSM] Failed to list parameters under '{prefix}': {e}") from e


def invalidate_cache(path: Optional[str] = None) -> None:
    """
    キャッシュを無効化する。シークレットローテーション時に手動呼び出し。

    :param path: 特定パスのみ無効化する場合に指定。None なら全キャッシュをクリア。
    """
    if path is None:
        _cache.clear()
        logger.info("[AWS_SSM] cache fully invalidated")
    else:
        _cache.pop(path, None)
        logger.info(f"[AWS_SSM] cache invalidated for path '{path}'")
