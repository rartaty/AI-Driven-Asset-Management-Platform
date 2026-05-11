"""
Web認証 (M4) 用モジュール
FastAPIのルーターを無断アクセスから保護するためのトークン認証機能を提供します。
"""
import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Swagger UI に "Authorize" ボタンを表示させるためのスキーム定義
security_scheme = HTTPBearer(auto_error=True)

def verify_admin_token(credentials: HTTPAuthorizationCredentials = Security(security_scheme)) -> str:
    """
    リクエストヘッダの Authorization: Bearer <token> を検証します。
    正しい管理者トークンでなければ 401 Unauthorized を返します。
    """
    # 簡易的に環境変数から期待するトークンを読み込みます
    # ローカル開発や未設定時は安全のためデフォルト値を使用
    expected_token = os.getenv("ADMIN_API_TOKEN", "dev-token-secret")
    
    if credentials.credentials != expected_token:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    return credentials.credentials
