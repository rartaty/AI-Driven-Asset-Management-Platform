"""
backup_db.py - SQLite データファイルの日次バックアップ

要件: §A.2.6 / §C.1 / ADR-0007 OQ-6 / ADR-0010 §6-2
関連 Runbook: RB-008 (data.db の破損疑い)

機能:
- backend/src/data.db を backups/data-YYYYMMDD.db へコピー
- 30 日分世代管理 (古いファイルを自動 rotate)
- ライブラリ関数 + CLI 両対応 (scheduler.job_backup_daily から呼ばれる)

設計方針:
- SQLite ファイルベース → 停止不要・無停止スナップショット可
- shutil.copy2 で metadata 含めてコピー (mtime 保持)
- rotate 対象: data-YYYYMMDD.db 形式のみ (任意のバックアップを誤削除しない)
"""
from __future__ import annotations

import logging
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


# プロジェクトルート (本ファイルは scripts/ 配下)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _PROJECT_ROOT / "backend" / "src" / "data.db"
DEFAULT_BACKUP_DIR = _PROJECT_ROOT / "backups"
DEFAULT_RETENTION_DAYS = 30

# data-YYYYMMDD.db 形式のみ rotate 対象
_BACKUP_FILENAME_PATTERN = re.compile(r"^data-(\d{8})\.db$")


def backup_database(
    db_path: Path = DEFAULT_DB_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    target_date: Optional[date] = None,
) -> Path:
    """data.db を backups/data-YYYYMMDD.db へコピー。

    :param db_path: コピー元 SQLite ファイル
    :param backup_dir: バックアップディレクトリ (なければ作成)
    :param target_date: コピー先ファイル名の日付 (テスト用、デフォルトは今日)
    :return: 生成された backup ファイルのパス
    :raises FileNotFoundError: db_path が存在しない場合
    """
    if not db_path.exists():
        raise FileNotFoundError(f"[Backup] DB file not found: {db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    target = target_date or date.today()
    dest = backup_dir / f"data-{target.strftime('%Y%m%d')}.db"

    shutil.copy2(db_path, dest)
    logger.info(f"[Backup] Copied {db_path} -> {dest}")
    return dest


def list_backups(backup_dir: Path = DEFAULT_BACKUP_DIR) -> List[Path]:
    """バックアップディレクトリ内の data-YYYYMMDD.db を新しい順で返す。"""
    if not backup_dir.exists():
        return []
    files = []
    for f in backup_dir.iterdir():
        if f.is_file() and _BACKUP_FILENAME_PATTERN.match(f.name):
            files.append(f)
    # 名前 (data-YYYYMMDD.db) は辞書順でソートすれば日付順
    return sorted(files, reverse=True)


def rotate_backups(
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> List[Path]:
    """retention_days より古い data-YYYYMMDD.db を削除。

    :return: 削除されたファイルのリスト
    """
    if not backup_dir.exists():
        return []

    cutoff = date.today() - timedelta(days=retention_days)
    deleted: List[Path] = []

    for f in backup_dir.iterdir():
        if not f.is_file():
            continue
        m = _BACKUP_FILENAME_PATTERN.match(f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            logger.warning(f"[Backup] Skipping unparseable filename: {f.name}")
            continue
        if file_date < cutoff:
            f.unlink()
            deleted.append(f)
            logger.info(f"[Backup] Rotated (deleted) {f}")

    return deleted


def run_daily_backup(
    db_path: Path = DEFAULT_DB_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> Path:
    """日次バックアップ (バックアップ + rotate を一括実行)。

    scheduler.job_backup_daily から呼ばれるエントリポイント。
    :return: 生成された backup ファイルのパス
    """
    dest = backup_database(db_path=db_path, backup_dir=backup_dir)
    rotate_backups(backup_dir=backup_dir, retention_days=retention_days)
    return dest


def main() -> int:
    """CLI: scripts/backup_db.py を直接実行した場合のエントリ。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        dest = run_daily_backup()
        print(f"Backup completed: {dest}")
        return 0
    except Exception as e:
        logger.error(f"[Backup] Failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())