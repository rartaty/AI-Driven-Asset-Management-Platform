"""
AIレポートアルバム用 APIルーター

認証: 全エンドポイントが管理者トークン必須 (要件 §E.5.1 / §2.3)。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uuid

from models.database import get_db
from services.scheduler import system_scheduler
from core.security import verify_admin_token

router = APIRouter(
    prefix="/api/v1/reports",
    tags=["Reports"],
    dependencies=[Depends(verify_admin_token)]
)
# system_scheduler が持っている analyzer インスタンスを使い回す（キャッシュ共有のため）
analyzer = system_scheduler.ai_analyzer

@router.get("/")
def get_reports(db: Session = Depends(get_db)):
    """
    フロントエンドのアルバム画面に表示する過去のレポート一覧を取得する
    """
    # TODO: 実際には DB (AI_Report_Album テーブル) から取得する。
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # スケジューラー（AI生成ジョブ）によって既に生成・キャッシュされたレポートを取得する
    # もしまだ生成されていなければ、プレースホルダーを表示する
    if analyzer.latest_report:
        latest_ai_summary = analyzer.latest_report
    else:
        latest_ai_summary = "現在、AIが本日のトレード履歴を分析中です...しばらく経ってから画面を更新してください。"

    # フロントエンド向けのリスト（仮の履歴＋最新の生成結果）
    reports = [
        {
            "report_id": str(uuid.uuid4()),
            "report_type": "Daily",
            "target_date": today_str,
            "file_path": f"/reports/daily_{today_str}.md",
            "ai_summary": latest_ai_summary
        },
        {
            "report_id": str(uuid.uuid4()),
            "report_type": "Monthly",
            "target_date": "2024-03-31",
            "file_path": "/reports/monthly_202403.md",
            "ai_summary": "# 月次サマリー\n\n3月はパッシブ運用（投資信託）が好調で、資産全体を押し上げました。短期トレードの勝率は65%でした。"
        }
    ]
    
    return reports

@router.post("/generate_now")
def trigger_report_generation(db: Session = Depends(get_db)):
    """
    （手動テスト用）現在のトレード履歴から直ちにAIレポートを生成し保存する
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    mock_trade_data = [{"msg": "This is mock data triggered manually"}]
    
    summary = analyzer.generate_daily_report(mock_trade_data, today_str)
    
    # TODO: DBへの保存ロジック
    
    return {"status": "success", "message": "Report generated", "content": summary}
