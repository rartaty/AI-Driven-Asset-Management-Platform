import pytest
import sys
import os
import pandas as pd

# srcをインポートパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from strategy.v6_long import evaluate_long_term_fundamentals

def test_evaluate_long_term_fundamentals_eligible():
    # 理想的な成長企業: 3期連続FCFプラス、資産増加よりEBITDA増加が大きい
    # 資産: 1000 -> 1050 -> 1100 (10%増)
    # EBITDA: 200 -> 230 -> 260 (30%増)
    data = {
        'year': [2021, 2022, 2023],
        'fcf': [50, 60, 80],
        'total_assets': [1000, 1050, 1100],
        'ebitda': [200, 230, 260]
    }
    df = pd.DataFrame(data)
    result = evaluate_long_term_fundamentals(df)
    
    assert result["is_eligible"] is True
    assert pytest.approx(result["assets_growth_rate_pct"]) == 10.0
    assert pytest.approx(result["ebitda_growth_rate_pct"]) == 30.0
    assert result["reason"] == "Efficient growth"

def test_evaluate_long_term_fundamentals_negative_fcf():
    # FCF赤字の年が含まれる企業
    data = {
        'year': [2021, 2022, 2023],
        'fcf': [50, -10, 80], # 2022年が赤字
        'total_assets': [1000, 1050, 1100],
        'ebitda': [200, 230, 260]
    }
    df = pd.DataFrame(data)
    result = evaluate_long_term_fundamentals(df)
    
    assert result["is_eligible"] is False
    assert result["reason"] == "FCF was negative in the last 3 years."

def test_evaluate_long_term_fundamentals_inefficient():
    # 資産が肥大化し非効率な企業
    # 資産: 1000 -> 1250 -> 1500 (50%増)
    # EBITDA: 200 -> 220 -> 240 (20%増)
    data = {
        'year': [2021, 2022, 2023],
        'fcf': [50, 60, 80],
        'total_assets': [1000, 1250, 1500],
        'ebitda': [200, 220, 240]
    }
    df = pd.DataFrame(data)
    result = evaluate_long_term_fundamentals(df)
    
    assert result["is_eligible"] is False
    assert pytest.approx(result["assets_growth_rate_pct"]) == 50.0
    assert pytest.approx(result["ebitda_growth_rate_pct"]) == 20.0
    assert "inefficient" in result["reason"]

def test_evaluate_long_term_fundamentals_insufficient_data():
    # 2期分しかない場合
    data = {
        'year': [2022, 2023],
        'fcf': [60, 80],
        'total_assets': [1050, 1100],
        'ebitda': [230, 260]
    }
    df = pd.DataFrame(data)
    result = evaluate_long_term_fundamentals(df)
    
    assert result["is_eligible"] is False
    assert result["error"] == "Insufficient data"
