"""
Target_Portfolio 管理サービス (動的トップレベル比率)

要件 §2.2 ポートフォリオ見直しタイミング:
- (a) 四半期定期: 1月 / 4月 / 7月 / 10月
- (b) VIXギア発火時: VIX ≤ 20 (DEFEND) / VIX ≥ 35 (ATTACK) の閾値超過を即時トリガー
- (c) 手動指示: Discord WebHook / Next.js UI から発動 (本実装は別途エンドポイント経由)

トップレベル比率 = cash / trust / stocks の3バケット。
内訳 (株式内 60% Long / 40% Short, 長期内 70% コア / 30% サテライト) は固定 (§2.2)。

注: VIX/四半期トリガー時のデフォルト比率はプレースホルダ。
将来は AI (Gemma 2 + ローカル LLM) によるマクロ評価で動的化する (Phase 6+ 予定)。
"""
from typing import Optional
from datetime import date as _date

from sqlalchemy import desc
from sqlalchemy.orm import Session

from models.schema import Target_Portfolio


# VIX モード別のデフォルト比率 (要件 §2.2 を満たすプレースホルダ実装)
VIX_GEAR_RATIOS = {
    "DEFEND":  {"cash": 0.30, "trust": 0.40, "stocks": 0.30},   # 防御: 現金温存
    "ATTACK":  {"cash": 0.05, "trust": 0.40, "stocks": 0.55},   # 攻撃: リバウンド狙い
}

# 四半期定期見直しのベースライン比率 (AI 動的化までのプレースホルダ)
QUARTERLY_BASELINE_RATIOS = {"cash": 0.10, "trust": 0.50, "stocks": 0.40}


def get_latest(session: Session) -> Optional[Target_Portfolio]:
    """最新の effective_date を持つ Target_Portfolio レコードを返す (なければ None)。"""
    return session.query(Target_Portfolio).order_by(desc(Target_Portfolio.effective_date)).first()


def get_active_ratios(session: Session) -> dict:
    """
    現在有効な動的比率を取得。

    Target_Portfolio が空の場合は QUARTERLY_BASELINE_RATIOS を返却。
    呼び出し側 (rebalance ロジック等) は本関数の戻り値だけを参照すれば良い。
    """
    latest = get_latest(session)
    if latest is None:
        return dict(QUARTERLY_BASELINE_RATIOS)
    return {
        "cash": latest.cash_target_pct,
        "trust": latest.trust_target_pct,
        "stocks": latest.stocks_target_pct,
    }


def write_target(
    session: Session,
    cash_pct: float,
    trust_pct: float,
    stocks_pct: float,
    trigger: str,
    notes: Optional[str] = None,
    effective_date_override: Optional[_date] = None,
) -> Target_Portfolio:
    """
    Target_Portfolio レコードを書込 (effective_date = 当日 / UPSERT)。

    :param trigger: 'Quarterly' / 'VIX_DEFEND' / 'VIX_ATTACK' / 'Manual' のいずれか
    :raises ValueError: 比率の合計が 1.0 (±0.01) から外れた場合
    """
    total = cash_pct + trust_pct + stocks_pct
    if abs(total - 1.0) > 0.01:
        raise ValueError(
            f"[TargetPortfolio] target percentages must sum to 1.0, got {total:.4f}"
        )

    eff_date = effective_date_override or _date.today()

    record = session.query(Target_Portfolio).filter(
        Target_Portfolio.effective_date == eff_date
    ).first()
    if record is None:
        record = Target_Portfolio(effective_date=eff_date)
        session.add(record)

    record.cash_target_pct = cash_pct
    record.trust_target_pct = trust_pct
    record.stocks_target_pct = stocks_pct
    record.trigger = trigger
    record.notes = notes
    session.commit()
    session.refresh(record)
    return record


def write_for_vix_gear(
    session: Session, vix_mode: str, vix_value: float
) -> Optional[Target_Portfolio]:
    """
    VIXギア発火時の Target_Portfolio 書込。

    NEUTRAL モードは書込せず None を返す (NEUTRAL は四半期見直しでベースラインへ復帰させる設計)。

    :param vix_mode: 'DEFEND' / 'ATTACK' / 'NEUTRAL'
    :param vix_value: VIX 値 (notes に記録)
    :return: 書き込まれたレコード、または None (NEUTRAL 時)
    """
    if vix_mode not in VIX_GEAR_RATIOS:
        return None

    ratios = VIX_GEAR_RATIOS[vix_mode]
    trigger_label = f"VIX_{vix_mode}"  # 'VIX_DEFEND' / 'VIX_ATTACK'
    notes = f"Auto-fired by VIX gear: VIX={vix_value:.2f}, mode={vix_mode}"
    return write_target(
        session,
        ratios["cash"], ratios["trust"], ratios["stocks"],
        trigger_label, notes,
    )


def write_for_quarterly_review(session: Session) -> Target_Portfolio:
    """
    四半期定期見直しの Target_Portfolio 書込 (現状は QUARTERLY_BASELINE_RATIOS)。

    将来は AI (Gemma 2) によるマクロ評価を経た動的比率に置換予定 (Phase 6+)。
    """
    notes = f"Quarterly review baseline ({_date.today().isoformat()})"
    return write_target(
        session,
        QUARTERLY_BASELINE_RATIOS["cash"],
        QUARTERLY_BASELINE_RATIOS["trust"],
        QUARTERLY_BASELINE_RATIOS["stocks"],
        "Quarterly",
        notes,
    )


def split_stocks_to_long_short(stocks_pct: float) -> dict:
    """
    トップレベル stocks 比率を「株式内 60% Long / 40% Short」に固定分解 (要件 §2.2)。

    :return: {"trust": ..., "long": stocks * 0.60, "short": stocks * 0.40}
             ※ trust は呼出側で active_ratios["trust"] を別途付与する想定
    """
    return {
        "long": stocks_pct * 0.60,
        "short": stocks_pct * 0.40,
    }
