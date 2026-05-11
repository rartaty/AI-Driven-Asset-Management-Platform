from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from datetime import datetime, timedelta
from typing import List, Dict, Any

from models.database import get_db
from models.schema import Daily_Asset_Snapshot, Trade_Tick_Log, System_Logs, Trade_Logs
from services.portfolio_sync import PortfolioSyncService
from core.security import verify_admin_token

# Require authentication for all endpoints in this router
router = APIRouter(
    prefix="/api/v1/portfolio",
    tags=["Portfolio"],
    dependencies=[Depends(verify_admin_token)]
)

sync_service = PortfolioSyncService()

@router.get("/summary")
def get_portfolio_summary(db: Session = Depends(get_db)):
    """
    ダッシュボード用の資産サマリ（全体像、ポジション、アクティビティ）を取得する
    """
    # サービス層（PaperTraderまたは本番API）から最新のポートフォリオを取得
    portfolio_data = sync_service.get_consolidated_portfolio()
    
    # グラフ描画用のデフォルトデータ (Daily)
    thirty_days_ago = datetime.now().date() - timedelta(days=30)
    snapshots = db.query(Daily_Asset_Snapshot).filter(Daily_Asset_Snapshot.date >= thirty_days_ago).order_by(Daily_Asset_Snapshot.date).all()
    
    chart_data = []
    for s in snapshots:
        total = s.bank_balance + s.buying_power + s.trust_value + s.long_solid_value + s.long_growth_value + s.short_term_market_value
        chart_data.append({
            "date": s.date.strftime("%Y-%m-%d"),
            "value": total,
            "bank_balance": s.bank_balance,
            "trust": s.trust_value,
            "long_solid": s.long_solid_value,
            "short_term": s.short_term_market_value
        })
        
    portfolio_data["chart_data"] = chart_data
    
    # Recent activity from DB (Trade_Logs and System_Logs)
    activities = []
    trades = db.query(Trade_Logs).order_by(desc(Trade_Logs.timestamp)).limit(5).all()
    for t in trades:
        activities.append({
            "id": f"t_{t.id}",
            "type": "Trade",
            "title": f"{t.action} {t.ticker_symbol}",
            "description": f"{t.quantity} shares at ¥{t.price} (PnL: {t.pnl})",
            "timestamp": t.timestamp.isoformat(),
            "reason": t.decision_reason
        })
        
    system_logs = db.query(System_Logs).filter(System_Logs.level.in_(["WARN", "ERROR", "CRITICAL"])).order_by(desc(System_Logs.timestamp)).limit(3).all()
    for s in system_logs:
        activities.append({
            "id": f"s_{s.id}",
            "type": "System",
            "title": s.event,
            "description": s.component,
            "timestamp": s.timestamp.isoformat()
        })
        
    # Sort combined activities by timestamp desc
    activities.sort(key=lambda x: x["timestamp"], reverse=True)
    portfolio_data["recent_activity"] = activities[:8]
    
    return portfolio_data

@router.get("/chart/{timeframe}")
def get_chart_data(timeframe: str, db: Session = Depends(get_db)):
    """
    指定された時間軸（daily, monthly, intraday）のチャートデータを取得する
    """
    chart_data = []
    now = datetime.now()
    
    if timeframe == "daily":
        start_date = now.date() - timedelta(days=30)
        snapshots = db.query(Daily_Asset_Snapshot).filter(Daily_Asset_Snapshot.date >= start_date).order_by(Daily_Asset_Snapshot.date).all()
        for s in snapshots:
            total = s.bank_balance + s.buying_power + s.trust_value + s.long_solid_value + s.long_growth_value + s.short_term_market_value
            chart_data.append({
                "date": s.date.strftime("%Y-%m-%d"),
                "value": total
            })
            
    elif timeframe == "monthly":
        start_date = now.date() - timedelta(days=365)
        snapshots = db.query(Daily_Asset_Snapshot).filter(Daily_Asset_Snapshot.date >= start_date).order_by(Daily_Asset_Snapshot.date).all()
        
        # Group by month
        monthly_data = {}
        for s in snapshots:
            month_key = s.date.strftime("%Y-%m")
            total = s.bank_balance + s.buying_power + s.trust_value + s.long_solid_value + s.long_growth_value + s.short_term_market_value
            # Override with the latest value of the month
            monthly_data[month_key] = total
            
        for k, v in monthly_data.items():
            chart_data.append({"date": k, "value": v})
            
    elif timeframe == "intraday":
        # Get today's tick logs
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ticks = db.query(Trade_Tick_Log).filter(Trade_Tick_Log.timestamp >= today_start).order_by(Trade_Tick_Log.timestamp).all()
        for t in ticks:
            chart_data.append({
                "date": t.timestamp.strftime("%H:%M"),
                "pnl": t.unrealized_pnl + t.realized_pnl
            })
    else:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Use 'daily', 'monthly', or 'intraday'.")
        
    return {"chart_data": chart_data}
