"""
バックテストエントリポイント (M9 / Phase 8a / ADR-0011)

要件 §C.1 (運用保守) / RB-008 復旧後の検証用途

責任:
- 過去の Market_Ticks / Daily_Price_History からヒストリカルデータ取得
- 戦略 (vwap_short / v6_long / passive_core) を再生
- 結果を JSON 出力 (CSV 化は将来オプション)

サポート対象:
- vwap_short: Market_Ticks の rolling window で VWAP/Z 評価 → BUY/SELL/HOLD カウント
- v6_long: 未実装 (Phase 8b 予定)
- passive_core: 未実装 (Phase 8b 予定)

使い方:
    python -m runner.backtest --strategy vwap_short \\
        --start 2026-01-01 --end 2026-04-30 \\
        --tickers 7203,9984
    # → stdout に JSON 出力

    python -m runner.backtest --strategy vwap_short \\
        --start 2026-01-01 --end 2026-04-30 \\
        --tickers 7203 --output backtest_result.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ===== 戦略バックテスト関数 =====

def backtest_vwap_short(
    start_date: datetime,
    end_date: datetime,
    tickers: Sequence[str],
    db: Session,
    rolling_window: int = 100,
    z_threshold: float = 2.0,
) -> Dict[str, Dict]:
    """vwap_short の rolling window バックテスト。

    各銘柄について Market_Ticks を時系列順に走査し、rolling_window 個の tick で
    calculate_vwap_and_zscore を計算。閾値超えのシグナルをカウント。

    :return: ticker → {tick_count, buy_signals, sell_signals, signal_count}
    """
    import pandas as pd

    from strategy.vwap_short import calculate_vwap_and_zscore  # type: ignore
    from models.schema import Market_Ticks  # type: ignore

    results: Dict[str, Dict] = {}
    for ticker in tickers:
        ticks = (
            db.query(Market_Ticks)
            .filter(Market_Ticks.ticker_symbol == ticker)
            .filter(Market_Ticks.timestamp >= start_date)
            .filter(Market_Ticks.timestamp <= end_date)
            .order_by(Market_Ticks.timestamp)
            .all()
        )

        if len(ticks) < rolling_window:
            results[ticker] = {
                "tick_count": len(ticks),
                "buy_signals": 0,
                "sell_signals": 0,
                "signal_count": 0,
                "note": f"insufficient ticks for window={rolling_window}",
            }
            continue

        buy_count = 0
        sell_count = 0
        for i in range(rolling_window, len(ticks)):
            window = ticks[i - rolling_window:i]
            df = pd.DataFrame([
                {"price": t.last_price, "volume": t.delta_volume} for t in window
            ])
            r = calculate_vwap_and_zscore(df)
            z = r["z_score"]
            if z > z_threshold:
                sell_count += 1
            elif z < -z_threshold:
                buy_count += 1

        results[ticker] = {
            "tick_count": len(ticks),
            "buy_signals": buy_count,
            "sell_signals": sell_count,
            "signal_count": buy_count + sell_count,
        }

    return results


# ===== CLI エントリ =====

_SUPPORTED_STRATEGIES = ("vwap_short",)  # v6_long / passive_core は Phase 8b 予定


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Project Big Tester backtest (M9)")
    parser.add_argument("--strategy", required=True, choices=_SUPPORTED_STRATEGIES,
                        help="バックテスト対象の戦略")
    parser.add_argument("--start", required=True, help="開始日 (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="終了日 (YYYY-MM-DD)")
    parser.add_argument("--tickers", required=True,
                        help="銘柄コード (カンマ区切り、例: 7203,9984)")
    parser.add_argument("--rolling-window", type=int, default=100,
                        help="vwap_short の rolling window サイズ (tick 数、デフォルト 100)")
    parser.add_argument("--z-threshold", type=float, default=2.0,
                        help="vwap_short の Z スコア閾値 (デフォルト 2.0)")
    parser.add_argument("--output", default=None,
                        help="結果 JSON の出力先 (省略時 stdout)")
    parser.add_argument("--log-level", default="INFO",
                        help="ログレベル")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        # tz-naive で parse 後、UTC aware に変換
        # (Market_Ticks.timestamp は DateTime(timezone=True) なので比較整合のため)
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as e:
        logger.error(f"[Backtest] Invalid date format: {e}")
        return 1

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        logger.error("[Backtest] No tickers specified")
        return 1

    from models.database import SessionLocal  # type: ignore

    db = SessionLocal()
    try:
        if args.strategy == "vwap_short":
            results = backtest_vwap_short(
                start_dt, end_dt, tickers, db,
                rolling_window=args.rolling_window,
                z_threshold=args.z_threshold,
            )
        else:
            logger.error(f"[Backtest] Strategy {args.strategy} not yet implemented")
            return 1
    finally:
        db.close()

    output = {
        "strategy": args.strategy,
        "period": {"start": args.start, "end": args.end},
        "tickers": tickers,
        "params": {
            "rolling_window": args.rolling_window,
            "z_threshold": args.z_threshold,
        },
        "results": results,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[Backtest] Saved to {out_path}")
        print(f"Backtest result saved to {out_path}")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())