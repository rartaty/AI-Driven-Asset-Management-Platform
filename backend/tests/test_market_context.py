"""Tests for backend/src/services/market_context.py"""
import os
import sys
from datetime import date

import pytest
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from services import market_context
from models.database import Base
from models.schema import Market_Context
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

def test_evaluate_vix_gear():
    assert market_context.evaluate_vix_gear(15.0) == "DEFEND"
    assert market_context.evaluate_vix_gear(20.0) == "DEFEND"
    assert market_context.evaluate_vix_gear(25.0) == "NEUTRAL"
    assert market_context.evaluate_vix_gear(35.0) == "ATTACK"
    assert market_context.evaluate_vix_gear(40.0) == "ATTACK"
    assert market_context.evaluate_vix_gear(0.0) == "NEUTRAL"

def test_fetch_vix_success_kabucom(mocker):
    # Mock Kabucom
    mock_client = mocker.Mock()
    mock_client.get_index.return_value = 25.0
    mocker.patch("services.market_context.KabucomAPIClient", return_value=mock_client)
    
    vix = market_context.fetch_vix()
    assert vix == 25.0

def test_fetch_vix_fallback_yfinance(mocker):
    # Mock Kabucom to fail
    mock_client = mocker.Mock()
    mock_client.get_index.return_value = 0.0
    mocker.patch("services.market_context.KabucomAPIClient", return_value=mock_client)

    # Mock yfinance
    mock_ticker = mocker.Mock()
    df = pd.DataFrame({"Close": [22.5]})
    mock_ticker.history.return_value = df
    mocker.patch("yfinance.Ticker", return_value=mock_ticker)
    
    vix = market_context.fetch_vix()
    assert vix == 22.5

def test_update_market_context(session, mocker):
    mocker.patch("services.market_context.fetch_vix", return_value=36.0)
    
    context = market_context.update_market_context(session)
    
    assert context.vix == 36.0
    
    # Check if mode is ATTACK
    mode = market_context.evaluate_vix_gear(context.vix)
    assert mode == "ATTACK"
