"""Tests for backend/src/core/kill_switch.py (Living Expenses Protection)"""
import os
import sys
from datetime import datetime, date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import kill_switch
from models.database import Base
from models.schema import Daily_Asset_Snapshot, User_Settings

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    yield db
    db.close()

def test_living_expenses_trigger(session, monkeypatch):
    """銀行残高が閾値を下回った場合、Kill Switchが発動すること"""
    monkeypatch.setenv("LIVING_EXPENSES_THRESHOLD", "1000000")
    
    # 銀行残高が90万円 (閾値未満)
    snapshot = Daily_Asset_Snapshot(
        date=date.today(),
        bank_balance=900000,
        buying_power=0,
        trust_value=0,
        long_solid_value=0,
        long_growth_value=0,
        short_term_capital=0,
        short_term_market_value=0,
        cumulative_sweep_to_long_solid=0
    )
    session.add(snapshot)
    session.commit()
    
    # 発動するかチェック
    triggered = kill_switch.check_drawdown_and_trigger(session)
    assert triggered is True
    
    settings = session.query(User_Settings).first()
    assert settings.is_kill_switch_active is True

def test_living_expenses_no_trigger(session, monkeypatch):
    """銀行残高が閾値以上の場合、Kill Switchが発動しないこと"""
    monkeypatch.setenv("LIVING_EXPENSES_THRESHOLD", "1000000")
    
    # 銀行残高が110万円 (閾値以上)
    snapshot = Daily_Asset_Snapshot(
        date=date.today(),
        bank_balance=1100000,
        buying_power=0,
        trust_value=0,
        long_solid_value=0,
        long_growth_value=0,
        short_term_capital=0,
        short_term_market_value=0,
        cumulative_sweep_to_long_solid=0
    )
    session.add(snapshot)
    session.commit()
    
    # 発動しないかチェック
    triggered = kill_switch.check_drawdown_and_trigger(session)
    assert triggered is False
    
    settings = session.query(User_Settings).first()
    assert settings.is_kill_switch_active is False
