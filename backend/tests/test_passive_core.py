import pytest
import sys
import os

# srcをインポートパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from strategy.passive_core import calculate_profit_sweep, calculate_rebalance_amounts

def test_profit_sweep_no_previous_loss():
    # 前日損失ゼロ、当日利益10,000円 -> 5,000円ずつ分割されるべき
    result = calculate_profit_sweep(realized_pnl=10000, previous_loss=0)
    assert result["sweep_to_long_solid"] == 5000
    assert result["reinvest_to_short"] == 5000

def test_profit_sweep_with_previous_loss():
    # 前日損失4,000円、当日利益10,000円 -> 純利益6,000円なので、3,000円ずつ分割されるべき
    result = calculate_profit_sweep(realized_pnl=10000, previous_loss=4000)
    assert result["sweep_to_long_solid"] == 3000
    assert result["reinvest_to_short"] == 3000

def test_profit_sweep_loss_greater_than_profit():
    # 前日損失12,000円、当日利益10,000円 -> 未補填損失が残るため振替ゼロ、短期再投資ゼロ
    result = calculate_profit_sweep(realized_pnl=10000, previous_loss=12000)
    assert result["sweep_to_long_solid"] == 0
    assert result["reinvest_to_short"] == 0

def test_profit_sweep_negative_pnl():
    # 当日マイナス -> 振替・再投資ともにゼロ
    result = calculate_profit_sweep(realized_pnl=-5000, previous_loss=0)
    assert result["sweep_to_long_solid"] == 0
    assert result["reinvest_to_short"] == 0

def test_rebalance_no_need():
    # 強制トリガー(vix_triggered)がない場合はリバランスしない (トップレベルBandなし)
    result = calculate_rebalance_amounts(
        trust_value=400000, 
        long_value=300000, 
        short_value=300000,
        vix_triggered=False
    )
    assert result["trust_adj"] == 0
    assert result["long_adj"] == 0
    assert result["short_adj"] == 0

def test_rebalance_needed_short_excess():
    # vix_triggered=True の場合、目標比率（デフォルト50/30/20）に向かってリバランスされる
    # 投資信託: 400,000 (40%) - 目標 50%
    # 長期運用: 300,000 (30%) - 目標 30%
    # 短期運用: 300,000 (30%) - 目標 20%
    # 合計 1,000,000
    result = calculate_rebalance_amounts(
        trust_value=400000, 
        long_value=300000, 
        short_value=300000,
        vix_triggered=True
    )
    
    # 目標は 投信50万, 長期30万, 短期20万
    # 短期枠から 100,000 減らし、投信枠へ 100,000 追加するはず
    assert result["short_adj"] == -100000
    assert result["trust_adj"] == 100000
    assert result["long_adj"] == 0

def test_rebalance_with_dynamic_targets():
    # vix_triggered=True かつ、カスタムの目標比率（例: 攻撃モードで短期を増やす）
    result = calculate_rebalance_amounts(
        trust_value=400000, 
        long_value=300000, 
        short_value=300000,
        target_ratios={"trust": 0.40, "long": 0.30, "short": 0.30},
        vix_triggered=True
    )
    
    # ちょうど目標通りなので調整ゼロ
    assert result["short_adj"] == 0
    assert result["trust_adj"] == 0
    assert result["long_adj"] == 0
