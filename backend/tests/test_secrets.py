"""Tests for backend/src/core/secrets.py"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import secrets

@pytest.fixture
def mock_ssm_get_secret(mocker):
    return mocker.patch("core.aws_ssm.get_secret")

def test_get_secret_returns_value_from_ssm(mock_ssm_get_secret):
    mock_ssm_get_secret.return_value = "ssm_password"
    
    result = secrets.get_secret("KABUCOM_API_PASSWORD")
    
    assert result == "ssm_password"
    mock_ssm_get_secret.assert_called_once_with("/projectbig/kabucom/api-password")

def test_get_secret_falls_back_to_env_var_when_ssm_fails(mock_ssm_get_secret, monkeypatch):
    mock_ssm_get_secret.side_effect = Exception("SSM Error")
    monkeypatch.setenv("KABUCOM_API_PASSWORD", "env_password")
    
    result = secrets.get_secret("KABUCOM_API_PASSWORD")
    
    assert result == "env_password"

def test_get_secret_falls_back_to_env_var_when_ssm_returns_empty(mock_ssm_get_secret, monkeypatch):
    mock_ssm_get_secret.return_value = ""
    monkeypatch.setenv("KABUCOM_API_PASSWORD", "env_password")
    
    result = secrets.get_secret("KABUCOM_API_PASSWORD")
    
    assert result == "env_password"

def test_get_secret_falls_back_to_env_var_when_unknown_logical_key(monkeypatch):
    monkeypatch.setenv("UNKNOWN_KEY", "env_value")
    
    result = secrets.get_secret("UNKNOWN_KEY")
    
    assert result == "env_value"

def test_get_secret_raises_value_error_when_both_fail(mock_ssm_get_secret, monkeypatch):
    mock_ssm_get_secret.side_effect = Exception("SSM Error")
    monkeypatch.delenv("KABUCOM_API_PASSWORD", raising=False)
    
    with pytest.raises(ValueError, match=r"Could not resolve secret for 'KABUCOM_API_PASSWORD'"):
        secrets.get_secret("KABUCOM_API_PASSWORD")
