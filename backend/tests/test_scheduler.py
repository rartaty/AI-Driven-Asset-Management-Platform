import pytest
import os
import sys
from datetime import datetime, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from services.scheduler import SystemScheduler

def test_job_short_term_trade_time_filter(mocker):
    """時間外 (09:00) は早期 return、取引時間 (10:00) は処理続行を確認。

    Phase 7 P7-6: job_short_term_trade に Asset_Master query + evaluate_vwap_signal を統合したため、
    DB セッション + 戦略呼び出しもモックする。
    """
    scheduler = SystemScheduler()

    # Mock logger to see if it reaches the condition
    mock_logger = mocker.patch("services.scheduler.logger.info")

    class MockDatetime:
        def __init__(self, t):
            self.t = t
        def time(self):
            return self.t

    mock_now = mocker.patch("services.scheduler.datetime")

    # Phase 7: 取引時間内の場合に呼ばれる依存を全てモック
    mocker.patch("services.scheduler.SessionLocal")
    mock_eval = mocker.patch(
        "strategy.vwap_short.evaluate_vwap_signal",
        return_value={"signal": "HOLD", "reason": "test", "z_score": 0.0,
                      "vwap": 0, "current_price": 0, "tick_count": 0, "ticker": ""},
    )

    # Test 09:00 (too early) — 早期 return で何も呼ばれないこと
    mock_now.now.return_value = MockDatetime(time(9, 0, 0))
    scheduler.job_short_term_trade()
    mock_logger.assert_not_called()
    mock_eval.assert_not_called()

    # Test 10:00 (morning session) — 処理続行することを logger 呼び出しで確認
    mock_now.now.return_value = MockDatetime(time(10, 0, 0))
    scheduler.job_short_term_trade()
    mock_logger.assert_called()

def test_job_morning_sync(mocker):
    scheduler = SystemScheduler()
    
    # Mock Kabucom
    mock_kabucom = mocker.patch("api.kabucom.KabucomAPIClient.authenticate", return_value=True)
    mock_notify = mocker.patch("services.scheduler.notify_system")
    mock_sync = mocker.patch.object(scheduler, "job_sync_portfolio")
    
    scheduler.job_morning_sync()
    
    mock_kabucom.assert_called_once()
    mock_notify.assert_called_once_with("Morning Sync: Kabucom API authenticated.", component="Scheduler")
    mock_sync.assert_called_once()

def test_job_weekly_rebalance(mocker):
    """週次リバランス (P9): Target_Portfolio + portfolio_sync 経由の動的比率を使用。"""
    scheduler = SystemScheduler()

    # 相場環境とVIXギア判定をモック (mode=ATTACK)
    mock_context = mocker.MagicMock(vix=42.0)
    mocker.patch("services.market_context.update_market_context", return_value=mock_context)
    mocker.patch("services.market_context.evaluate_vix_gear", return_value="ATTACK")

    # 実評価額 (Daily_Asset_Snapshot 整合フィールド)
    mocker.patch.object(
        scheduler.portfolio_service, "get_consolidated_portfolio",
        return_value={
            "trust_value": 5000000,
            "long_solid_value": 2000000,
            "long_growth_value": 1000000,
            "short_term_market_value": 2000000,
        },
    )

    # Target_Portfolio から動的比率を取得 (cash=5%, trust=40%, stocks=55% = ATTACK)
    mocker.patch(
        "services.target_portfolio.get_active_ratios",
        return_value={"cash": 0.05, "trust": 0.40, "stocks": 0.55},
    )

    mock_calc = mocker.patch(
        "strategy.passive_core.calculate_rebalance_amounts",
        return_value={"trust_adj": 0, "long_adj": 0, "short_adj": 0},
    )
    mock_notify = mocker.patch("services.scheduler.notify_system")

    scheduler.job_weekly_rebalance()

    # invested_pct = 0.40 + 0.55 = 0.95
    # trust ratio = 0.40 / 0.95 ≈ 0.421
    # long ratio  = (0.55 * 0.60) / 0.95 ≈ 0.347
    # short ratio = (0.55 * 0.40) / 0.95 ≈ 0.232
    args, kwargs = mock_calc.call_args
    assert kwargs["trust_value"] == 5000000
    assert kwargs["long_value"] == 2000000 + 1000000  # long_solid + long_growth
    assert kwargs["short_value"] == 2000000
    assert kwargs["vix_triggered"] is True
    ratios = kwargs["target_ratios"]
    assert ratios["trust"] == pytest.approx(0.40 / 0.95, rel=1e-3)
    assert ratios["long"] == pytest.approx((0.55 * 0.60) / 0.95, rel=1e-3)
    assert ratios["short"] == pytest.approx((0.55 * 0.40) / 0.95, rel=1e-3)

    mock_notify.assert_called_once_with(
        "Rebalance computed (VIX mode: ATTACK)", component="Scheduler"
    )


