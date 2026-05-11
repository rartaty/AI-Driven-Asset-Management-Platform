"""Tests for backend/src/services/position_sizing.py (Phase 8c / ADR-0012)

カバー範囲:
- buying_power × POSITION_SIZE_PCT の基本算出
- KABU_DAILY_ORDER_LIMIT_YEN による日次累計制限
- 100 株単位切り捨て
- price=0 / 不足時 0 を返す
- REAL_MAX_YEN_PER_TRADE 追加上限
- PAPER_INITIAL_CAPITAL fallback (Daily_Asset_Snapshot 不在時)
"""
import os
import sys
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from models.database import Base
from models.schema import Daily_Asset_Snapshot, Trade_Logs
from services.position_sizing import calculate_position_qty, LOT_SIZE


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


def _add_snapshot(db, buying_power: int):
    """Daily_Asset_Snapshot を 1 件追加 (今日付・最低限のフィールド)。"""
    db.add(Daily_Asset_Snapshot(
        date=date.today(),
        bank_balance=0, buying_power=buying_power,
        trust_value=0, long_solid_value=0, long_growth_value=0,
        short_term_capital=0, short_term_market_value=0,
        cumulative_sweep_to_long_solid=0,
    ))
    db.commit()


# ===== 基本算出 =====

class TestBasicCalculation:
    def test_zero_price_returns_zero(self, session):
        _add_snapshot(session, 1_000_000)
        assert calculate_position_qty(session, price=0) == 0

    def test_negative_price_returns_zero(self, session):
        _add_snapshot(session, 1_000_000)
        assert calculate_position_qty(session, price=-100) == 0

    def test_no_snapshot_uses_default_capital(self, session, monkeypatch):
        """Daily_Asset_Snapshot 不在時はデフォルト ¥1M で計算。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        # buying_power=1M (default) × 20% = 200K → ¥500 株なら 200000//50000*100 = 400株
        qty = calculate_position_qty(session, price=500)
        assert qty == 400


# ===== POSITION_SIZE_PCT 動作 =====

class TestPositionSizePct:
    def test_default_pct_20_percent(self, session, monkeypatch):
        """default 0.20 = 20% / buying_power=1M → target ¥200K / 価格 ¥500 → 400株。"""
        monkeypatch.delenv("POSITION_SIZE_PCT", raising=False)
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        _add_snapshot(session, 1_000_000)
        qty = calculate_position_qty(session, price=500)
        assert qty == 400  # 200000 // 50000 * 100

    def test_custom_pct_10_percent(self, session, monkeypatch):
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.10")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        _add_snapshot(session, 1_000_000)
        # 1M × 10% = 100K / ¥500 → 100000 // 50000 * 100 = 200株
        qty = calculate_position_qty(session, price=500)
        assert qty == 200

    def test_invalid_pct_falls_back_to_default(self, session, monkeypatch):
        monkeypatch.setenv("POSITION_SIZE_PCT", "not_a_number")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        _add_snapshot(session, 1_000_000)
        qty = calculate_position_qty(session, price=500)
        assert qty == 400  # default 20% で計算


# ===== KABU_DAILY_ORDER_LIMIT_YEN 動作 =====

class TestDailyLimit:
    def test_no_used_yen_full_target_available(self, session, monkeypatch):
        """当日累計 0 なら基本 target 全額が使える。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "1000000")
        _add_snapshot(session, 1_000_000)
        # 1M × 20% = 200K, 残量 = 1M - 0 = 1M, min(200K, 1M) = 200K
        qty = calculate_position_qty(session, price=500)
        assert qty == 400  # 200K // 50K * 100

    def test_partial_used_yen_target_remains(self, session, monkeypatch):
        """当日 800K 使用済 → 残量 200K → target 200K → 400株 (¥500銘柄)。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "1000000")
        _add_snapshot(session, 1_000_000)
        # 800K 取引履歴を仕込む
        session.add(Trade_Logs(
            ticker_symbol="7203", action="BUY", quantity=400, price=2000, pnl=0,
        ))
        session.commit()
        # 残量 1M - 800K = 200K, target = min(200K, 200K) = 200K
        qty = calculate_position_qty(session, price=500)
        assert qty == 400  # 200K // 50K * 100

    def test_used_yen_exceeds_limit_returns_zero(self, session, monkeypatch):
        """日次上限到達なら qty=0 (skip)。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "1000000")
        _add_snapshot(session, 1_000_000)
        # 累計 1M (上限到達)
        session.add(Trade_Logs(
            ticker_symbol="7203", action="BUY", quantity=500, price=2000, pnl=0,
        ))
        session.commit()
        qty = calculate_position_qty(session, price=500)
        assert qty == 0

    def test_remaining_yen_smaller_than_target(self, session, monkeypatch):
        """残量 < 基本 target → 残量で切り詰め。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "1000000")
        _add_snapshot(session, 1_000_000)
        # 950K 累計、残量 50K
        session.add(Trade_Logs(
            ticker_symbol="7203", action="BUY", quantity=475, price=2000, pnl=0,
        ))
        session.commit()
        # 基本 target=200K だが残量 50K → min(200K, 50K) = 50K → ¥500 銘柄なら 100株
        qty = calculate_position_qty(session, price=500)
        assert qty == 100

    def test_zero_daily_limit_means_unlimited(self, session, monkeypatch):
        """KABU_DAILY_ORDER_LIMIT_YEN=0 で無制限扱い。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        _add_snapshot(session, 10_000_000)  # ¥1000万
        # 1000万 × 20% = 200万 / ¥500 → 200万 // 50K * 100 = 4000株
        qty = calculate_position_qty(session, price=500)
        assert qty == 4000


# ===== REAL_MAX_YEN_PER_TRADE 動作 (Phase 8d 用) =====

class TestRealMaxYen:
    def test_default_zero_means_no_extra_limit(self, session, monkeypatch):
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        monkeypatch.delenv("REAL_MAX_YEN_PER_TRADE", raising=False)
        _add_snapshot(session, 1_000_000)
        qty = calculate_position_qty(session, price=500)
        assert qty == 400  # 200K target そのまま

    def test_real_max_caps_target(self, session, monkeypatch):
        """REAL_MAX_YEN_PER_TRADE で 1 取引上限を更に絞る (100 株テスト用途)。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        monkeypatch.setenv("REAL_MAX_YEN_PER_TRADE", "50000")  # ¥50K cap
        _add_snapshot(session, 1_000_000)
        # 200K target → 50K で cap → ¥500 銘柄なら 100株
        qty = calculate_position_qty(session, price=500)
        assert qty == 100


# ===== 100 株単位切り捨て =====

class TestLotSize:
    def test_lot_size_constant(self):
        assert LOT_SIZE == 100

    def test_truncation_to_lot(self, session, monkeypatch):
        """target_yen / price が中途半端でも 100 株単位に切り捨て。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        _add_snapshot(session, 1_000_000)
        # target 200K, price ¥3000 → 200K // 300K = 0 → 0 株
        qty = calculate_position_qty(session, price=3000)
        assert qty == 0

    def test_truncation_partial_lot(self, session, monkeypatch):
        """¥1500 銘柄なら 200K // 150K * 100 = 100株 (200/150=1.33...)。"""
        monkeypatch.setenv("POSITION_SIZE_PCT", "0.20")
        monkeypatch.setenv("KABU_DAILY_ORDER_LIMIT_YEN", "0")
        _add_snapshot(session, 1_000_000)
        qty = calculate_position_qty(session, price=1500)
        assert qty == 100