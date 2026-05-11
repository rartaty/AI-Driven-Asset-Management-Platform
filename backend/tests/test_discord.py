"""Tests for backend/src/core/discord.py"""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import discord  # noqa: E402
from core.discord import DiscordChannel, NotifyLevel  # noqa: E402


@pytest.fixture
def mock_webhook_resolution(mocker):
    """webhook URL 解決を成功で固定。"""
    return mocker.patch.object(
        discord, "_resolve_webhook_url",
        return_value="https://discord.com/api/webhooks/dummy/token",
    )


@pytest.fixture
def mock_requests_post(mocker):
    """requests.post を成功 (HTTP 204) で固定。"""
    response = mocker.Mock()
    response.raise_for_status.return_value = None
    return mocker.patch.object(requests, "post", return_value=response)


# ===== _build_message (整形ロジック) =====
def test_build_message_includes_emoji_level_component_prefix():
    # シナリオ: 整形結果に絵文字 + [LEVEL] + [COMPONENT] が必ず先頭に含まれる
    formatted = discord._build_message("hello", "TestComp", NotifyLevel.CRITICAL)
    assert formatted.startswith("🔴 [CRITICAL] [TestComp] hello")


def test_build_message_truncates_when_over_2000_chars():
    # シナリオ: Discord webhook content 2000 文字上限を超える場合は切詰
    long_msg = "a" * 3000
    formatted = discord._build_message(long_msg, "C", NotifyLevel.INFO)
    assert len(formatted) <= 2000
    assert formatted.endswith("...(truncated)")


# ===== notify (success) =====
def test_notify_success_returns_true(mock_webhook_resolution, mock_requests_post):
    # シナリオ: webhook 取得成功 + HTTP 200 → True
    result = discord.notify("test message", "TestComp", NotifyLevel.INFO)
    assert result is True
    mock_requests_post.assert_called_once()


def test_notify_uses_correct_channel_path(mocker, mock_requests_post):
    # シナリオ: チャンネル指定が SSM パスに正しく反映される
    mock_get_secret = mocker.patch.object(
        discord, "get_secret", return_value="https://discord.com/api/webhooks/x/y"
    )

    discord.notify("msg", "C", NotifyLevel.INFO, DiscordChannel.ALERTS)

    mock_get_secret.assert_called_with("DISCORD_WEBHOOK_ALERTS")


def test_notify_posts_formatted_content(mock_webhook_resolution, mock_requests_post):
    # シナリオ: requests.post に整形済 content が JSON で渡される
    discord.notify("body", "MyComp", NotifyLevel.WARN)

    call_args = mock_requests_post.call_args
    payload = call_args.kwargs["json"]
    assert payload["content"].startswith("🟡 [WARN] [MyComp]")


# ===== notify (failure non-blocking) =====
def test_notify_returns_false_when_webhook_url_unresolvable(mocker):
    # シナリオ: SSM 取得失敗時は例外を投げず False
    mocker.patch.object(discord, "_resolve_webhook_url", return_value=None)
    result = discord.notify("msg", "C")
    assert result is False


def test_notify_returns_false_on_http_error(mock_webhook_resolution, mocker):
    # シナリオ: HTTP 500 等で raise_for_status が例外 → False (例外は伝播させない)
    response = mocker.Mock()
    response.raise_for_status.side_effect = requests.HTTPError("500")
    mocker.patch.object(requests, "post", return_value=response)

    result = discord.notify("msg", "C")
    assert result is False


def test_notify_returns_false_on_connection_timeout(mock_webhook_resolution, mocker):
    # シナリオ: タイムアウト等の RequestException → False
    mocker.patch.object(requests, "post", side_effect=requests.Timeout("timeout"))
    result = discord.notify("msg", "C")
    assert result is False


def test_notify_swallows_unexpected_exceptions(mock_webhook_resolution, mocker):
    # シナリオ: 想定外の例外 (e.g. JSON エンコード失敗) もメインを止めない
    mocker.patch.object(requests, "post", side_effect=ValueError("unexpected"))
    result = discord.notify("msg", "C")
    assert result is False


# ===== _resolve_webhook_url =====
def test_resolve_webhook_url_returns_none_on_ssm_failure(mocker):
    # シナリオ: SSM 取得失敗時は None を返し、例外を上位に伝播させない
    mocker.patch.object(discord, "get_secret", side_effect=RuntimeError("ssm fail"))
    result = discord._resolve_webhook_url(DiscordChannel.SYSTEM)
    assert result is None


# ===== Convenience helpers =====
def test_notify_critical_uses_alerts_channel(mocker, mock_requests_post):
    # シナリオ: notify_critical は alerts チャンネルに送る
    mock_get_secret = mocker.patch.object(
        discord, "get_secret", return_value="https://discord.com/api/webhooks/x/y"
    )

    discord.notify_critical("kill switch fired", "KillSwitch")

    mock_get_secret.assert_called_with("DISCORD_WEBHOOK_ALERTS")


def test_notify_trade_uses_trading_channel(mocker, mock_requests_post):
    # シナリオ: notify_trade は trading チャンネルに送る
    mock_get_secret = mocker.patch.object(
        discord, "get_secret", return_value="https://discord.com/api/webhooks/x/y"
    )

    discord.notify_trade("BUY 100 7203")

    mock_get_secret.assert_called_with("DISCORD_WEBHOOK_TRADING")


def test_notify_system_uses_system_channel(mocker, mock_requests_post):
    # シナリオ: notify_system は system チャンネルに送る
    mock_get_secret = mocker.patch.object(
        discord, "get_secret", return_value="https://discord.com/api/webhooks/x/y"
    )

    discord.notify_system("scheduler started", "Sched")

    mock_get_secret.assert_called_with("DISCORD_WEBHOOK_SYSTEM")
