from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Dict, Any

from models.database import get_db
from models.schema import Trade_Logs, System_Logs
from core.security import verify_admin_token

router = APIRouter(
    prefix="/api/v1/analytics",
    tags=["Analytics"],
    dependencies=[Depends(verify_admin_token)]
)

@router.get("/timeline")
def get_ai_timeline(db: Session = Depends(get_db), limit: int = Query(20, le=100)):
    """
    AIの意思決定ログとシステムの重要イベントを統合したタイムラインを取得
    """
    timeline = []
    
    # 1. トレード履歴とAIの判断理由 (Loss Drilldown に使用)
    trades = db.query(Trade_Logs).order_by(desc(Trade_Logs.timestamp)).limit(limit).all()
    for t in trades:
        is_loss = t.pnl is not None and t.pnl < 0
        timeline.append({
            "id": f"trade_{t.id}",
            "type": "Trade",
            "timestamp": t.timestamp.isoformat(),
            "symbol": t.ticker_symbol,
            "action": t.action,
            "quantity": t.quantity,
            "price": t.price,
            "pnl": t.pnl,
            "is_loss": is_loss,
            "decision_reason": t.decision_reason
        })
        
    # 2. システムイベント
    system_events = db.query(System_Logs).order_by(desc(System_Logs.timestamp)).limit(limit).all()
    for s in system_events:
        timeline.append({
            "id": f"sys_{s.id}",
            "type": "SystemEvent",
            "timestamp": s.timestamp.isoformat(),
            "level": s.level,
            "component": s.component,
            "event": s.event,
            "payload": s.payload
        })
        
    # 時系列降順でソート
    timeline.sort(key=lambda x: x["timestamp"], reverse=True)
    
    return {"timeline": timeline[:limit]}
