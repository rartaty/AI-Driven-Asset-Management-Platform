"""
ポートフォリオ同期サービス

証券APIと銀行APIのデータを統合し、Daily_Asset_Snapshot 整合の構造化データへ変換する。
要件:
- §2.2: 銀行プール (読取専用) と証券プール (AI 管理) を独立管理
- §2.2: 4-bucket 構造 (Passive / Long_Solid / Long_Growth / Short)
- §8: Daily_Asset_Snapshot への日次永続化
"""

import os
from typing import Dict, Any, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from api.kabucom import KabucomAPIClient
from api.opencanvas import OpenCanvasBankAPIClient
from .paper_trader import paper_trader_engine
from models.schema import Asset_Master, Daily_Asset_Snapshot
from datetime import date as _date


# 4-bucket 集計の zero-init テンプレート (Asset_Master.AssetCategory enum 値と一致)
_BUCKET_KEYS = ("Passive", "Long_Solid", "Long_Growth", "Short")


class PortfolioSyncService:
    def __init__(self):
        self.broker_api = KabucomAPIClient()
        self.bank_api = OpenCanvasBankAPIClient()

    def get_consolidated_portfolio(self, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        証券口座 (AI管理対象) と銀行口座 (読取専用) を統合したサマリを生成。

        :param db: Asset_Master からカテゴリ参照するための DB session (REAL モードで必須)
        :return: Daily_Asset_Snapshot 整合のフィールド + フロントエンド互換フィールドを併記した dict

        PAPER モード時は paper_trader_engine.get_portfolio_summary() を直接返却
        (こちらも同じ schema で出力するよう Phase 5 で統一済み)。
        """
        trade_mode = os.getenv("TRADE_MODE", "PAPER").upper()

        if trade_mode == "PAPER":
            return paper_trader_engine.get_portfolio_summary()

        # ---------------------------------------------------------
        # TRADE_MODE=REAL: 本番通信ロジック
        # ---------------------------------------------------------

        # 1. API データ取得 (銀行は読取専用 / 証券は AI 管理対象)
        bank_balance = self.bank_api.get_account_balance()        # 銀行プール
        broker_cash = self.broker_api.get_cash_balance()          # 証券キャッシュ (= 買付余力)
        positions = self.broker_api.get_positions()

        # 2. Asset_Master から銘柄カテゴリ参照テーブル構築
        #    DB 未提供時はカテゴリ判定不能 → Short にフォールバック (短期パスは ms 級判定が要件 §B.2)
        if db is not None:
            asset_categories = {a.ticker_symbol: a.category.value for a in db.query(Asset_Master).all()}
        else:
            asset_categories = {}

        # 3. bucket 別集計 (要件 §2.2)
        bucket_market: Dict[str, float] = {k: 0.0 for k in _BUCKET_KEYS}
        bucket_capital: Dict[str, float] = {k: 0.0 for k in _BUCKET_KEYS}
        formatted_positions = []

        for p in positions:
            symbol = p.get("Symbol", "Unknown")
            shares = p.get("LeavesQty", 0)
            avg_price = p.get("Price", 0)
            current_price = p.get("CurrentPrice", 0)
            unrealized_pnl = (current_price - avg_price) * shares

            # Asset_Master 未登録銘柄は Short 扱い (短期トレードで意図せず買った想定)
            category = asset_categories.get(symbol, "Short")
            market_value = current_price * shares
            capital = avg_price * shares
            bucket_market[category] = bucket_market.get(category, 0.0) + market_value
            bucket_capital[category] = bucket_capital.get(category, 0.0) + capital

            formatted_positions.append({
                "symbol": symbol,
                "name": p.get("Name", "Unknown"),
                "category": category,
                "shares": shares,
                "avg_price": avg_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
            })

        # 4. 累計 Profit Sweep 額は最新 snapshot から引き継ぎ (DB なしなら 0)
        cumulative_sweep = 0
        if db is not None:
            latest = db.query(Daily_Asset_Snapshot).order_by(desc(Daily_Asset_Snapshot.date)).first()
            if latest is not None:
                cumulative_sweep = latest.cumulative_sweep_to_long_solid

        trust_value = bucket_market["Passive"]
        long_solid_value = bucket_market["Long_Solid"]
        long_growth_value = bucket_market["Long_Growth"]
        short_market = bucket_market["Short"]
        short_capital = bucket_capital["Short"]
        total_long_value = long_solid_value + long_growth_value

        # 銀行は AI 管理対象外だが、サマリ表示の都合上 total_value には含める (UI 表示用)
        total_value = bank_balance + broker_cash + trust_value + total_long_value + short_market

        return {
            # ===== 新 schema (Daily_Asset_Snapshot 整合 / 要件 §2.2 §8) =====
            "bank_balance": int(bank_balance),
            "buying_power": int(broker_cash),
            "trust_value": int(trust_value),
            "long_solid_value": int(long_solid_value),
            "long_growth_value": int(long_growth_value),
            "short_term_capital": int(short_capital),
            "short_term_market_value": int(short_market),
            "cumulative_sweep_to_long_solid": int(cumulative_sweep),
            # ===== 互換性: フロントエンド既存 PortfolioSummary 型 (frontend M11 で段階移行予定) =====
            "long_value": int(total_long_value),
            "short_value": int(short_market),
            "cash_balance": int(broker_cash),
            "total_value": int(total_value),
            "accumulated_sweep": int(cumulative_sweep),
            # ===== 表示・メタ =====
            "positions": formatted_positions,
            "is_mock": self.broker_api.test_mode,
            "trade_mode": trade_mode,
        }

    def write_daily_snapshot(self, db: Session) -> Daily_Asset_Snapshot:
        """
        当日分の Daily_Asset_Snapshot を UPSERT で書込する (要件 §8)。

        - 当日レコードが既存 → 評価額のみ更新 (cumulative_sweep は別 path で増分管理)
        - 当日レコードが未存在 → 新規作成
        - PAPER モードでも記録対象 (paper_trader の擬似集計値で埋まる)

        :param db: SQLAlchemy session
        :return: 書き込まれた Daily_Asset_Snapshot レコード
        """
        portfolio = self.get_consolidated_portfolio(db=db)
        today = _date.today()

        snapshot = db.query(Daily_Asset_Snapshot).filter(Daily_Asset_Snapshot.date == today).first()
        if snapshot is None:
            snapshot = Daily_Asset_Snapshot(date=today)
            db.add(snapshot)

        snapshot.bank_balance = portfolio["bank_balance"]
        snapshot.buying_power = portfolio["buying_power"]
        snapshot.trust_value = portfolio["trust_value"]
        snapshot.long_solid_value = portfolio["long_solid_value"]
        snapshot.long_growth_value = portfolio["long_growth_value"]
        snapshot.short_term_capital = portfolio["short_term_capital"]
        snapshot.short_term_market_value = portfolio["short_term_market_value"]
        snapshot.cumulative_sweep_to_long_solid = portfolio["cumulative_sweep_to_long_solid"]
        db.commit()
        db.refresh(snapshot)
        return snapshot
