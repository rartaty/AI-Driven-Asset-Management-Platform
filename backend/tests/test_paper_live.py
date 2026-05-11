"""Tests for PAPER_LIVE mode integration (Phase 8c / ADR-0012)

カバー範囲:
- paper_trader.get_realtime_price: Layer 1 優先 + yfinance fallback
- paper_trader.PAPER_INITIAL_CAPITAL 環境変数による初期化
- kabu_push_client.start() in PAPER_LIVE: WebSocket 接続を試みる
- runner/main_trade preflight: PAPER_LIVE 確認文字列不要 + kabu smoke test 必須
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from models.database import Base
from services import market_data, paper_trader, kabu_push_client
from runner import main_trade


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def reset_singletons():
    """各テスト前にグローバル状態をリセット。"""
    market_data.reset_state()
    kabu_push_client.reset_client()
    yield
    market_data.reset_state()
    kabu_push_client.reset_client()


# ===== paper_trader.get_realtime_price: Layer 1 優先 =====

class TestGetRealtimePriceLayer1Priority:
    def test_layer1_tick_returns_layer1_price(self):
        """Layer 1 deque に tick あり → yfinance 呼ばずに Layer 1 価格を返す。"""
        layer1 = market_data.get_layer1()
        layer1.push_tick({"ticker_symbol": "7203", "last_price": 3050})

        engine = paper_trader.PaperTrader()
        engine._initialize()
        # yfinance を呼んだら実通信 (避ける)
        with patch("services.paper_trader.yf.Ticker") as mock_yf:
            price = engine.get_realtime_price("7203")
            assert price == 3050.0
            mock_yf.assert_not_called()  # yfinance に到達しない

    def test_layer1_empty_falls_back_to_yfinance(self):
        """Layer 1 空 → yfinance 呼ぶ。"""
        engine = paper_trader.PaperTrader()
        engine._initialize()

        # yfinance のみモック (return_value で価格供給)
        import pandas as pd
        mock_df = pd.DataFrame({"Close": [2900.0]}, index=[0])
        with patch("services.paper_trader.yf.Ticker") as mock_yf:
            mock_yf.return_value.history.return_value = mock_df
            price = engine.get_realtime_price("9999")  # Layer 1 にない銘柄
            assert price == 2900.0
            mock_yf.assert_called_once()


# ===== PAPER_INITIAL_CAPITAL 環境変数 =====

class TestPaperInitialCapital:
    def test_default_capital(self, monkeypatch):
        monkeypatch.delenv("PAPER_INITIAL_CAPITAL", raising=False)
        engine = paper_trader.PaperTrader()
        engine._initialize()
        assert engine.initial_capital == 1_000_000.0
        assert engine.cash_balance == 1_000_000.0

    def test_custom_capital_via_env(self, monkeypatch):
        monkeypatch.setenv("PAPER_INITIAL_CAPITAL", "5000000")
        engine = paper_trader.PaperTrader()
        engine._initialize()
        assert engine.initial_capital == 5_000_000.0
        assert engine.cash_balance == 5_000_000.0

    def test_invalid_capital_falls_back(self, monkeypatch):
        monkeypatch.setenv("PAPER_INITIAL_CAPITAL", "not_a_number")
        engine = paper_trader.PaperTrader()
        engine._initialize()
        assert engine.initial_capital == 1_000_000.0


# ===== kabu_push_client.start() in PAPER_LIVE =====

class TestKabuPushStartPaperLive:
    def test_paper_mode_no_op(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "PAPER")
        client = kabu_push_client.KabuPushClient()
        result = client.start()
        assert result is True
        assert not client.is_running()

    def test_paper_live_mode_starts_thread(self, monkeypatch):
        """PAPER_LIVE では WebSocket 接続を試みる (mock で実通信回避)。"""
        monkeypatch.setenv("TRADE_MODE", "PAPER_LIVE")
        client = kabu_push_client.KabuPushClient()

        with patch("services.kabu_push_client.websocket.WebSocketApp") as mock_ws_app:
            mock_instance = MagicMock()
            mock_instance.run_forever.return_value = None
            mock_ws_app.return_value = mock_instance

            client._stop_event.set()  # ループを 1 周で抜ける
            client.start()
            assert client._thread is not None
            client.stop()


# ===== runner/main_trade preflight: PAPER_LIVE モード =====

class TestMainTradePaperLive:
    def test_paper_live_no_confirmation_required(self, monkeypatch):
        """PAPER_LIVE は確認文字列不要で起動可能。"""
        monkeypatch.setenv("TRADE_MODE", "PAPER_LIVE")
        monkeypatch.delenv("RUNNER_REAL_CONFIRM", raising=False)
        assert main_trade.check_trade_mode() == "PAPER_LIVE"

    def test_paper_live_does_not_require_allow_paper_flag(self, monkeypatch):
        """PAPER_LIVE は --allow-paper フラグなしで起動 OK。"""
        monkeypatch.setenv("TRADE_MODE", "PAPER_LIVE")
        # allow_paper=False (default) でも通る
        assert main_trade.check_trade_mode(allow_paper=False) == "PAPER_LIVE"

    def test_unknown_mode_raises(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "UNKNOWN_MODE")
        with pytest.raises(main_trade.PreflightError, match="Unknown TRADE_MODE"):
            main_trade.check_trade_mode()

    def test_check_kabu_authenticate_pass(self, monkeypatch):
        """kabu authenticate 成功時は例外なし。"""
        with patch("api.kabucom.KabucomAPIClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.authenticate.return_value = True
            mock_client_cls.return_value = mock_client
            main_trade.check_kabu_authenticate_for_live()  # 例外なし

    def test_check_kabu_authenticate_fail_raises(self, monkeypatch):
        """kabu authenticate 失敗 → PreflightError。"""
        with patch("api.kabucom.KabucomAPIClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.authenticate.return_value = False
            mock_client_cls.return_value = mock_client
            with pytest.raises(main_trade.PreflightError, match="kabu Station authenticate failed"):
                main_trade.check_kabu_authenticate_for_live()