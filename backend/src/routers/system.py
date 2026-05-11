"""
システム管理用 APIルーター
システムのステータス確認や、自動化スケジューラーの手動テスト実行を行う。

認証: 全エンドポイントが管理者トークン必須 (要件 §E.5.1 / §2.3)。
test/* エンドポイントはスケジューラジョブを手動発火する高権限操作のため特に保護必須。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from services.scheduler import system_scheduler
from core.security import verify_admin_token
from models.database import get_db

router = APIRouter(
    prefix="/api/v1/system",
    tags=["System"],
    dependencies=[Depends(verify_admin_token)]
)

@router.get("/status")
def get_system_status():
    """
    現在のシステムの稼働状況（スケジューラーが動いているか等）を返す
    """
    return {
        "scheduler_running": system_scheduler.scheduler.running,
        "active_jobs": len(system_scheduler.scheduler.get_jobs())
    }

@router.post("/test/profit_sweep")
def force_profit_sweep():
    """
    【テスト用】14:50の「全決済＆利益振替」ジョブを今すぐ強制実行する
    """
    system_scheduler.job_profit_sweep()
    return {"message": "Profit sweep job triggered manually."}

@router.post("/test/ai_report")
def force_ai_report():
    """
    【テスト用】16:00の「AI反省レポート自動生成」ジョブを今すぐ強制実行する
    """
    system_scheduler.job_generate_ai_report()
    return {"message": "AI report generation job triggered manually."}

@router.post("/test/buy")
def force_virtual_buy(
    symbol: str = "7203",
    name: str = "Toyota",
    qty: int = 100,
    db: Session = Depends(get_db),
):
    """
    【テスト用】ペーパートレードエンジンに仮想の買い注文を入れる。
    db を渡すことで kill switch アクティブ時はブロックされる (要件 §6 / 絶対禁止 3)。
    """
    from services.paper_trader import paper_trader_engine
    import os

    if os.getenv("TRADE_MODE", "PAPER").upper() != "PAPER":
        return {"message": "This endpoint only works in PAPER mode."}

    success = paper_trader_engine.execute_virtual_order(symbol, name, qty, True, db=db)
    if success:
        return {"message": f"Successfully placed paper buy order for {qty} shares of {symbol}."}
    else:
        return {"message": "Failed to place paper buy order (insufficient funds, price unavailable, or kill switch active)."}
