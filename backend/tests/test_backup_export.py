"""Tests for scripts/backup_db.py + scripts/export_buckets.py (Phase 6 P6-2)

カバー範囲:
- backup_db: コピー / rotate / list_backups / 日付指定
- export_buckets: bucket 別 JSON 出力 / 全 bucket dump / 不正な bucket 名
"""
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

# scripts/ と backend/src/ を path に追加
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
sys.path.insert(0, str(_REPO_ROOT / "backend" / "src"))

import backup_db  # noqa: E402
import export_buckets  # noqa: E402

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base
from models.schema import Asset_Master, AssetCategory, Trade_Logs


# ===== backup_db =====

class TestBackupDb:
    def test_backup_database_creates_copy(self, tmp_path):
        # ダミー DB ファイル作成
        db_src = tmp_path / "data.db"
        db_src.write_bytes(b"SQLITE_DUMMY_CONTENTS")
        backup_dir = tmp_path / "backups"

        dest = backup_db.backup_database(db_path=db_src, backup_dir=backup_dir, target_date=date(2026, 5, 9))
        assert dest.exists()
        assert dest.name == "data-20260509.db"
        assert dest.read_bytes() == b"SQLITE_DUMMY_CONTENTS"

    def test_backup_database_missing_source_raises(self, tmp_path):
        db_src = tmp_path / "no_such.db"
        backup_dir = tmp_path / "backups"
        with pytest.raises(FileNotFoundError):
            backup_db.backup_database(db_path=db_src, backup_dir=backup_dir)

    def test_list_backups_empty_dir(self, tmp_path):
        assert backup_db.list_backups(backup_dir=tmp_path) == []

    def test_list_backups_filters_by_pattern(self, tmp_path):
        # 命名規約に合うファイルとそうでないファイル
        (tmp_path / "data-20260501.db").write_bytes(b"")
        (tmp_path / "data-20260502.db").write_bytes(b"")
        (tmp_path / "manual_backup.db").write_bytes(b"")  # 規約外 → 無視
        (tmp_path / "data-bad.db").write_bytes(b"")        # 規約外 → 無視

        files = backup_db.list_backups(backup_dir=tmp_path)
        names = [f.name for f in files]
        assert "data-20260501.db" in names
        assert "data-20260502.db" in names
        assert "manual_backup.db" not in names
        assert "data-bad.db" not in names

    def test_list_backups_sorted_desc(self, tmp_path):
        (tmp_path / "data-20260501.db").write_bytes(b"")
        (tmp_path / "data-20260510.db").write_bytes(b"")
        (tmp_path / "data-20260505.db").write_bytes(b"")

        files = backup_db.list_backups(backup_dir=tmp_path)
        assert [f.name for f in files] == [
            "data-20260510.db", "data-20260505.db", "data-20260501.db"
        ]

    def test_rotate_deletes_old_files(self, tmp_path):
        today = date.today()
        old = today - timedelta(days=40)
        recent = today - timedelta(days=5)

        old_file = tmp_path / f"data-{old.strftime('%Y%m%d')}.db"
        recent_file = tmp_path / f"data-{recent.strftime('%Y%m%d')}.db"
        old_file.write_bytes(b"")
        recent_file.write_bytes(b"")

        deleted = backup_db.rotate_backups(backup_dir=tmp_path, retention_days=30)
        assert old_file in deleted
        assert recent_file not in deleted
        assert not old_file.exists()
        assert recent_file.exists()

    def test_rotate_preserves_non_pattern_files(self, tmp_path):
        """規約外ファイル (manual_backup.db 等) は rotate 対象外。"""
        manual = tmp_path / "manual_backup.db"
        manual.write_bytes(b"")
        backup_db.rotate_backups(backup_dir=tmp_path, retention_days=0)  # 全部古い扱いだが
        assert manual.exists(), "rotate は命名規約外のファイルを削除しない"

    def test_run_daily_backup_combines_copy_and_rotate(self, tmp_path):
        db_src = tmp_path / "data.db"
        db_src.write_bytes(b"X")
        backup_dir = tmp_path / "backups"

        # 古いファイルを事前に置く
        backup_dir.mkdir()
        old_date = date.today() - timedelta(days=40)
        old_file = backup_dir / f"data-{old_date.strftime('%Y%m%d')}.db"
        old_file.write_bytes(b"")

        dest = backup_db.run_daily_backup(db_path=db_src, backup_dir=backup_dir, retention_days=30)
        assert dest.exists()
        assert not old_file.exists()  # rotate で削除


# ===== export_buckets =====

@pytest.fixture
def session_with_data():
    """Asset_Master + Trade_Logs を仕込んだセッション。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # 2 銘柄: Toyota=Long_Solid, SoftBank=Long_Growth
    db.add(Asset_Master(ticker_symbol="7203", asset_name="Toyota",
                        category=AssetCategory.long_solid, is_active=True))
    db.add(Asset_Master(ticker_symbol="9984", asset_name="SoftBank",
                        category=AssetCategory.long_growth, is_active=True))
    db.commit()

    # Trade_Logs を 3 件 (Toyota 2件 / SoftBank 1件)
    db.add(Trade_Logs(ticker_symbol="7203", action="BUY", quantity=100, price=3000, pnl=0))
    db.add(Trade_Logs(ticker_symbol="7203", action="SELL", quantity=100, price=3100, pnl=10000))
    db.add(Trade_Logs(ticker_symbol="9984", action="BUY", quantity=50, price=8000, pnl=0))
    db.commit()
    yield db
    db.close()


class TestExportBuckets:
    def test_export_bucket_long_solid(self, session_with_data, tmp_path):
        path = export_buckets.export_bucket(session_with_data, "long_solid", tmp_path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["bucket"] == "long_solid"
        assert data["category"] == "Long_Solid"
        assert data["summary"]["trade_count"] == 2  # Toyota の 2 件
        assert data["summary"]["total_pnl"] == 10000
        assert len(data["trades"]) == 2

    def test_export_bucket_long_growth(self, session_with_data, tmp_path):
        path = export_buckets.export_bucket(session_with_data, "long_growth", tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["summary"]["trade_count"] == 1  # SoftBank
        assert data["summary"]["total_pnl"] == 0

    def test_export_bucket_empty(self, session_with_data, tmp_path):
        """対象銘柄なしの bucket は trade_count=0 で空 JSON を出力。"""
        path = export_buckets.export_bucket(session_with_data, "passive", tmp_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["summary"]["trade_count"] == 0
        assert data["trades"] == []

    def test_export_bucket_unknown_raises(self, session_with_data, tmp_path):
        with pytest.raises(ValueError, match="unknown bucket"):
            export_buckets.export_bucket(session_with_data, "no_such_bucket", tmp_path)

    def test_export_all_buckets_creates_4_files(self, session_with_data, tmp_path):
        paths = export_buckets.export_all_buckets(
            session_with_data, output_base=tmp_path, target_date=date(2026, 5, 9)
        )
        assert len(paths) == 4
        # 4 つの bucket 全部のファイルが生成
        names = sorted([p.name for p in paths])
        assert names == ["long_growth.json", "long_solid.json", "passive.json", "short.json"]
        # 出力ディレクトリは backups/buckets/YYYYMMDD/ パターン
        for p in paths:
            assert p.parent.name == "20260509"