"""Tests for backend/src/runner/main_trade.py + backtest.py (Phase 8a / M9 / ADR-0011)

カバー範囲:
- main_trade preflight: trade_mode 検証 (REAL 確認文字列 / PAPER allow_paper)
- main_trade preflight: SSM 到達性 / Kill Switch 状態
- backtest_vwap_short: 基本動作 / 不足 tick / 銘柄不在
"""
import os
import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from models.database import Base
from models.schema import Asset_Master, AssetCategory, Market_Ticks, TickSide, User_Settings
from runner import main_trade, backtest


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


# ===== main_trade preflight: TRADE_MODE 検証 =====

class TestCheckTradeMode:
    def test_real_mode_with_correct_confirmation(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "REAL")
        monkeypatch.setenv("RUNNER_REAL_CONFIRM", main_trade.REAL_MODE_CONFIRMATION)
        assert main_trade.check_trade_mode() == "REAL"

    def test_real_mode_without_confirmation_raises(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "REAL")
        monkeypatch.delenv("RUNNER_REAL_CONFIRM", raising=False)
        with pytest.raises(main_trade.PreflightError, match="explicit confirmation"):
            main_trade.check_trade_mode()

    def test_real_mode_with_wrong_confirmation_raises(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "REAL")
        monkeypatch.setenv("RUNNER_REAL_CONFIRM", "wrong_phrase")
        with pytest.raises(main_trade.PreflightError, match="explicit confirmation"):
            main_trade.check_trade_mode()

    def test_paper_mode_default_blocks(self, monkeypatch):
        """allow_paper フラグなしで PAPER 起動はブロックされる (smoke test 用途明示が必要)。

        Phase 8c (ADR-0012) で PAPER_LIVE 追加に伴いメッセージが「REAL/PAPER_LIVE-mode entry」に変更。
        """
        monkeypatch.setenv("TRADE_MODE", "PAPER")
        with pytest.raises(main_trade.PreflightError, match="REAL/PAPER_LIVE-mode entry"):
            main_trade.check_trade_mode(allow_paper=False)

    def test_paper_mode_with_allow_flag_passes(self, monkeypatch):
        monkeypatch.setenv("TRADE_MODE", "PAPER")
        assert main_trade.check_trade_mode(allow_paper=True) == "PAPER"


# ===== main_trade preflight: Kill Switch =====

class TestCheckKillSwitch:
    def test_inactive_passes(self, session, monkeypatch):
        # User_Settings 未作成 = デフォルト False
        monkeypatch.setattr("models.database.SessionLocal", lambda: session)
        # 例外を投げないことを確認
        main_trade.check_kill_switch_inactive()

    def test_active_raises(self, session, monkeypatch):
        from core import kill_switch
        # Discord 通知をモックして DB 単体テストを実現
        monkeypatch.setattr(kill_switch, "notify_critical", lambda **kwargs: None)
        kill_switch.activate(session, reason="test", manual=True)
        monkeypatch.setattr("models.database.SessionLocal", lambda: session)
        with pytest.raises(main_trade.PreflightError, match="Kill switch is ACTIVE"):
            main_trade.check_kill_switch_inactive()


# ===== backtest_vwap_short =====

@pytest.fixture
def session_with_ticks():
    """Asset_Master + Market_Ticks (200 件) を仕込んだセッション。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    db.add(Asset_Master(
        ticker_symbol="7203", asset_name="Toyota",
        category=AssetCategory.short, is_active=True,
    ))
    db.commit()

    # 価格を sin 波風に振動させて Z スコア大きく出るようにする
    import math
    base_ts = datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)
    for i in range(200):
        ts = base_ts.replace(second=i % 60, minute=(i // 60) + 1)
        price = 3000 + int(50 * math.sin(i / 10))
        db.add(Market_Ticks(
            ticker_symbol="7203",
            timestamp=ts.replace(microsecond=i),  # 一意性確保
            last_price=price,
            cumulative_volume=100 * (i + 1),
            delta_volume=100,
            side_inference=TickSide.mid,
            push_count=1,
        ))
    db.commit()
    yield db
    db.close()


class TestBacktestVwapShort:
    def test_basic_run(self, session_with_ticks):
        start = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 1, 23, 59, 59, tzinfo=timezone.utc)
        results = backtest.backtest_vwap_short(
            start, end, ["7203"], session_with_ticks, rolling_window=50, z_threshold=1.0,
        )
        assert "7203" in results
        assert results["7203"]["tick_count"] == 200
        # rolling=50 で 150 回評価 → BUY/SELL シグナルが少なくとも 1 件以上発火
        assert results["7203"]["signal_count"] > 0

    def test_insufficient_ticks(self, session_with_ticks):
        """rolling_window より tick が少ない場合は signal_count=0 + note 付与。"""
        start = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 1, 23, 59, 59, tzinfo=timezone.utc)
        results = backtest.backtest_vwap_short(
            start, end, ["7203"], session_with_ticks, rolling_window=500, z_threshold=2.0,
        )
        assert results["7203"]["signal_count"] == 0
        assert "note" in results["7203"]

    def test_unknown_ticker_returns_zero(self, session_with_ticks):
        start = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 1, 23, 59, 59, tzinfo=timezone.utc)
        results = backtest.backtest_vwap_short(
            start, end, ["9999"], session_with_ticks, rolling_window=10, z_threshold=2.0,
        )
        assert results["9999"]["tick_count"] == 0
        assert results["9999"]["signal_count"] == 0

    def test_multiple_tickers(self, session_with_ticks):
        # 9984 を追加
        session_with_ticks.add(Asset_Master(
            ticker_symbol="9984", asset_name="SoftBank",
            category=AssetCategory.short, is_active=True,
        ))
        session_with_ticks.commit()
        start = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 1, 23, 59, 59, tzinfo=timezone.utc)
        results = backtest.backtest_vwap_short(
            start, end, ["7203", "9984"], session_with_ticks, rolling_window=10,
        )
        assert "7203" in results
        assert "9984" in results