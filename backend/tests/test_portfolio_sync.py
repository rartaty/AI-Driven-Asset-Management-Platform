"""Tests for backend/src/services/portfolio_sync.py

カバー範囲 (Phase 5 P11):
- PAPER モード時の get_consolidated_portfolio が新 schema フィールドを返却
- bucket 別集計が Asset_Master.category に従う (REAL モード)
- write_daily_snapshot の UPSERT 挙動
- 旧 schema バグ (trust_value: bank_balance) のリグレッション検証
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base
from models.schema import Asset_Master, AssetCategory, Daily_Asset_Snapshot
from services.paper_trader import PaperTrader
from services.portfolio_sync import PortfolioSyncService


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture
def paper_engine(monkeypatch):
    """PaperTrader はシングルトンなので、テスト毎に状態をリセット。

    update_positions_market_value() は yfinance への実通信を行うため no-op に差し替え
    (要件: テストから本番 API への通信禁止 / @../.claude/testing.md)。
    テストは current_price を直接 fixture で指定して制御する。
    """
    engine = PaperTrader()
    engine._initialize()
    monkeypatch.setattr(engine, "update_positions_market_value", lambda: None)
    return engine


# ===== PAPER モード: 新 schema 出力検証 =====

def test_paper_mode_returns_new_schema_fields(monkeypatch, paper_engine):
    """PAPER モードで Daily_Asset_Snapshot 整合の全 8 フィールドが含まれること。"""
    monkeypatch.setenv("TRADE_MODE", "PAPER")
    service = PortfolioSyncService()

    result = service.get_consolidated_portfolio()

    required_fields = {
        "bank_balance", "buying_power",
        "trust_value", "long_solid_value", "long_growth_value",
        "short_term_capital", "short_term_market_value",
        "cumulative_sweep_to_long_solid",
    }
    missing = required_fields - set(result.keys())
    assert not missing, f"Missing new schema fields: {missing}"


def test_paper_mode_keeps_legacy_fields_for_frontend_compat(monkeypatch, paper_engine):
    """フロントエンド既存 PortfolioSummary 型との互換性のため旧フィールドが残存していること。"""
    monkeypatch.setenv("TRADE_MODE", "PAPER")
    service = PortfolioSyncService()

    result = service.get_consolidated_portfolio()

    legacy_fields = {"long_value", "short_value", "cash_balance", "total_value", "accumulated_sweep"}
    missing = legacy_fields - set(result.keys())
    assert not missing, f"Missing legacy compat fields: {missing}"


def test_paper_mode_buckets_aggregate_by_position_category(monkeypatch, paper_engine):
    """同じ category のポジションが正しい bucket に集計されること。"""
    monkeypatch.setenv("TRADE_MODE", "PAPER")
    paper_engine.cash_balance = 500000.0
    paper_engine.positions = [
        {"symbol": "7203", "name": "Toyota", "category": "Long_Solid",
         "shares": 100, "avg_price": 3000, "current_price": 3100, "unrealized_pnl": 10000},
        {"symbol": "9984", "name": "SoftBank", "category": "Long_Growth",
         "shares": 50, "avg_price": 8000, "current_price": 8500, "unrealized_pnl": 25000},
        {"symbol": "8035", "name": "TEL", "category": "Short",
         "shares": 10, "avg_price": 30000, "current_price": 31000, "unrealized_pnl": 10000},
    ]

    service = PortfolioSyncService()
    result = service.get_consolidated_portfolio()

    assert result["long_solid_value"] == 100 * 3100        # 310,000
    assert result["long_growth_value"] == 50 * 8500        # 425,000
    assert result["short_term_market_value"] == 10 * 31000  # 310,000
    assert result["short_term_capital"] == 10 * 30000       # 300,000 (= 元本)
    assert result["trust_value"] == 0                       # Passive ポジションなし


# ===== リグレッション: 旧 schema バグ =====

def test_no_regression_trust_value_is_not_bank_balance(monkeypatch, paper_engine):
    """旧バグ (trust_value: bank_balance) が再発していないこと。

    要件 §2.2: 銀行と証券は独立プール。bank_balance を trust_value に流用してはならない。
    """
    monkeypatch.setenv("TRADE_MODE", "PAPER")
    paper_engine.cash_balance = 1000000.0  # 証券口座キャッシュ
    paper_engine.positions = []            # ポジションなし → trust_value は 0 が正解

    service = PortfolioSyncService()
    result = service.get_consolidated_portfolio()

    assert result["trust_value"] == 0, (
        "trust_value は Passive ポジション評価額のみを反映すべきで、"
        "bank_balance や cash_balance を流用してはならない (要件 §2.2 旧 schema バグ)"
    )


# ===== write_daily_snapshot の UPSERT 検証 =====

def test_write_daily_snapshot_creates_new_record(session, monkeypatch, paper_engine):
    """当日レコードが未存在 → 新規作成されること。"""
    monkeypatch.setenv("TRADE_MODE", "PAPER")
    paper_engine.cash_balance = 200000.0
    paper_engine.positions = []

    service = PortfolioSyncService()
    snapshot = service.write_daily_snapshot(session)

    assert snapshot.date == date.today()
    assert snapshot.buying_power == 200000
    assert snapshot.trust_value == 0
    assert snapshot.long_solid_value == 0

    # DB に1件だけ存在すること
    all_snapshots = session.query(Daily_Asset_Snapshot).all()
    assert len(all_snapshots) == 1


def test_write_daily_snapshot_upserts_existing_record(session, monkeypatch, paper_engine):
    """同日2回呼ばれても1レコードのまま、評価額が最新値で上書きされること。"""
    monkeypatch.setenv("TRADE_MODE", "PAPER")
    paper_engine.cash_balance = 100000.0
    paper_engine.positions = []

    service = PortfolioSyncService()
    service.write_daily_snapshot(session)

    # キャッシュ変動を反映して再書込
    paper_engine.cash_balance = 250000.0
    snapshot2 = service.write_daily_snapshot(session)

    assert snapshot2.buying_power == 250000

    all_snapshots = session.query(Daily_Asset_Snapshot).all()
    assert len(all_snapshots) == 1, "UPSERT 設計違反: 同日2レコード作成は不可"


def test_write_daily_snapshot_persists_all_buckets(session, monkeypatch, paper_engine):
    """全 bucket フィールドが Daily_Asset_Snapshot へ正しく persist されること。"""
    monkeypatch.setenv("TRADE_MODE", "PAPER")
    paper_engine.cash_balance = 500000.0
    paper_engine.positions = [
        {"symbol": "EMAXIS", "name": "eMAXIS Slim", "category": "Passive",
         "shares": 100, "avg_price": 20000, "current_price": 21000, "unrealized_pnl": 100000},
    ]

    service = PortfolioSyncService()
    snapshot = service.write_daily_snapshot(session)

    assert snapshot.trust_value == 100 * 21000  # 2,100,000
    assert snapshot.buying_power == 500000
    assert snapshot.bank_balance == 0
    assert snapshot.long_solid_value == 0
    assert snapshot.long_growth_value == 0
    assert snapshot.short_term_market_value == 0


# ===== Asset_Master からのカテゴリ参照 (REAL モード) =====

def test_real_mode_uses_asset_master_for_category_lookup(session, monkeypatch, mocker):
    """REAL モード時、Asset_Master.category に従って bucket 集計されること。"""
    monkeypatch.setenv("TRADE_MODE", "REAL")

    # Asset_Master に銘柄マスタを2件登録
    session.add(Asset_Master(
        ticker_symbol="7203", asset_name="Toyota", category=AssetCategory.long_solid, is_active=True
    ))
    session.add(Asset_Master(
        ticker_symbol="9984", asset_name="SoftBank", category=AssetCategory.long_growth, is_active=True
    ))
    session.commit()

    service = PortfolioSyncService()
    # 証券・銀行 API をモック
    mocker.patch.object(service.broker_api, "get_cash_balance", return_value=500000)
    mocker.patch.object(service.bank_api, "get_account_balance", return_value=2000000)
    mocker.patch.object(service.broker_api, "get_positions", return_value=[
        {"Symbol": "7203", "Name": "Toyota", "LeavesQty": 100, "Price": 3000, "CurrentPrice": 3100},
        {"Symbol": "9984", "Name": "SoftBank", "LeavesQty": 50, "Price": 8000, "CurrentPrice": 8500},
    ])
    # broker_api.test_mode 属性アクセス対応
    service.broker_api.test_mode = False

    result = service.get_consolidated_portfolio(db=session)

    assert result["long_solid_value"] == 100 * 3100    # Toyota
    assert result["long_growth_value"] == 50 * 8500    # SoftBank
    assert result["bank_balance"] == 2000000
    assert result["buying_power"] == 500000
    assert result["short_term_market_value"] == 0      # 該当ポジションなし


def test_paper_buy_blocked_when_kill_switch_active(session, paper_engine, mocker):
    """PAPER モード買付でも、Kill Switch がアクティブなら execute_virtual_order がブロックされる
    (要件 §6 fail-safe / paper-trade-validator High finding 対応)。"""
    from models.schema import User_Settings
    # Kill Switch を Active 状態に設定
    user = User_Settings(id=1, is_kill_switch_active=True, max_drawdown_limit=-0.03)
    session.add(user)
    session.commit()

    # 価格取得は成功 (yfinance モック不要 — ブロックは get_realtime_price 前)
    mocker.patch.object(paper_engine, "get_realtime_price", return_value=3000.0)
    paper_engine.cash_balance = 10000000.0  # 十分な資金 (資金不足以外でブロックされること確認)

    success = paper_engine.execute_virtual_order(
        "7203", "Toyota", qty=100, is_buy=True, db=session
    )

    assert success is False, "Kill Switch アクティブ時の PAPER 買付はブロックされるべき"
    assert paper_engine.cash_balance == 10000000.0, "ブロック時に cash_balance は変動しないこと"
    assert len(paper_engine.positions) == 0, "ブロック時にポジションが追加されないこと"


def test_paper_sell_allowed_when_kill_switch_active(session, paper_engine, mocker):
    """Kill Switch アクティブでも sell (決済) は通る (非対称オーバーライド / 要件 §6)。"""
    from models.schema import User_Settings
    user = User_Settings(id=1, is_kill_switch_active=True, max_drawdown_limit=-0.03)
    session.add(user)
    session.commit()

    paper_engine.cash_balance = 0.0
    paper_engine.positions = [
        {"symbol": "7203", "name": "Toyota", "category": "Short",
         "shares": 100, "avg_price": 2900, "current_price": 3000, "unrealized_pnl": 10000}
    ]
    mocker.patch.object(paper_engine, "get_realtime_price", return_value=3000.0)

    success = paper_engine.execute_virtual_order(
        "7203", "Toyota", qty=100, is_buy=False, db=session
    )

    assert success is True, "Kill Switch アクティブでも sell は通るべき (非対称オーバーライド)"
    assert len(paper_engine.positions) == 0, "全数量 sell 後にポジションがクローズされること"


def test_real_mode_unknown_ticker_falls_back_to_short(session, monkeypatch, mocker):
    """Asset_Master 未登録銘柄は Short bucket にフォールバックすること。"""
    monkeypatch.setenv("TRADE_MODE", "REAL")

    service = PortfolioSyncService()
    mocker.patch.object(service.broker_api, "get_cash_balance", return_value=0)
    mocker.patch.object(service.bank_api, "get_account_balance", return_value=0)
    mocker.patch.object(service.broker_api, "get_positions", return_value=[
        {"Symbol": "9999", "Name": "Unknown", "LeavesQty": 100, "Price": 1000, "CurrentPrice": 1100},
    ])
    service.broker_api.test_mode = False

    result = service.get_consolidated_portfolio(db=session)

    assert result["short_term_market_value"] == 100 * 1100
    assert result["short_term_capital"] == 100 * 1000
    assert result["long_solid_value"] == 0