def test_job_quarterly_review(mocker):
    """四半期定期見直し (P9): Target_Portfolio へ baseline 比率を書込。"""
    scheduler = SystemScheduler()

    mock_target = mocker.MagicMock(
        cash_target_pct=0.10, trust_target_pct=0.50, stocks_target_pct=0.40,
    )
    mock_write = mocker.patch(
        "services.target_portfolio.write_for_quarterly_review",
        return_value=mock_target,
    )
    mock_notify = mocker.patch("services.scheduler.notify_system")

    scheduler.job_quarterly_review()

    mock_write.assert_called_once()
    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "Quarterly review" in msg
    assert "cash=0.10" in msg
    assert "trust=0.50" in msg
    assert "stocks=0.40" in msg


# ===== Phase 6 P6-2: backup / export job カバレッジ (paper-trade-validator Med 対応) =====

def test_job_backup_daily_success(mocker, tmp_path):
    """日次バックアップジョブ: scripts.backup_db.run_daily_backup の呼び出しと notify を確認。"""
    scheduler = SystemScheduler()

    # backup_db.run_daily_backup をモック
    fake_dest = tmp_path / "data-20260509.db"
    fake_dest.write_bytes(b"")
    mock_run = mocker.patch("backup_db.run_daily_backup", return_value=fake_dest)
    mock_notify = mocker.patch("services.scheduler.notify_system")

    scheduler.job_backup_daily()

    mock_run.assert_called_once()
    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "Daily backup completed" in msg
    assert "data-20260509.db" in msg


def test_job_backup_daily_failure_does_not_propagate(mocker):
    """バックアップ失敗時もメイン処理をブロックしない (非ブロッキング設計)。"""
    scheduler = SystemScheduler()

    mocker.patch("backup_db.run_daily_backup", side_effect=RuntimeError("disk full"))
    mock_notify = mocker.patch("services.scheduler.notify_system")

    # 例外伝播せず完了
    scheduler.job_backup_daily()

    # 失敗通知が送られる
    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "Daily backup failed" in msg


def test_job_export_buckets_weekly_success(mocker, tmp_path):
    """週次 bucket 別 JSON 論理 dump ジョブ: export_all_buckets 呼び出しと notify を確認。"""
    scheduler = SystemScheduler()

    fake_paths = [tmp_path / f"{b}.json" for b in ["passive", "long_solid", "long_growth", "short"]]
    mock_export = mocker.patch("export_buckets.export_all_buckets", return_value=fake_paths)
    mocker.patch("services.scheduler.SessionLocal")  # DB セッションをモック
    mock_notify = mocker.patch("services.scheduler.notify_system")

    scheduler.job_export_buckets_weekly()

    mock_export.assert_called_once()
    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "Weekly bucket export completed" in msg
    assert "4 files" in msg


def test_job_export_buckets_weekly_failure_does_not_propagate(mocker):
    """週次 export 失敗時もメイン処理をブロックしない。"""
    scheduler = SystemScheduler()

    mocker.patch("services.scheduler.SessionLocal")
    mocker.patch("export_buckets.export_all_buckets", side_effect=RuntimeError("export failed"))
    mock_notify = mocker.patch("services.scheduler.notify_system")

    scheduler.job_export_buckets_weekly()

    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "Weekly bucket export failed" in msg
