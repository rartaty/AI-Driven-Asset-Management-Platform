"""
Position Sizing Service (Phase 8c / ADR-0012)

PAPER_LIVE / REAL mode 共通の qty 計算ロジック。

要件:
- buying_power × POSITION_SIZE_PCT で 1 取引あたりの基本サイズ算出
- KABU_DAILY_ORDER_LIMIT_YEN を超えないよう日次累計を遵守
- 100 株単位切り捨て (日本株標準ロット)
- 不足時 0 を返す (発注スキップ)

関連:
- ADR-0012: Phase 8c PAPER_LIVE モード設計
- ADR-0013: Phase 8d REAL モード本実装 (REAL_MAX_YEN_PER_TRADE 追加チェック)
"""
from __future__ import annotations

import logging
import os
from datetime import date as _date
from typing import Optional

from sqlalchemy import desc as _desc, func as _func
from sqlalchemy.orm import Session

from models.schema import Daily_Asset_Snapshot, Trade_Logs

logger = logging.getLogger(__name__)


# 日本株標準ロット (100 株単位)
LOT_SIZE = 100


def calculate_position_qty(
    db: Session,
    price: int,
    bucket: str = "Short",
) -> int:
    """
    Position sizing + kabu 日次手数料無料枠遵守の qty 計算。

    Step 1: buying_power × POSITION_SIZE_PCT で基本 target_yen 算出
    Step 2: KABU_DAILY_ORDER_LIMIT_YEN の残量を Trade_Logs 当日累計から計算
    Step 3: target_yen を min(基本, 残量) に切り詰め
    Step 4: 100 株単位切り捨て

    :param db: SQLAlchemy session
    :param price: 銘柄の現在価格 (円単位整数)
    :param bucket: bucket 名 (現状未使用、Phase 9 で bucket 別キャッシュ管理時に活用予定)
    :return: 発注株数 (100 株単位)、不足時 0
    """
    if price <= 0:
        return 0

    # Step 1: buying_power × POSITION_SIZE_PCT
    latest = db.query(Daily_Asset_Snapshot).order_by(_desc(Daily_Asset_Snapshot.date)).first()
    buying_power = latest.buying_power if latest else 1_000_000  # default ¥1M
    try:
        pct = float(os.getenv("POSITION_SIZE_PCT", "0.20"))  # default 20%
    except ValueError:
        pct = 0.20
    target_yen = buying_power * pct

    # Step 2: kabu 日次手数料無料枠の残量チェック
    try:
        daily_limit = int(os.getenv("KABU_DAILY_ORDER_LIMIT_YEN", "1000000"))  # default ¥100万
    except ValueError:
        daily_limit = 1_000_000

    if daily_limit > 0:
        today = _date.today()
        used_yen = db.query(
            _func.coalesce(_func.sum(Trade_Logs.quantity * Trade_Logs.price), 0)
        ).filter(
            _func.date(Trade_Logs.timestamp) == today
        ).scalar() or 0
        remaining_yen = max(0, daily_limit - int(used_yen))
        if remaining_yen <= 0:
            logger.warning(
                f"[PositionSize] Daily limit ¥{daily_limit:,} reached "
                f"(used=¥{int(used_yen):,}), skip new orders"
            )
            return 0
        # 基本 target と残量の小さい方を採用 (無料枠超過しない)
        target_yen = min(target_yen, remaining_yen)

    # Step 3: REAL モード追加上限 (Phase 8d 用、デフォルト 0 = 制限なし)
    try:
        real_max = int(os.getenv("REAL_MAX_YEN_PER_TRADE", "0"))
    except ValueError:
        real_max = 0
    if real_max > 0:
        target_yen = min(target_yen, real_max)

    # Step 4: 100 株単位切り捨て
    qty = int(target_yen // (price * LOT_SIZE)) * LOT_SIZE
    return max(qty, 0)