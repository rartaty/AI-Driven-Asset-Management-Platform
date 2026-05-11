"""Tests for backend/src/core/kill_switch.py — bucket 別キルスイッチ (Phase 6 / ADR-0010)

カバー範囲:
- is_active(session, bucket=...) の OR 判定
- assert_inactive_for_entry(session, bucket=...) の bucket 引数対応
- activate_bucket / deactivate_bucket の挙動
- 全体フラグ ON で全 bucket がブロックされること (非対称オーバーライド)
- 不正な bucket 名で ValueError
- 解除確認文字列必須 (絶対禁止 #3 準拠)
"""
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import kill_switch  # noqa: E402
from core.kill_switch import (  # noqa: E402
    DEACTIVATE_CONFIRMATION_PHRASE,
    KillSwitchError,
    activate,
    activate_bucket,
    assert_inactive_for_entry,
    deactivate_bucket,
    is_active,
)
from models.schema import Base, User_Settings  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _mute_discord(mocker):
    mocker.patch("core.kill_switch.notify_critical")
    mocker.patch("core.kill_switch.notify_system")


# ===== is_active: bucket 引数 =====

class TestIsActiveBucket:
    def test_no_flags_set_returns_false(self, session):
        assert is_active(session) is False
        for bucket in ["Passive", "Long_Solid", "Long_Growth", "Short"]:
            assert is_active(session, bucket=bucket) is False

    def test_whole_flag_on_blocks_all_buckets(self, session):
        """全体フラグ ON で全 bucket がブロックされる (非対称オーバーライド)。"""
        activate(session, reason="test", manual=True)
        assert is_active(session) is True
        for bucket in ["Passive", "Long_Solid", "Long_Growth", "Short"]:
            assert is_active(session, bucket=bucket) is True, f"bucket={bucket} should be blocked by whole flag"

    def test_bucket_flag_isolation(self, session):
        """bucket 別フラグは該当 bucket のみブロック、他はパス。"""
        activate_bucket(session, bucket="Short", reason="test", manual=True)
        assert is_active(session) is False  # 全体フラグはまだ OFF
        assert is_active(session, bucket="Short") is True
        assert is_active(session, bucket="Passive") is False
        assert is_active(session, bucket="Long_Solid") is False
        assert is_active(session, bucket="Long_Growth") is False

    def test_unknown_bucket_raises_value_error(self, session):
        with pytest.raises(ValueError, match="unknown bucket"):
            is_active(session, bucket="NotABucket")


# ===== assert_inactive_for_entry: bucket 引数 =====

class TestAssertInactiveForEntryBucket:
    def test_passes_when_no_flags(self, session):
        assert_inactive_for_entry(session)
        assert_inactive_for_entry(session, bucket="Short")

    def test_raises_when_whole_flag_on_with_any_bucket(self, session):
        activate(session, reason="test", manual=True)
        with pytest.raises(KillSwitchError):
            assert_inactive_for_entry(session)
        with pytest.raises(KillSwitchError):
            assert_inactive_for_entry(session, bucket="Short")
        with pytest.raises(KillSwitchError):
            assert_inactive_for_entry(session, bucket="Passive")

    def test_raises_only_for_target_bucket(self, session):
        activate_bucket(session, bucket="Long_Growth", reason="test", manual=True)
        # Long_Growth はブロックされる
        with pytest.raises(KillSwitchError):
            assert_inactive_for_entry(session, bucket="Long_Growth")
        # 他 bucket はパス
        assert_inactive_for_entry(session, bucket="Short")
        assert_inactive_for_entry(session, bucket="Passive")
        assert_inactive_for_entry(session, bucket="Long_Solid")
        # bucket 引数なし (全体扱い) もパス
        assert_inactive_for_entry(session)


# ===== activate_bucket / deactivate_bucket =====

class TestActivateBucket:
    def test_activate_bucket_sets_correct_flag(self, session):
        activate_bucket(session, bucket="Passive", reason="test", manual=True)
        settings = session.query(User_Settings).first()
        assert settings.is_kill_switch_active_passive is True
        assert settings.is_kill_switch_active is False  # 全体フラグは触らない
        assert settings.is_kill_switch_active_short is False

    def test_activate_bucket_idempotent(self, session):
        activate_bucket(session, bucket="Short", reason="first", manual=True)
        activate_bucket(session, bucket="Short", reason="second", manual=True)
        # エラーなく完了 (ログのみ)
        assert is_active(session, bucket="Short") is True

    def test_activate_bucket_invalid_name_raises(self, session):
        with pytest.raises(ValueError):
            activate_bucket(session, bucket="UnknownBucket", reason="test")

    def test_activate_bucket_none_raises(self, session):
        with pytest.raises(ValueError, match="non-None"):
            activate_bucket(session, bucket=None, reason="test")  # type: ignore[arg-type]


class TestDeactivateBucket:
    def test_deactivate_bucket_requires_confirmation(self, session):
        activate_bucket(session, bucket="Short", reason="test", manual=True)
        with pytest.raises(ValueError, match="explicit confirmation"):
            deactivate_bucket(session, bucket="Short", reason="oops", confirmation="wrong")
        # まだ Active のまま
        assert is_active(session, bucket="Short") is True

    def test_deactivate_bucket_with_correct_confirmation(self, session):
        activate_bucket(session, bucket="Short", reason="test", manual=True)
        deactivate_bucket(
            session, bucket="Short", reason="ok",
            confirmation=DEACTIVATE_CONFIRMATION_PHRASE,
        )
        assert is_active(session, bucket="Short") is False

    def test_deactivate_bucket_idempotent_when_already_inactive(self, session):
        # 一度も活性化されていない状態でも例外なく完了
        deactivate_bucket(
            session, bucket="Long_Solid", reason="noop",
            confirmation=DEACTIVATE_CONFIRMATION_PHRASE,
        )
        assert is_active(session, bucket="Long_Solid") is False

    def test_deactivate_bucket_only_affects_target(self, session):
        activate_bucket(session, bucket="Short", reason="test", manual=True)
        activate_bucket(session, bucket="Passive", reason="test", manual=True)
        deactivate_bucket(
            session, bucket="Short", reason="ok",
            confirmation=DEACTIVATE_CONFIRMATION_PHRASE,
        )
        assert is_active(session, bucket="Short") is False
        assert is_active(session, bucket="Passive") is True  # 別 bucket は維持