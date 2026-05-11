"""Tests for backend/src/core/kill_switch.py"""
import os
import sys
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import kill_switch  # noqa: E402
from core.kill_switch import (  # noqa: E402
    DEACTIVATE_CONFIRMATION_PHRASE,
    KillSwitchError,
    activate,
    assert_inactive_for_entry,
    check_drawdown_and_trigger,
    compute_drawdown,
    deactivate,
    is_active,
)
from models.schema import Base, Daily_Asset_Snapshot, User_Settings  # noqa: E402


# ===== Fixtures =====
@pytest.fixture
def session():
    """In-memory SQLite セッション (testing.md §モック方針: DBスキーマ整合性は実DB)。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _mute_discord(mocker):
    """Discord 通知は全テストで mock (実 webhook を叩かない)。"""
    mocker.patch.object(kill_switch, "notify_critical", return_value=True)
    mocker.patch.object(kill_switch, "notify_system", return_value=True)


def _add_snapshot(session, day: date, total_value: int):
    """テスト用ヘルパ: 指定日の snapshot を作成 (投資ポートフォリオ評価額のみ trust_value に集約)。"""
    s = Daily_Asset_Snapshot(
        date=day,
        bank_balance=1_000_000,           # 生活防衛費 (DD 計算には含めない)
        buying_power=0,
        trust_value=total_value,
        long_solid_value=0,
        long_growth_value=0,
        short_term_capital=0,
        short_term_market_value=0,
    )
    session.add(s)
    session.commit()


# ===== compute_drawdown =====
def test_compute_drawdown_no_snapshots_returns_none(session):
    # シナリオ: snapshot ゼロ件 → None
    assert compute_drawdown(session) is None


def test_compute_drawdown_single_snapshot_zero_drawdown(session):
    # シナリオ: 1件のみ → peak == current → DD = 0
    _add_snapshot(session, date(2026, 5, 1), 1_000_000)
    report = compute_drawdown(session)
    assert report.drawdown_pct == 0.0
    assert report.snapshot_count == 1


def test_compute_drawdown_normal_decline(session):
    # 正常系: peak 1,000,000 → current 970,000 = -3.0%
    _add_snapshot(session, date(2026, 5, 1), 1_000_000)
    _add_snapshot(session, date(2026, 5, 2), 970_000)

    report = compute_drawdown(session)
    assert report.peak_value == 1_000_000
    assert report.current_value == 970_000
    assert report.drawdown_pct == pytest.approx(-0.03, abs=1e-6)


def test_compute_drawdown_excludes_bank_balance(session):
    # 境界値: bank_balance を変動させても DD には影響しない (生活防衛費を除外)
    _add_snapshot(session, date(2026, 5, 1), 1_000_000)
    s = Daily_Asset_Snapshot(
        date=date(2026, 5, 2),
        bank_balance=999_999_999,  # bank だけ大きい
        trust_value=1_000_000,     # 投資評価額は同じ
    )
    session.add(s)
    session.commit()

    report = compute_drawdown(session)
    assert report.drawdown_pct == 0.0  # 投資評価額は変わってないので DD ゼロ


def test_compute_drawdown_zero_peak_returns_zero(session):
    # 異常系: 全 snapshot ゼロ → 除算ガード発動して DD=0
    s = Daily_Asset_Snapshot(date=date(2026, 5, 1))
    session.add(s)
    session.commit()

    report = compute_drawdown(session)
    assert report.drawdown_pct == 0.0


# ===== check_drawdown_and_trigger =====
def test_check_drawdown_triggers_at_threshold_breach(session):
    # 必須検証ケース (testing.md): DD -3% 超で is_kill_switch_active=True
    _add_snapshot(session, date(2026, 5, 1), 1_000_000)
    _add_snapshot(session, date(2026, 5, 2), 960_000)  # -4% DD

    fired = check_drawdown_and_trigger(session)
    assert fired is True
    assert is_active(session) is True


def test_check_drawdown_not_triggered_within_threshold(session):
    # 境界値: -3% ちょうど未満なら発動しない (例: -2.5%)
    _add_snapshot(session, date(2026, 5, 1), 1_000_000)
    _add_snapshot(session, date(2026, 5, 2), 975_000)  # -2.5%

    fired = check_drawdown_and_trigger(session)
    assert fired is False
    assert is_active(session) is False


def test_check_drawdown_exact_threshold_triggers(session):
    # 境界値: -3.0% ちょうど → 発動 (`<=` 比較)
    _add_snapshot(session, date(2026, 5, 1), 1_000_000)
    _add_snapshot(session, date(2026, 5, 2), 970_000)  # ちょうど -3%

    fired = check_drawdown_and_trigger(session)
    assert fired is True


def test_check_drawdown_does_not_re_fire_when_already_active(session):
    # シナリオ: 既発動状態では再度活性化しない (= return False)
    activate(session, reason="test pre-activation", manual=True)
    _add_snapshot(session, date(2026, 5, 1), 1_000_000)
    _add_snapshot(session, date(2026, 5, 2), 900_000)  # -10%

    fired = check_drawdown_and_trigger(session)
    assert fired is False  # 新規発動ではないため False


# ===== activate =====
def test_activate_writes_db_flag(session):
    # 正常系: activate で DB フラグ True
    activate(session, reason="manual test", manual=True)
    assert is_active(session) is True


def test_activate_sends_critical_discord(session, mocker):
    # シナリオ: activate で notify_critical が呼ばれる
    spy = mocker.patch.object(kill_switch, "notify_critical", return_value=True)
    activate(session, reason="DD breach", manual=False)
    spy.assert_called_once()
    args, kwargs = spy.call_args
    assert "kill switch activated" in kwargs["message"]
    assert kwargs["component"] == "KillSwitch"


def test_activate_idempotent_when_already_active(session, mocker):
    # シナリオ: 既発動状態で activate を再呼出 → 通知は飛ばない (重複 warn 抑制)
    activate(session, reason="first", manual=True)
    spy = mocker.patch.object(kill_switch, "notify_critical", return_value=True)
    activate(session, reason="second", manual=True)
    spy.assert_not_called()


# ===== deactivate =====
def test_deactivate_requires_confirmation_phrase(session):
    # 必須検証ケース (絶対禁止 #3): 確認文字列なしでは ValueError
    activate(session, reason="setup", manual=True)
    with pytest.raises(ValueError, match="explicit confirmation"):
        deactivate(session, reason="user requested", confirmation="wrong")
    assert is_active(session) is True  # 解除されない


def test_deactivate_with_correct_confirmation_succeeds(session):
    # 正常系: 確認文字列が正しければ DB フラグ False
    activate(session, reason="setup", manual=True)
    deactivate(session, reason="user confirmed risk", confirmation=DEACTIVATE_CONFIRMATION_PHRASE)
    assert is_active(session) is False


def test_deactivate_idempotent_when_already_inactive(session, mocker):
    # シナリオ: 既解除状態で deactivate → 通知飛ばない
    spy = mocker.patch.object(kill_switch, "notify_system", return_value=True)
    deactivate(session, reason="noop", confirmation=DEACTIVATE_CONFIRMATION_PHRASE)
    spy.assert_not_called()


# ===== assert_inactive_for_entry (V2 spec §4 非対称オーバーライド) =====
def test_assert_inactive_for_entry_passes_when_not_active(session):
    # 正常系: 発動していなければ例外なし
    assert is_active(session) is False
    assert_inactive_for_entry(session)  # raises 無し


def test_assert_inactive_for_entry_raises_when_active(session):
    # 必須検証ケース: 発動中の新規エントリー試行は KillSwitchError
    activate(session, reason="test", manual=True)
    with pytest.raises(KillSwitchError, match="new entry blocked"):
        assert_inactive_for_entry(session)


def test_assert_inactive_for_entry_sends_block_notification(session, mocker):
    # シナリオ: ブロック発生時 Discord 通知される
    activate(session, reason="setup", manual=True)
    spy = mocker.patch.object(kill_switch, "notify_critical", return_value=True)
    with pytest.raises(KillSwitchError):
        assert_inactive_for_entry(session)
    spy.assert_called_once()


# ===== User_Settings 自動作成 =====
def test_user_settings_auto_created_when_missing(session):
    # シナリオ: User_Settings が DB に存在しなくても is_active() で自動作成される
    assert session.query(User_Settings).count() == 0
    is_active(session)
    assert session.query(User_Settings).count() == 1
