"""
データベース接続の初期設定 (SQLAlchemy)
環境変数 DATABASE_URL に基づいて接続先を切り替えます。
デフォルトはローカル開発用の SQLite (data.db) です。
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 環境変数からデータベースURLを取得。無ければSQLiteを使用。
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db")

# SQLiteの場合、別スレッドからのアクセスを許可するフラグが必要
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 全テーブルの親となるBaseクラス
Base = declarative_base()

def get_db():
    """FastAPIのDependency Injection用のDBセッション取得関数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
