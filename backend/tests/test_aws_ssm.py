"""Tests for backend/src/core/aws_ssm.py"""
import os
import sys
import time

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import aws_ssm  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    """各テスト前にキャッシュとクライアントをリセット (グローバル状態の干渉防止)。"""
    aws_ssm._cache.clear()
    aws_ssm._ssm_client = None
    yield
    aws_ssm._cache.clear()
    aws_ssm._ssm_client = None


# ===== get_secret =====
def test_get_secret_returns_value_from_ssm(mocker):
    # シナリオ: 初回取得 → boto3 を呼び、復号値を返す
    mock_client = mocker.Mock()
    mock_client.get_parameter.return_value = {"Parameter": {"Value": "secret123"}}
    mocker.patch.object(aws_ssm, "_get_client", return_value=mock_client)

    result = aws_ssm.get_secret("/projectbig/test/key")

    assert result == "secret123"
    mock_client.get_parameter.assert_called_once_with(
        Name="/projectbig/test/key", WithDecryption=True
    )


def test_get_secret_uses_cache_on_second_call(mocker):
    # シナリオ: 2回目の取得は SSM を呼ばずキャッシュから返す
    mock_client = mocker.Mock()
    mock_client.get_parameter.return_value = {"Parameter": {"Value": "cached_value"}}
    mocker.patch.object(aws_ssm, "_get_client", return_value=mock_client)

    first = aws_ssm.get_secret("/projectbig/test/cache_key")
    second = aws_ssm.get_secret("/projectbig/test/cache_key")

    assert first == "cached_value"
    assert second == "cached_value"
    assert mock_client.get_parameter.call_count == 1  # 2回目は呼ばれない


def test_get_secret_refetches_after_ttl_expiry(mocker):
    # シナリオ: TTL 期限切れ後は再取得する
    mock_client = mocker.Mock()
    mock_client.get_parameter.return_value = {"Parameter": {"Value": "v1"}}
    mocker.patch.object(aws_ssm, "_get_client", return_value=mock_client)

    aws_ssm.get_secret("/projectbig/test/ttl", ttl_sec=1)
    time.sleep(1.1)
    aws_ssm.get_secret("/projectbig/test/ttl", ttl_sec=1)

    assert mock_client.get_parameter.call_count == 2


def test_get_secret_raises_on_client_error(mocker):
    # シナリオ: AWS から AccessDenied 等が返ったら fail-fast で RuntimeError
    mock_client = mocker.Mock()
    mock_client.get_parameter.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetParameter",
    )
    mocker.patch.object(aws_ssm, "_get_client", return_value=mock_client)

    with pytest.raises(RuntimeError, match=r"\[AWS_SSM\]"):
        aws_ssm.get_secret("/projectbig/forbidden/key")


# ===== put_secret =====
def test_put_secret_enforces_standard_tier_and_aws_managed_kms(mocker):
    # シナリオ: put_secret は必ず Tier='Standard' / KeyId='alias/aws/ssm' を渡す (5重防御 #1, #5)
    mock_client = mocker.Mock()
    mocker.patch.object(aws_ssm, "_get_client", return_value=mock_client)

    aws_ssm.put_secret("/projectbig/test/wkey", "value", overwrite=True)

    mock_client.put_parameter.assert_called_once_with(
        Name="/projectbig/test/wkey",
        Value="value",
        Type="SecureString",
        KeyId="alias/aws/ssm",
        Tier="Standard",
        Overwrite=True,
    )


def test_put_secret_rejects_non_projectbig_path(mocker):
    # シナリオ: /projectbig/ プレフィクス外のパスは ValueError で拒否
    mocker.patch.object(aws_ssm, "_get_client")

    with pytest.raises(ValueError, match=r"Path must start with"):
        aws_ssm.put_secret("/other-project/foo", "value")


def test_put_secret_rejects_oversize_value(mocker):
    # シナリオ: 4KB Standard tier 上限を超えたら拒否 (Advanced tier 課金回避)
    mocker.patch.object(aws_ssm, "_get_client")
    huge = "x" * 5000

    with pytest.raises(ValueError, match=r"exceeds 4KB"):
        aws_ssm.put_secret("/projectbig/test/huge", huge)


def test_put_secret_invalidates_cache_on_write(mocker):
    # シナリオ: 書き込み後、該当パスのキャッシュは消える (次回 get で最新取得)
    mock_client = mocker.Mock()
    mocker.patch.object(aws_ssm, "_get_client", return_value=mock_client)

    # 事前にキャッシュを作る
    aws_ssm._cache["/projectbig/test/cached"] = ("old_val", time.time() + 9999)
    assert "/projectbig/test/cached" in aws_ssm._cache

    aws_ssm.put_secret("/projectbig/test/cached", "new_val")

    assert "/projectbig/test/cached" not in aws_ssm._cache


# ===== describe_with_prefix =====
def test_describe_with_prefix_returns_parameter_list(mocker):
    # シナリオ: describe_parameters paginator が返す Parameters を集約して返す
    mock_paginator = mocker.Mock()
    mock_paginator.paginate.return_value = [
        {"Parameters": [{"Name": "/projectbig/a", "LastModifiedDate": "x"}]},
        {"Parameters": [{"Name": "/projectbig/b", "LastModifiedDate": "y"}]},
    ]
    mock_client = mocker.Mock()
    mock_client.get_paginator.return_value = mock_paginator
    mocker.patch.object(aws_ssm, "_get_client", return_value=mock_client)

    result = aws_ssm.describe_with_prefix("/projectbig/")

    assert len(result) == 2
    assert result[0]["Name"] == "/projectbig/a"
    assert result[1]["Name"] == "/projectbig/b"


# ===== invalidate_cache =====
def test_invalidate_cache_specific_path(mocker):
    # シナリオ: 特定パスのキャッシュのみクリアし、他は残す
    aws_ssm._cache["/projectbig/keep"] = ("v1", time.time() + 9999)
    aws_ssm._cache["/projectbig/drop"] = ("v2", time.time() + 9999)

    aws_ssm.invalidate_cache("/projectbig/drop")

    assert "/projectbig/keep" in aws_ssm._cache
    assert "/projectbig/drop" not in aws_ssm._cache


def test_invalidate_cache_all_when_no_path():
    # シナリオ: path=None の場合は全キャッシュクリア
    aws_ssm._cache["/projectbig/a"] = ("v1", time.time() + 9999)
    aws_ssm._cache["/projectbig/b"] = ("v2", time.time() + 9999)

    aws_ssm.invalidate_cache()

    assert len(aws_ssm._cache) == 0
