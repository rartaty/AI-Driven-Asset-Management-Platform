"""
FastAPI アプリケーション本体 (Entry Point)
データベースの初期化と、各APIルーターの登録を行います。
"""
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# DB基盤とスキーマのインポート
from models.database import engine, Base, get_db
from models import schema

from contextlib import asynccontextmanager

# APIルーターのインポート
from routers import portfolio, reports, system, analytics
from core.security import verify_admin_token

# スケジューラーのインポート
from services.scheduler import system_scheduler

# アプリケーション起動時にDBテーブルを自動生成（既に存在する場合はスキップ）
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # アプリケーション起動時の処理
    system_scheduler.start()
    yield
    # アプリケーション終了時の処理
    system_scheduler.shutdown()

app = FastAPI(
    title="Project Big Tester - Backend API",
    description="資産管理・自動運用システムのバックエンドAPI",
    version="1.0.0",
    lifespan=lifespan
)

# CORS設定 (フロントエンドからのアクセスを許可)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーターの登録 (すべて認証保護)
app.include_router(portfolio.router, dependencies=[Depends(verify_admin_token)])
app.include_router(reports.router, dependencies=[Depends(verify_admin_token)])
app.include_router(system.router, dependencies=[Depends(verify_admin_token)])
app.include_router(analytics.router, dependencies=[Depends(verify_admin_token)])

@app.get("/")
def read_root():
    return {"message": "Welcome to Project Big Tester API. Please use /api/health to check status."}

@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    """
    システムの死活監視とDB接続チェック
    """
    # DB接続が正常か確認するため、設定テーブルを1件取得してみる（テーブルが存在するかのチェック）
    db_status = "OK"
    try:
        db.query(schema.User_Settings).first()
    except Exception as e:
        db_status = f"Error: {str(e)}"
        
    return {
        "status": "active",
        "database_connection": db_status
    }
