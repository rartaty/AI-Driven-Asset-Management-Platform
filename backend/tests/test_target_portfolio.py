"""Tests for backend/src/services/target_portfolio.py (Phase 5 P9)

カバー範囲:
- write_target の UPSERT + 比率合計バリデーション
- VIXギア発火時の自動書込 (DEFEND/ATTACK/NEUTRAL)
- 四半期定期見直しのベースライン書込
- get_active_ratios の DB なし時のフォールバック
"""
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base
from models.schema import Target_Portfolio
from services import target_portfolio as tp


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


# ===== write_target 基本動作 =====

def test_write_target_creates_record(session):
    record = tp.write_target(
        session, cash_pct=0.10, trust_pct=0.50, stocks_pct=0.40,
        trigger="Manual", notes="initial test"
    )
    assert record.cash_target_pct == 0.10
    assert record.trust_target_pct == 0.50
    assert record.stocks_target_pct == 0.40
    assert record.trigger == "Manual"


def test_write_target_upserts_same_day(session):
    """同日2回呼ばれても1レコード、最後の値で上書き。"""
    tp.write_target(session, 0.10, 0.50, 0.40, "Manual")
    tp.write_target(session, 0.30, 0.40, 0.30, "VIX_DEFEND")

    all_records = session.query(Target_Portfolio).all()
    assert len(all_records) == 1
    assert all_records[0].cash_target_pct == 0.30
    assert all_records[0].trigger == "VIX_DEFEND"


def test_write_target_rejects_invalid_sum(session):
    """比率合計が 1.0 から 0.01 以上ずれた場合 ValueError。"""
    with pytest.raises(ValueError, match="must sum to 1.0"):
        tp.write_target(session, 0.30, 0.30, 0.30, "Manual")


def test_write_target_accepts_minor_rounding(session):
    """0.01 以内の誤差は許容 (浮動小数点演算の誤差吸収)。"""
    record = tp.write_target(session, 0.333, 0.333, 0.334, "Manual")
    assert record is not None


# ===== VIXギア発火 =====

def test_write_for_vix_gear_defend_writes_defensive_ratios(session):
    record = tp.write_for_vix_gear(session, "DEFEND", vix_value=18.5)
    assert record is not None
    assert record.cash_target_pct == 0.30  # cash-heavy
    assert record.stocks_target_pct == 0.30
    assert record.trigger == "VIX_DEFEND"
    assert "VIX=18.50" in (record.notes or "")


def test_write_for_vix_gear_attack_writes_aggressive_ratios(session):
    record = tp.write_for_vix_gear(session, "ATTACK", vix_value=42.0)
    assert record is not None
    assert record.cash_target_pct == 0.05  # deploy aggressively
    assert record.stocks_target_pct == 0.55
    assert record.trigger == "VIX_ATTACK"


def test_write_for_vix_gear_neutral_skips(session):
    """NEUTRAL モードは Target_Portfolio に書込しない (要件: 四半期見直しでベースライン復帰)。"""
    record = tp.write_for_vix_gear(session, "NEUTRAL", vix_value=25.0)
    assert record is None
    assert session.query(Target_Portfolio).count() == 0


def test_write_for_vix_gear_unknown_mode_skips(session):
    """未知のモード (タイポ等) では何もしない。"""
    record = tp.write_for_vix_gear(session, "UNKNOWN", vix_value=20.0)
    assert record is None


# ===== 四半期定期見直し =====

def test_write_for_quarterly_review_writes_baseline(session):
    record = tp.write_for_quarterly_review(session)
    assert record.trigger == "Quarterly"
    assert record.cash_target_pct == tp.QUARTERLY_BASELINE_RATIOS["cash"]
    assert record.trust_target_pct == tp.QUARTERLY_BASELINE_RATIOS["trust"]
    assert record.stocks_target_pct == tp.QUARTERLY_BASELINE_RATIOS["stocks"]


# ===== get_active_ratios =====

def test_get_active_ratios_returns_baseline_when_empty(session):
    """Target_Portfolio が空 → QUARTERLY_BASELINE_RATIOS を返す (rebalance ロジックの安全装置)。"""
    ratios = tp.get_active_ratios(session)
    assert ratios == tp.QUARTERLY_BASELINE_RATIOS


def test_get_active_ratios_returns_latest(session):
    """複数レコードがあれば effective_date が最新のものを返す。"""
    today = date.today()
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=7)

    tp.write_target(session, 0.10, 0.50, 0.40, "Manual",
                    effective_date_override=last_week)
    tp.write_target(session, 0.20, 0.45, 0.35, "Manual",
                    effective_date_override=yesterday)
    tp.write_target(session, 0.05, 0.40, 0.55, "VIX_ATTACK",
                    effective_date_override=today)

    ratios = tp.get_active_ratios(session)
    assert ratios["cash"] == 0.05  # 最新
    assert ratios["stocks"] == 0.55


# ===== split_stocks_to_long_short =====

def test_split_stocks_to_long_short_60_40_fixed():
    """株式内 60% Long / 40% Short の固定比率 (要件 §2.2)。"""
    result = tp.split_stocks_to_long_short(0.40)
    assert result["long"] == pytest.approx(0.40 * 0.60)
    assert result["short"] == pytest.approx(0.40 * 0.40)


def test_split_stocks_to_long_short_zero():
    result = tp.split_stocks_to_long_short(0.0)
    assert result["long"] == 0.0
    assert result["short"] == 0.0
