"""Tests for backend/src/core/security.py"""
import os
import sys

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import security

def test_verify_admin_token_success(monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", "my-secret-token")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="my-secret-token")
    
    result = security.verify_admin_token(credentials)
    
    assert result == "my-secret-token"

def test_verify_admin_token_failure(monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", "my-secret-token")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-token")
    
    with pytest.raises(HTTPException) as excinfo:
        security.verify_admin_token(credentials)
        
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid authentication token"

def test_verify_admin_token_uses_default_when_env_not_set(monkeypatch):
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="dev-token-secret")
    
    result = security.verify_admin_token(credentials)
    
    assert result == "dev-token-secret"
