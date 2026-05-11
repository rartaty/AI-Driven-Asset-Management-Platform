"""
Kill Switch — 致命的損失からの最終防衛機構

参照仕様:
- docs/operations/KILL_SWITCH_V2_SPEC.md (V2 仕様書 — 本実装は §3.1 / §4 / §5.2 / §6 を範囲)
- docs/REQUIREMENTS.md §5 (リスク管理)
- docs/adr/0005-phase4-scope-standard.md (Phase 4 スコープ)
- docs/adr/0010-phase6-scope-sre-operations.md (Phase 6 / bucket 別キルスイッチ追加)
- CLAUDE.md 絶対禁止 #3 (キルスイッチ無断解除禁止 — 全フラグに適用)

Phase 4 実装範囲:
- Daily_Asset_Snapshot 履歴からのドローダウン計算
- User_Settings.is_kill_switch_active 書込/参照
- 全買付パス用 assert_kill_switch_inactive_for_entry()
- 決済パス用 (常に許可) は **チェック関数を呼ばない** 設計で表現
- 手動解除 (explicit confirmation 文字列必須・絶対禁止 #3 準拠)
- Discord 致命通知 (notify_critical) 連動

Phase 6 拡張 (本ファイル):
- bucket 別キルスイッチ (passive / long_solid / long_growth / short)
- is_active(session, bucket=None) — 全体 OR bucket 別フラグ OR
- assert_inactive_for_entry(session, bucket=None) — bucket 引数で個別チェック
- activate_bucket / deactivate_bucket — bucket 別の発動/解除
- 解除確認文字列は全フラグ共通で必須

Phase 7+ に持ち越し (V2 spec の残り):
- 物理 `.kill.lock` ファイル二重防御 (§3.2)
- HTTP 5xx 連続エラー自動発動 (§2.1)
- Discord 経由 `!killswitch on/off` コマンド (§2.2 / §5.2)
- Next.js UI 緊急停止ボタン (§2.2)
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from core.discord import notify_critical, notify_system, NotifyLevel
from models.schema import Daily_Asset_Snapshot, User_Settings


logger = logging.getLogger(__name__)


# ===== Exception =====
class KillSwitchError(Exception):
    """Kill switch is active — new entry blocked.

    決済 (exit) パスではこの例外を発生させない設計のため、本例外が伝播するのは
    新規エントリー試行時のみ。catch 側はキルスイッチ起因とみなしてよい。
    """


# ===== Drawdown 計算 =====
@dataclass(frozen=True)
class DrawdownReport:
    """ドローダウン計算結果。"""
    current_value: int
    peak_value: int
    drawdown_pct: float  # 負値 (例: -0.0312 = -3.12%)
    snapshot_count: int


def _investment_portfolio_value(snapshot: Daily_Asset_Snapshot) -> int:
    """投資ポートフォリオ評価額 (生活防衛費=bank_balance を除く)。

    bank_balance は要件 §2.2 で「読取専用・生活防衛費含む」とされており、
    投資判断のドローダウン計算からは除外する。
    """
    return (
        (snapshot.buying_power or 0)
        + (snapshot.trust_value or 0)
        + (snapshot.long_solid_value or 0)
        + (snapshot.long_growth_value or 0)
        + (snapshot.short_term_market_value or 0)
    )


def compute_drawdown(session: Session, lookback_days: int = 30) -> Optional[DrawdownReport]:
    """直近 N 日間の Daily_Asset_Snapshot からドローダウンを計算。

    :param session: SQLAlchemy セッション
    :param lookback_days: 遡及日数 (デフォルト 30)
    :return: DrawdownReport (snapshot 不足時は None)
    """
    snapshots = (
        session.query(Daily_Asset_Snapshot)
        .order_by(Daily_Asset_Snapshot.date.desc())
        .limit(lookback_days)
        .all()
    )
    if not snapshots:
        return None

    values = [_investment_portfolio_value(s) for s in snapshots]
    current = values[0]  # 最新 (desc order)
    peak = max(values)

    if peak <= 0:
        # 評価額ゼロ以下では DD 計算不能 (除算ガード)
        return DrawdownReport(current_value=current, peak_value=peak, drawdown_pct=0.0, snapshot_count=len(values))

    drawdown_pct = (current - peak) / peak
    return DrawdownReport(
        current_value=current,
        peak_value=peak,
        drawdown_pct=drawdown_pct,
        snapshot_count=len(values),
    )


# ===== State 読書 =====
def _get_user_settings(session: Session) -> User_Settings:
    """シングルトン User_Settings (id=1) を取得。存在しなければデフォルト値で作成。"""
    settings = session.query(User_Settings).filter(User_Settings.id == 1).first()
    if settings is None:
        settings = User_Settings(id=1)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


# bucket 名 → User_Settings 列名のマップ (Phase 6 / ADR-0010)
_BUCKET_FLAG_COLUMN = {
    "Passive": "is_kill_switch_active_passive",
    "Long_Solid": "is_kill_switch_active_long_solid",
    "Long_Growth": "is_kill_switch_active_long_growth",
    "Short": "is_kill_switch_active_short",
}


def _validate_bucket(bucket: Optional[str]) -> None:
    """bucket 引数のバリデーション (None は OK = 全体扱い)。"""
    if bucket is not None and bucket not in _BUCKET_FLAG_COLUMN:
        raise ValueError(
            f"[KillSwitch] unknown bucket '{bucket}'. "
            f"Valid: {list(_BUCKET_FLAG_COLUMN.keys())} or None (whole)"
        )


def is_active(session: Session, bucket: Optional[str] = None) -> bool:
    """現在のキルスイッチ状態を返す。

    :param bucket: None なら全体フラグのみ参照、bucket 名指定なら全体 OR bucket 別の OR で判定
    :return: 該当 bucket への新規エントリーをブロックすべきなら True
    """
    _validate_bucket(bucket)
    settings = _get_user_settings(session)
    if settings.is_kill_switch_active:
        return True  # 全体停止は全 bucket をブロック
    if bucket is None:
        return False
    return bool(getattr(settings, _BUCKET_FLAG_COLUMN[bucket]))


# ===== 自動発動判定 =====
def check_drawdown_and_trigger(session: Session, lookback_days: int = 30) -> bool:
    """ドローダウンを計算し、閾値超過なら自動発動。

    :param session: SQLAlchemy セッション
    :param lookback_days: 遡及日数
    :return: 本呼び出しで新規発動した場合 True (既発動・未到達は False)
    """
    settings = _get_user_settings(session)
    if settings.is_kill_switch_active:
        return False  # 既発動なら何もしない

    # 1. 生活防衛費の保護チェック (M2)
    latest_snapshot = session.query(Daily_Asset_Snapshot).order_by(Daily_Asset_Snapshot.date.desc()).first()
    if latest_snapshot and latest_snapshot.bank_balance is not None:
        try:
            living_expenses_threshold = int(os.getenv("LIVING_EXPENSES_THRESHOLD", "1000000"))
        except ValueError:
            living_expenses_threshold = 1000000
            
        if latest_snapshot.bank_balance < living_expenses_threshold:
            reason = f"bank_balance ({latest_snapshot.bank_balance:,}) < living expenses threshold ({living_expenses_threshold:,})"
            activate(session, reason=reason, manual=False)
            return True

    # 2. ドローダウン計算
    report = compute_drawdown(session, lookback_days=lookback_days)
    if report is None:
        return False

    threshold = float(settings.max_drawdown_limit or -0.03)
    if report.drawdown_pct <= threshold:
        reason = (
            f"drawdown {report.drawdown_pct:.4f} <= threshold {threshold:.4f} "
            f"(peak={report.peak_value:,} / current={report.current_value:,} / lookback={report.snapshot_count}d)"
        )
        activate(session, reason=reason, manual=False)
        return True
    return False


# ===== 発動 / 解除 =====
def activate(session: Session, reason: str, manual: bool = False) -> None:
    """キルスイッチを発動 (DB 書込 + Discord 致命通知)。

    :param session: SQLAlchemy セッション
    :param reason: 発動理由 (ログ・通知に含める)
    :param manual: 手動発動なら True (ログ区別用)
    """
    settings = _get_user_settings(session)
    if settings.is_kill_switch_active:
        logger.info(f"[KillSwitch] activate() called but already active. reason={reason}")
        return

    settings.is_kill_switch_active = True
    session.commit()

    trigger_label = "MANUAL" if manual else "AUTO"
    msg = f"🚨 [{trigger_label}] kill switch activated. reason: {reason}"
    logger.critical(f"[KillSwitch] {msg}")
    # Discord 通知 (失敗は非ブロッキング — notify_critical 自体が握る)
    notify_critical(message=msg, component="KillSwitch")


# 解除確認用の固定文字列 (CLAUDE.md 絶対禁止 #3 準拠 — 誤操作防止)
DEACTIVATE_CONFIRMATION_PHRASE = "I_UNDERSTAND_KILL_SWITCH_DEACTIVATION_RISK"


def deactivate(session: Session, reason: str, confirmation: str) -> None:
    """キルスイッチ全体を手動解除する。

    解除には固定確認文字列を要求 (絶対禁止 #3 準拠 — コードからの不用意解除を物理的に阻止)。
    Phase 7+ で Discord/UI コマンド経由解除に拡張予定だが、現状は code 経由のみ。

    :param session: SQLAlchemy セッション
    :param reason: 解除理由 (ログ・通知に含める)
    :param confirmation: DEACTIVATE_CONFIRMATION_PHRASE と一致必須
    :raises ValueError: 確認文字列不一致時
    """
    if confirmation != DEACTIVATE_CONFIRMATION_PHRASE:
        raise ValueError(
            f"[KillSwitch] deactivate requires explicit confirmation string. "
            f"Pass confirmation='{DEACTIVATE_CONFIRMATION_PHRASE}'."
        )

    settings = _get_user_settings(session)
    if not settings.is_kill_switch_active:
        logger.info(f"[KillSwitch] deactivate() called but already inactive. reason={reason}")
        return

    settings.is_kill_switch_active = False
    session.commit()

    msg = f"🟢 kill switch DEACTIVATED. reason: {reason}"
    logger.warning(f"[KillSwitch] {msg}")
    notify_system(message=msg, component="KillSwitch", level=NotifyLevel.WARN)


# ===== bucket 別 発動 / 解除 (Phase 6 / ADR-0010) =====
def activate_bucket(session: Session, bucket: str, reason: str, manual: bool = False) -> None:
    """bucket 別キルスイッチを発動 (該当 bucket のみブロック)。

    :param bucket: 'Passive' / 'Long_Solid' / 'Long_Growth' / 'Short' のいずれか
    :raises ValueError: 不正な bucket 名
    """
    _validate_bucket(bucket)
    if bucket is None:
        raise ValueError("[KillSwitch] activate_bucket requires non-None bucket. Use activate() for whole.")

    settings = _get_user_settings(session)
    column = _BUCKET_FLAG_COLUMN[bucket]
    if getattr(settings, column):
        logger.info(f"[KillSwitch] activate_bucket({bucket}) called but already active. reason={reason}")
        return

    setattr(settings, column, True)
    session.commit()

    trigger_label = "MANUAL" if manual else "AUTO"
    msg = f"🚨 [{trigger_label}] kill switch activated for bucket={bucket}. reason: {reason}"
    logger.critical(f"[KillSwitch] {msg}")
    notify_critical(message=msg, component="KillSwitch")


def deactivate_bucket(session: Session, bucket: str, reason: str, confirmation: str) -> None:
    """bucket 別キルスイッチを手動解除する (確認文字列必須・絶対禁止 #3 準拠)。"""
    if confirmation != DEACTIVATE_CONFIRMATION_PHRASE:
        raise ValueError(
            f"[KillSwitch] deactivate_bucket requires explicit confirmation string. "
            f"Pass confirmation='{DEACTIVATE_CONFIRMATION_PHRASE}'."
        )
    _validate_bucket(bucket)
    if bucket is None:
        raise ValueError("[KillSwitch] deactivate_bucket requires non-None bucket. Use deactivate() for whole.")

    settings = _get_user_settings(session)
    column = _BUCKET_FLAG_COLUMN[bucket]
    if not getattr(settings, column):
        logger.info(f"[KillSwitch] deactivate_bucket({bucket}) called but already inactive. reason={reason}")
        return

    setattr(settings, column, False)
    session.commit()

    msg = f"🟢 kill switch DEACTIVATED for bucket={bucket}. reason: {reason}"
    logger.warning(f"[KillSwitch] {msg}")
    notify_system(message=msg, component="KillSwitch", level=NotifyLevel.WARN)


# ===== Pre-order チェック (V2 spec §4: 非対称オーバーライド) =====
def assert_inactive_for_entry(session: Session, bucket: Optional[str] = None) -> None:
    """新規エントリー (買付・空売り建て) パスで呼び出す。

    bucket 引数:
    - None: 全体フラグのみチェック (Phase 4 互換)
    - 'Passive' / 'Long_Solid' / 'Long_Growth' / 'Short': 全体 OR bucket 別の OR でチェック

    発動中なら KillSwitchError 送出 + Discord 致命通知。
    決済 (exit) パスではこの関数を呼ばない (V2 spec §4.2)。

    :raises KillSwitchError: キルスイッチ発動中
    :raises ValueError: 不正な bucket 名
    """
    _validate_bucket(bucket)
    if is_active(session, bucket=bucket):
        bucket_label = f" for bucket={bucket}" if bucket else ""
        msg = f"new entry blocked{bucket_label}"
        logger.warning(f"[KillSwitch] {msg}")
        notify_critical(message=msg, component="KillSwitch")
        raise KillSwitchError(f"Kill switch active — new entry blocked{bucket_label}")
