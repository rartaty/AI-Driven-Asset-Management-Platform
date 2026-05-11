"""
Discord Webhook 通知クライアント

参照仕様:
- docs/REQUIREMENTS_DEFINITION.md §6 (異常系・エッジケース対応)
- docs/TECHNICAL_SPECIFICATION.md §5 (アラート・監視仕様)
- docs/infrastructure/DATA_FOUNDATION.md §7.2 (Discord 通知タグ規約)
- docs/adr/0003-aws-ssm-standard-tier.md (Webhook URL は SSM 経由取得)
- .claude/agents/discord-notify-validator.md (検証観点)

通知タグ規約:
- 🔴 [CRITICAL] [COMPONENT] — 致命的 (API連続失敗、Kill Switch発動、14:50決済失敗等)
- 🟡 [WARN] [COMPONENT] — 警告 (一過性エラー、見直し推奨)
- 🟢 [INFO] [COMPONENT] — 通常通知 (約定、レポート生成完了等)

設計方針 (要件 §6.5 + behaviors.md S3):
- Webhook URL は SSM Parameter Store から動的取得 (絶対禁止2 準拠)
- 送信失敗時はメイン処理をブロックせず、ログには `[Notify]` プレフィクスで残す
- HTTP タイムアウト 5 秒 (短期トレードの遅延ブロック防止)
- リトライは 1 回 (失敗時は即諦め・冪等性のため `X-Discord-Idempotency` 等の仕組みは Discord 側にないため重複送信回避優先)
"""

import logging
from enum import Enum
from typing import Optional

import requests

from core.secrets import get_secret


logger = logging.getLogger(__name__)


# ===== Constants =====
_HTTP_TIMEOUT_SEC = 5
_DISCORD_MAX_CONTENT_LEN = 2000  # Discord webhook content 上限


class NotifyLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class DiscordChannel(str, Enum):
    """通知チャンネル区分。SSM パス `/projectbig/discord/webhook-{value}` に対応"""
    TRADING = "trading"  # 約定・取引イベント
    SYSTEM = "system"    # 通常運用・スケジューラ完了通知
    ALERTS = "alerts"    # 致命イベント・Kill Switch・大損失警告


_LEVEL_EMOJI = {
    NotifyLevel.INFO: "🟢",
    NotifyLevel.WARN: "🟡",
    NotifyLevel.CRITICAL: "🔴",
}


def _build_message(message: str, component: str, level: NotifyLevel) -> str:
    """通知本文を整形 (絵文字 + [LEVEL] + [COMPONENT] プレフィクス付与)。"""
    emoji = _LEVEL_EMOJI[level]
    formatted = f"{emoji} [{level.value}] [{component}] {message}"
    if len(formatted) > _DISCORD_MAX_CONTENT_LEN:
        formatted = formatted[: _DISCORD_MAX_CONTENT_LEN - 20] + "...(truncated)"
    return formatted


def _resolve_webhook_url(channel: DiscordChannel) -> Optional[str]:
    """SSM から該当チャンネルの Webhook URL を取得。失敗時は None を返し非ブロッキング。"""
    key_mapping = {
        DiscordChannel.TRADING: "DISCORD_WEBHOOK_TRADING",
        DiscordChannel.SYSTEM: "DISCORD_WEBHOOK_SYSTEM",
        DiscordChannel.ALERTS: "DISCORD_WEBHOOK_ALERTS",
    }
    logical_key = key_mapping.get(channel)
    if not logical_key:
        return None

    try:
        return get_secret(logical_key)
    except Exception as e:
        logger.error(f"[Notify] failed to resolve webhook URL for {channel.value}: {e}")
        return None


def notify(
    message: str,
    component: str,
    level: NotifyLevel = NotifyLevel.INFO,
    channel: DiscordChannel = DiscordChannel.SYSTEM,
) -> bool:
    """
    Discord Webhook へ通知を送信する (非ブロッキング設計)。

    :param message: 通知本文
    :param component: 発生源コンポーネント (例: "DB", "API:Kabucom", "KillSwitch")
    :param level: 通知レベル (INFO / WARN / CRITICAL)
    :param channel: 送信先チャンネル区分
    :return: 送信成功なら True、失敗なら False (例外は伝播させない)

    フォーマット例: "🔴 [CRITICAL] [API:Kabucom] kabuステーションAPI 連続3回タイムアウト"

    エラー処理: Webhook 送信失敗してもメイン処理をブロックせず、ログに [Notify] プレフィクスで記録。
    """
    formatted = _build_message(message, component, level)

    webhook_url = _resolve_webhook_url(channel)
    if webhook_url is None:
        return False

    try:
        response = requests.post(
            webhook_url,
            json={"content": formatted},
            timeout=_HTTP_TIMEOUT_SEC,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"[Notify] Discord webhook send failed (channel={channel.value}): {e}")
        return False
    except Exception as e:
        # 想定外の例外もメインを止めない
        logger.error(f"[Notify] unexpected error during Discord send (channel={channel.value}): {e}")
        return False


# ===== Convenience helpers (高頻度ユースケース向けショートカット) =====

def notify_critical(message: str, component: str) -> bool:
    """致命的イベント通知 (alerts チャンネル固定)。Kill Switch 発動・大損失等で使用。"""
    return notify(message, component, NotifyLevel.CRITICAL, DiscordChannel.ALERTS)


def notify_trade(message: str, component: str = "Trade") -> bool:
    """約定・取引イベント通知 (trading チャンネル固定)。"""
    return notify(message, component, NotifyLevel.INFO, DiscordChannel.TRADING)


def notify_system(message: str, component: str, level: NotifyLevel = NotifyLevel.INFO) -> bool:
    """通常システム通知 (system チャンネル固定)。"""
    return notify(message, component, level, DiscordChannel.SYSTEM)
