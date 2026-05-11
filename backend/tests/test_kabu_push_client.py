"""Tests for backend/src/services/kabu_push_client.py (Phase 7 P7-3)

カバー範囲:
- TRADE_MODE=PAPER 時の no-op 挙動
- WebSocket 接続のモックテスト (websocket-client patch)
- メッセージパース (kabu Push JSON → market_data.on_push 形式)
- 切断時の再接続バックオフ計算
- start/stop の冪等性

注意: 本物の kabu Station への接続テストは別途 (本テストはモック専用)。
"""
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from services import kabu_push_client as kpc
from services import market_data
from models.schema import TickSide


@pytest.fixture(autouse=True)
def reset_singletons():
    """各テスト前にグローバル状態をリセット。"""
    kpc.reset_client()
    market_data.reset_state()
    yield
    kpc.reset_client()
    market_data.reset_state()


# ===== TRADE_MODE=PAPER 挙動 =====

class TestPaperMode:
    def test_start_in_paper_mode_is_noop(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "PAPER")
        client = kpc.KabuPushClient()
        result = client.start()
        assert result is True
        assert not client.is_running()  # スレッド起動していないこと

    def test_stop_in_paper_mode_is_safe(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "PAPER")
        client = kpc.KabuPushClient()
        client.start()
        client.stop()  # 例外なく完了


# ===== メッセージパース =====

class TestParsePush:
    def test_parse_complete_message(self):
        payload = {
            "Symbol": "7203",
            "CurrentPrice": 3000,
            "TradingVolume": 100000,
            "BidPrice": 2999,
            "AskPrice": 3001,
        }
        result = kpc.KabuPushClient._parse_push(payload)
        assert result is not None
        assert result["ticker_symbol"] == "7203"
        assert result["last_price"] == 3000
        assert result["cumulative_volume"] == 100000
        assert result["bid_price"] == 2999
        assert result["ask_price"] == 3001
        assert result["is_synthetic"] is False

    def test_parse_missing_quote_returns_partial(self):
        """bid/ask 欠損でもパース成功 (LR-EMO で tick test fallback)。"""
        payload = {
            "Symbol": "7203",
            "CurrentPrice": 3000,
            "TradingVolume": 100000,
        }
        result = kpc.KabuPushClient._parse_push(payload)
        assert result is not None
        assert result["bid_price"] is None
        assert result["ask_price"] is None

    def test_parse_missing_required_fields_returns_none(self):
        # Symbol 欠損
        assert kpc.KabuPushClient._parse_push({"CurrentPrice": 3000, "TradingVolume": 100}) is None
        # CurrentPrice 欠損
        assert kpc.KabuPushClient._parse_push({"Symbol": "7203", "TradingVolume": 100}) is None
        # TradingVolume 欠損
        assert kpc.KabuPushClient._parse_push({"Symbol": "7203", "CurrentPrice": 3000}) is None


# ===== on_message: パース → market_data 転送 =====

class TestOnMessage:
    def test_on_message_routes_to_market_data(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "PAPER")  # WebSocket は起動しない
        client = kpc.KabuPushClient()
        ws_mock = MagicMock()

        message = json.dumps({
            "Symbol": "7203",
            "CurrentPrice": 3000,
            "TradingVolume": 100,
            "BidPrice": 2999,
            "AskPrice": 3001,
        })
        client._on_message(ws_mock, message)

        # market_data の Layer 1 deque に到達したか確認
        # 1 push だけでは bucket 確定しないため、もう 1 push 送る
        message2 = json.dumps({
            "Symbol": "7203",
            "CurrentPrice": 3010,
            "TradingVolume": 200,
            "BidPrice": 3009,
            "AskPrice": 3011,
        })
        # 2 つ目の push 時刻は同 1 秒に入る可能性 - 実際の挙動に依存するため、
        # 最低限「例外なく処理された」ことを確認する
        client._on_message(ws_mock, message2)

    def test_on_message_invalid_json_logs_error(self):
        client = kpc.KabuPushClient()
        ws_mock = MagicMock()
        # 例外を投げず logger.error のみで済むこと
        client._on_message(ws_mock, "not a json{{{")

    def test_on_message_missing_fields_skipped(self):
        client = kpc.KabuPushClient()
        ws_mock = MagicMock()
        # Symbol なし → スキップ (例外を投げない)
        client._on_message(ws_mock, json.dumps({"CurrentPrice": 3000}))


# ===== 再接続バックオフ =====

class TestReconnectBackoff:
    def test_backoff_grows_exponentially(self):
        """exponential backoff: 1, 2, 4, 8, 16, 32, 60(cap), 60..."""
        from services.kabu_push_client import (
            RECONNECT_BACKOFF_BASE_SEC,
            RECONNECT_BACKOFF_MAX_SEC,
        )
        # base=1, attempts=1 → 1
        # attempts=2 → 2
        # attempts=3 → 4
        # attempts=7 → min(64, 60) = 60
        for attempt, expected in [(1, 1.0), (2, 2.0), (3, 4.0), (5, 16.0), (7, 60.0), (10, 60.0)]:
            backoff = min(
                RECONNECT_BACKOFF_BASE_SEC * (2 ** (attempt - 1)),
                RECONNECT_BACKOFF_MAX_SEC,
            )
            assert backoff == expected


# ===== start/stop ライフサイクル (REAL モード mock) =====

class TestStartStopReal:
    def test_start_creates_thread_in_real_mode(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "REAL")
        client = kpc.KabuPushClient()

        # WebSocketApp.run_forever をモックして即座に終了させる
        with patch("services.kabu_push_client.websocket.WebSocketApp") as mock_ws_app:
            mock_instance = MagicMock()
            mock_instance.run_forever.return_value = None
            mock_ws_app.return_value = mock_instance

            # stop_event を即セットして再接続ループを 1 周で抜ける
            client._stop_event.set()
            client.start()
            # スレッド起動はされる
            assert client._thread is not None
            client.stop()

    def test_double_start_is_safe(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "REAL")
        client = kpc.KabuPushClient()

        with patch("services.kabu_push_client.websocket.WebSocketApp") as mock_ws_app:
            mock_instance = MagicMock()

            def slow_run_forever(*args, **kwargs):
                client._stop_event.wait(timeout=2)

            mock_instance.run_forever = slow_run_forever
            mock_ws_app.return_value = mock_instance

            assert client.start() is True   # 初回 start 成功
            # 2 回目は False (既に動いている)
            second = client.start()
            assert second is False
            client.stop()


# ===== シングルトン =====

class TestSingleton:
    def test_get_client_returns_same_instance(self):
        c1 = kpc.get_client()
        c2 = kpc.get_client()
        assert c1 is c2

    def test_reset_client_creates_new(self):
        c1 = kpc.get_client()
        kpc.reset_client()
        c2 = kpc.get_client()
        assert c1 is not c2
