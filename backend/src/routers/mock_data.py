"""
フロントエンド表示用のモックデータ
"""

PORTFOLIO_MOCK_DATA = {
    "target_date": "2024-01-01T15:00:00Z",
    "trust_value": 500000,
    "long_value": 300000,
    "short_value": 200000,
    "cash_balance": 100000,
    "total_value": 1100000,
    "accumulated_sweep": 50000,
    "positions": [
        {"symbol": "7203", "name": "トヨタ自動車", "category": "Long", "shares": 100, "avg_price": 3000, "current_price": 3100, "unrealized_pnl": 10000},
        {"symbol": "8306", "name": "三菱UFJ", "category": "Long", "shares": 500, "avg_price": 1200, "current_price": 1180, "unrealized_pnl": -10000},
        {"symbol": "eMAXIS", "name": "eMAXIS Slim All Country", "category": "Passive", "shares": 15000, "avg_price": 20000, "current_price": 21000, "unrealized_pnl": 15000}
    ],
    "recent_activity": [
        {"timestamp": "2024-04-29T14:50:00Z", "type": "TRADE", "message": "VWAP Short Trade Executed (Symbol: 6861). PNL: +¥1,500"},
        {"timestamp": "2024-04-29T14:50:01Z", "type": "SYSTEM", "message": "Profit Sweep triggered. Transferred ¥750 to Trust Fund."},
        {"timestamp": "2024-04-29T14:50:02Z", "type": "RULE", "message": "All intraday positions closed successfully (Overnight Risk Avoided)."}
    ],
    "chart_data": [
        {"date": "4/24", "Trust": 480000, "Long": 280000, "Cash": 100000},
        {"date": "4/25", "Trust": 485000, "Long": 285000, "Cash": 100000},
        {"date": "4/26", "Trust": 490000, "Long": 290000, "Cash": 100000},
        {"date": "4/27", "Trust": 495000, "Long": 295000, "Cash": 100000},
        {"date": "4/28", "Trust": 500000, "Long": 300000, "Cash": 100000}
    ],
    "is_mock": True
}
