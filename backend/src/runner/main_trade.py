"""
本番稼働エントリポイント (M9 / Phase 8a / ADR-0011)

要件 §6 (Fail-safe) / CLAUDE.md 絶対禁止 1 (REAL 切替に明示確認必須)

責任:
- 起動前チェック (preflight): SSM / Kill Switch / TRADE_MODE 確認
- スケジューラ起動 + 永続稼働ループ
- SIGINT/SIGTERM での安全停止 (scheduler.shutdown 呼び出し)

REAL モード起動条件:
- TRADE_MODE=REAL かつ RUNNER_REAL_CONFIRM='I_UNDERSTAND_REAL_TRADING_RISK' が必要
- 環境変数で確認文字列を要求することで誤起動を物理的に阻止
- PAPER モードでの起動は --allow-paper フラグ明示時のみ (smoke test 用途)

使い方:
    # PAPER モードで smoke test
    $env:TRADE_MODE="PAPER"
    python -m runner.main_trade --allow-paper

    # REAL 本番稼働
    $env:TRADE_MODE="REAL"
    $env:RUNNER_REAL_CONFIRM="I_UNDERSTAND_REAL_TRADING_RISK"
    python -m runner.main_trade
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ===== 定数 =====
REAL_MODE_CONFIRMATION = "I_UNDERSTAND_REAL_TRADING_RISK"


class PreflightError(Exception):
    """起動前チェック失敗。SystemExit(1) のラッパー。"""


# ===== 起動前チェック =====

def check_trade_mode(allow_paper: bool = False) -> str:
    """TRADE_MODE を取得し、各モードの起動条件を検証。

    Phase 8c (ADR-0012): TRADE_MODE 三値化
    - PAPER     : --allow-paper フラグ必須 (smoke test 用途)
    - PAPER_LIVE: 確認文字列不要 (実マネー使わない・ただし kabu Station 必須)
    - REAL      : RUNNER_REAL_CONFIRM 環境変数必須 (CLAUDE.md 絶対禁止 1)

    :param allow_paper: True のとき PAPER 起動を許可 (smoke test)
    :return: 検証済の trade_mode 文字列
    :raises PreflightError: 検証失敗
    """
    mode = os.getenv("TRADE_MODE", "PAPER").upper()
    if mode == "REAL":
        confirmation = os.getenv("RUNNER_REAL_CONFIRM")
        if confirmation != REAL_MODE_CONFIRMATION:
            raise PreflightError(
                f"REAL mode startup requires explicit confirmation. "
                f"Set RUNNER_REAL_CONFIRM='{REAL_MODE_CONFIRMATION}' (CLAUDE.md 絶対禁止 1)"
            )
        return mode
    if mode == "PAPER_LIVE":
        # 実マネー使わないため確認文字列不要、ただし kabu Station 必須 (preflight で smoke test)
        return mode
    if mode == "PAPER":
        if not allow_paper:
            raise PreflightError(
                f"main_trade.py is REAL/PAPER_LIVE-mode entry by default. "
                f"Got TRADE_MODE={mode}. Pass --allow-paper for smoke test."
            )
        return mode
    raise PreflightError(
        f"Unknown TRADE_MODE: {mode}. Valid: PAPER | PAPER_LIVE | REAL"
    )


def check_ssm_reachable() -> None:
    """SSM Parameter Store への到達性確認 (smoke test 用に既存 path で 1 件取得試行)。

    :raises PreflightError: SSM 取得失敗
    """
    try:
        from core.secrets import get_secret  # type: ignore
        # 起動時必ず使う鍵を 1 件 smoke test
        get_secret("DISCORD_WEBHOOK_SYSTEM")
    except Exception as e:
        raise PreflightError(f"SSM preflight failed: {e}")


def check_kill_switch_inactive() -> None:
    """Kill Switch 全体フラグが Inactive であることを確認。

    Active 状態で本番起動しても発注はブロックされるが、未解決のまま稼働するのは
    INCIDENT_RESPONSE.md §4 解除条件不満たしの状態。起動時に明示拒否する。

    :raises PreflightError: Kill Switch が Active
    """
    try:
        from core.kill_switch import is_active  # type: ignore
        from models.database import SessionLocal  # type: ignore
    except Exception as e:
        raise PreflightError(f"Kill switch check import failed: {e}")

    db = SessionLocal()
    try:
        try:
            active = is_active(db)
        except Exception as e:
            # is_active() の例外は呼出側の except PreflightError をすり抜けるため
            # 必ず PreflightError でラップ (paper-trade-validator Med 対応)
            raise PreflightError(f"Kill switch state check failed: {e}")
        if active:
            raise PreflightError(
                "Kill switch is ACTIVE. Resolve incident first "
                "(see docs/operations/INCIDENT_RESPONSE.md §4) before starting runner."
            )
    finally:
        db.close()


def check_kabu_authenticate_for_live() -> None:
    """PAPER_LIVE / REAL モードで kabu Station の到達性確認 (Phase 8c / ADR-0012)。

    実 tick データ取得には kabu Station デスクトップアプリが localhost:18080 で起動している必要。
    起動忘れを起動時に明示検出する。

    :raises PreflightError: kabu authenticate 失敗
    """
    try:
        from api.kabucom import KabucomAPIClient  # type: ignore
    except Exception as e:
        raise PreflightError(f"kabu API client import failed: {e}")

    try:
        client = KabucomAPIClient()
        if not client.authenticate():
            raise PreflightError(
                "kabu Station authenticate failed. "
                "Is kabu Station desktop app running at localhost:18080? "
                "Is KABUCOM_API_PASSWORD registered in SSM?"
            )
    except PreflightError:
        raise
    except Exception as e:
        raise PreflightError(f"kabu authenticate check failed: {e}")


def run_preflight(allow_paper: bool = False) -> str:
    """全 preflight チェックを順次実行。

    モード別追加チェック:
    - PAPER_LIVE / REAL: kabu Station 認証可能性 smoke test

    :return: 検証済 trade_mode
    """
    mode = check_trade_mode(allow_paper=allow_paper)
    check_ssm_reachable()
    check_kill_switch_inactive()
    if mode in ("PAPER_LIVE", "REAL"):
        check_kabu_authenticate_for_live()
    logger.info(f"[Runner] Preflight passed (TRADE_MODE={mode})")
    return mode


# ===== シグナルハンドリング =====

_shutdown_requested = False


def _make_signal_handler(scheduler) -> "callable":
    """SIGINT/SIGTERM ハンドラ (scheduler.shutdown を呼ぶ)。"""
    def _handler(signum, frame):
        global _shutdown_requested
        logger.info(f"[Runner] Received signal {signum}, shutting down...")
        _shutdown_requested = True
        try:
            scheduler.shutdown()
        except Exception as e:
            logger.error(f"[Runner] Scheduler shutdown error: {e}")
    return _handler


# ===== メイン =====

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project Big Tester - REAL/PAPER 本番稼働 entry (M9)"
    )
    parser.add_argument("--allow-paper", action="store_true",
                        help="PAPER モードでも起動許可 (smoke test 用途)")
    parser.add_argument("--log-level", default="INFO",
                        help="ログレベル (DEBUG/INFO/WARNING/ERROR)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info(f"[Runner] Project Big Tester starting at {datetime.now()}")

    try:
        mode = run_preflight(allow_paper=args.allow_paper)
    except PreflightError as e:
        logger.error(f"[Runner] Preflight FAILED: {e}")
        return 1

    # 遅延 import (preflight 後でないと import 自体に失敗する依存があるため)
    from services.scheduler import system_scheduler  # type: ignore

    signal.signal(signal.SIGINT, _make_signal_handler(system_scheduler))
    signal.signal(signal.SIGTERM, _make_signal_handler(system_scheduler))

    system_scheduler.start()
    logger.info(f"[Runner] Scheduler started ({mode}). Press Ctrl+C to stop.")

    # 永続稼働ループ (scheduler は BackgroundScheduler で別スレッド動作)
    while not _shutdown_requested:
        time.sleep(60)

    logger.info("[Runner] Shutdown complete")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())