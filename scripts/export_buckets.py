"""
export_buckets.py - bucket 別 JSON 論理 dump (週次)

要件: ADR-0007 OQ-6 / ADR-0010 §6-2
関連 Runbook: RB-008 (data.db の破損疑い時の bucket 単位復旧)

機能:
- Trade_Logs を category 別 (Passive / Long_Solid / Long_Growth / Short) に分割し JSON 出力
- 出力: backups/buckets/YYYYMMDD/{passive|long_solid|long_growth|short}.json
- 各 bucket 内には対応する Trade_Logs + 集計統計 (件数 / 期間 / pnl 合計) を含める

設計方針:
- 物理 DB コピー (backup_db.py) と論理 dump (本ファイル) の二段構え
- 論理 dump は bucket 単位の pinpoint restore に使う (将来 PG 移行時の bridge にも)
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_BASE = _PROJECT_ROOT / "backups" / "buckets"

# bucket → 銘柄カテゴリ列挙との対応 (Asset_Master.AssetCategory enum 値)
_BUCKET_TO_CATEGORY = {
    "passive": "Passive",
    "long_solid": "Long_Solid",
    "long_growth": "Long_Growth",
    "short": "Short",
}


def _serialize_trade(trade) -> Dict[str, Any]:
    """Trade_Logs ORM オブジェクトを JSON 化可能な dict に変換。"""
    return {
        "id": trade.id,
        "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
        "ticker_symbol": trade.ticker_symbol,
        "action": trade.action,
        "quantity": trade.quantity,
        "price": trade.price,
        "pnl": trade.pnl,
        "decision_reason": trade.decision_reason,
    }


def export_bucket(
    session: Session,
    bucket_name: str,
    output_dir: Path,
) -> Path:
    """指定 bucket の Trade_Logs を JSON ファイルへ dump。

    :param bucket_name: 'passive' / 'long_solid' / 'long_growth' / 'short'
    :return: 出力ファイルパス
    """
    if bucket_name not in _BUCKET_TO_CATEGORY:
        raise ValueError(f"[Export] unknown bucket '{bucket_name}'. Valid: {list(_BUCKET_TO_CATEGORY.keys())}")

    from models.schema import Trade_Logs, Asset_Master  # 遅延 import (DB 初期化待ち)

    category_value = _BUCKET_TO_CATEGORY[bucket_name]

    # bucket 銘柄一覧
    asset_symbols = [
        a.ticker_symbol for a in session.query(Asset_Master).filter(
            Asset_Master.category == category_value
        ).all()
    ]

    # bucket 内 Trade_Logs (ticker_symbol 経由で filter)
    if asset_symbols:
        trades = (
            session.query(Trade_Logs)
            .filter(Trade_Logs.ticker_symbol.in_(asset_symbols))
            .order_by(Trade_Logs.timestamp)
            .all()
        )
    else:
        trades = []

    # 集計統計
    total_pnl = sum((t.pnl or 0) for t in trades)
    timestamps = [t.timestamp for t in trades if t.timestamp]
    period_start = min(timestamps).isoformat() if timestamps else None
    period_end = max(timestamps).isoformat() if timestamps else None

    payload = {
        "bucket": bucket_name,
        "category": category_value,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "trade_count": len(trades),
            "total_pnl": total_pnl,
            "period_start": period_start,
            "period_end": period_end,
            "asset_count": len(asset_symbols),
        },
        "trades": [_serialize_trade(t) for t in trades],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{bucket_name}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Export] {bucket_name}: {len(trades)} trades -> {output_path}")
    return output_path


def export_all_buckets(
    session: Session,
    output_base: Path = DEFAULT_OUTPUT_BASE,
    target_date: Optional[date] = None,
) -> List[Path]:
    """全 4 bucket を JSON ファイルへ dump。

    :param target_date: 出力ディレクトリ命名用 (デフォルト = 今日)
    :return: 生成された各 bucket の JSON ファイルパスリスト
    """
    target = target_date or date.today()
    output_dir = output_base / target.strftime("%Y%m%d")

    paths: List[Path] = []
    for bucket_name in _BUCKET_TO_CATEGORY.keys():
        try:
            paths.append(export_bucket(session, bucket_name, output_dir))
        except Exception as e:
            logger.error(f"[Export] {bucket_name} failed: {e}")
    return paths


def main() -> int:
    """CLI: scripts/export_buckets.py を直接実行した場合のエントリ。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sys.path.insert(0, str(_PROJECT_ROOT / "backend" / "src"))

    try:
        from models.database import SessionLocal  # type: ignore[import-not-found]
    except ImportError as e:
        logger.error(f"[Export] Failed to import SessionLocal: {e}")
        return 1

    db = SessionLocal()
    try:
        paths = export_all_buckets(db)
        print(f"Exported {len(paths)} bucket files: {[str(p) for p in paths]}")
        return 0
    except Exception as e:
        logger.error(f"[Export] Failed: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())