import pytest
import sys
import os
import pandas as pd
import numpy as np

# srcをインポートパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from strategy.vwap_short import calculate_vwap_and_zscore, detect_order_book_walls, evaluate_vwap_signal
from services import market_data

def test_calculate_vwap_and_zscore():
    # ダミー取引履歴データ
    # 価格が100円で10株、200円で20株、300円で20株
    # VWAP = (100*10 + 200*20 + 300*20) / 50 = (1000 + 4000 + 6000) / 50 = 11000 / 50 = 220
    data = {
        'price': [100.0, 200.0, 300.0],
        'volume': [10, 20, 20]
    }
    df = pd.DataFrame(data)
    
    result = calculate_vwap_and_zscore(df)
    
    assert result['vwap'] == 220.0
    assert result['current_price'] == 300.0
    # 乖離率 = (300 - 220) / 220 * 100 = 80 / 220 * 100 = 36.3636...
    assert pytest.approx(result['deviation_pct'], 0.01) == 36.36
    
    # Zスコアの符号確認 (現在価格がVWAPより高いのでプラスになるはず)
    assert result['z_score'] > 0

def test_calculate_vwap_empty():
    df = pd.DataFrame()
    result = calculate_vwap_and_zscore(df)
    assert result['vwap'] == 0.0
    assert result['z_score'] == 0.0

def test_detect_order_book_walls_spoofing():
    # 板情報のダミーデータ
    # 注文1: 通常 (見せ板ではない)
    # 注文2: 見せ板 (滞留時間が短いのに巨大なボリューム) -> 排除されるべき
    # 注文3: 本物の壁 (滞留時間が長く、ボリュームが平均の5倍以上)
    # 注文4: 通常 (見せ板ではない)
    
    data = {
        'price': [1000, 1010, 990, 1020],
        'volume': [100, 10000, 2000, 150],
        'duration_sec': [120, 5, 300, 150],
        'type': ['bid', 'ask', 'bid', 'ask']
    }
    df = pd.DataFrame(data)
    
    # 滞留60秒未満は見せ板、平均の5倍を壁とする
    walls = detect_order_book_walls(df, min_duration_sec=60, volume_multiplier=5.0)
    
    # 見せ板(1010円)は排除されているため、有効な注文は 100, 2000, 150 (平均750)
    # 閾値は 750 * 5 = 3750。しかし 2000 は 3750 を超えないため、壁は検出されないはず。
    
    # 条件を変えてテスト
    walls_lenient = detect_order_book_walls(df, min_duration_sec=60, volume_multiplier=2.0)
    
    # 閾値 750 * 2 = 1500。2000 の注文が壁として検出されるはず。
    assert len(walls_lenient) == 1
    assert walls_lenient[0]['price'] == 990
    assert walls_lenient[0]['volume'] == 2000
    assert walls_lenient[0]['duration_sec'] == 300

def test_detect_order_book_walls_empty():
    df = pd.DataFrame()
    walls = detect_order_book_walls(df)
    assert len(walls) == 0


# ===== Phase 7 P7-4: evaluate_vwap_signal (Layer 1 接続) =====

@pytest.fixture(autouse=False)
def clean_layer1():
    """Layer 1 deque をテスト前後でクリーンに保つ。"""
    market_data.reset_state()
    yield
    market_data.reset_state()


def test_evaluate_vwap_signal_no_ticks(clean_layer1):
    """Layer 1 に tick がない → HOLD + tick_count=0。"""
    result = evaluate_vwap_signal("9999")
    assert result["signal"] == "HOLD"
    assert result["tick_count"] == 0
    assert "No tick data" in result["reason"]


def test_evaluate_vwap_signal_buy_when_price_below_vwap(clean_layer1):
    """価格が VWAP より大きく下回る (Z < -2.0) → BUY シグナル。"""
    layer1 = market_data.get_layer1()
    # 高値 (3100) で大量取引、最後に低値 (2900) → Z スコアが負方向に大きく出る
    for _ in range(20):
        layer1.push_tick({"ticker_symbol": "7203", "last_price": 3100, "delta_volume": 100})
    layer1.push_tick({"ticker_symbol": "7203", "last_price": 2900, "delta_volume": 100})

    result = evaluate_vwap_signal("7203", n_ticks=21, z_threshold=2.0)
    assert result["z_score"] < -2.0
    assert result["signal"] == "BUY"
    assert result["tick_count"] == 21


def test_evaluate_vwap_signal_sell_when_price_above_vwap(clean_layer1):
    """価格が VWAP より大きく上回る (Z > 2.0) → SELL シグナル。"""
    layer1 = market_data.get_layer1()
    for _ in range(20):
        layer1.push_tick({"ticker_symbol": "7203", "last_price": 3000, "delta_volume": 100})
    layer1.push_tick({"ticker_symbol": "7203", "last_price": 3300, "delta_volume": 100})

    result = evaluate_vwap_signal("7203", n_ticks=21, z_threshold=2.0)
    assert result["z_score"] > 2.0
    assert result["signal"] == "SELL"


def test_evaluate_vwap_signal_hold_when_within_threshold(clean_layer1):
    """Z が閾値以内 → HOLD。"""
    layer1 = market_data.get_layer1()
    # 価格が小幅変動で一定 → Z は小さい
    for price in [3000, 3001, 2999, 3000, 3000, 3001, 2999]:
        layer1.push_tick({"ticker_symbol": "7203", "last_price": price, "delta_volume": 100})

    result = evaluate_vwap_signal("7203", n_ticks=10, z_threshold=2.0)
    assert abs(result["z_score"]) <= 2.0
    assert result["signal"] == "HOLD"


def test_evaluate_vwap_signal_isolates_per_ticker(clean_layer1):
    """銘柄違いの tick は混じらない (Layer 1 deque per-ticker)。"""
    layer1 = market_data.get_layer1()
    layer1.push_tick({"ticker_symbol": "7203", "last_price": 3000, "delta_volume": 100})
    layer1.push_tick({"ticker_symbol": "9984", "last_price": 8000, "delta_volume": 100})

    result_7203 = evaluate_vwap_signal("7203")
    result_9984 = evaluate_vwap_signal("9984")
    assert result_7203["tick_count"] == 1
    assert result_9984["tick_count"] == 1
    assert result_7203["current_price"] == 3000.0
    assert result_9984["current_price"] == 8000.0
