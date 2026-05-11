"""
ペーパートレード（仮想取引）エンジン
yfinanceを使用してリアルタイム株価を取得し、仮想の資金とポジションを管理する。
"""

import os
import yfinance as yf
from typing import Dict, Any, List

class PaperTrader:
    _instance = None
    
    def __new__(cls):
        # シングルトンパターン: アプリケーション全体で仮想資金の状態を共有するため
        if cls._instance is None:
            cls._instance = super(PaperTrader, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        # 仮想の初期資金 (Phase 8c / ADR-0012: 環境変数 PAPER_INITIAL_CAPITAL で設定可能)
        # PAPER / PAPER_LIVE 両モードで使われる「架空の証券口座入金額」
        try:
            self.initial_capital = float(os.getenv("PAPER_INITIAL_CAPITAL", "1000000"))
        except ValueError:
            self.initial_capital = 1000000.0
        self.cash_balance = self.initial_capital
        # 仮想ポジションのリスト
        self.positions: List[Dict[str, Any]] = []
        # トレード履歴
        self.trade_history: List[Dict[str, Any]] = []

    def get_realtime_price(self, symbol: str) -> float:
        """
        現在の株価を取得する (Phase 8c / ADR-0012):
        - Step 1: services/market_data Layer 1 deque の最新 tick を優先 (TRADE_MODE=PAPER_LIVE で kabu Push 由来)
        - Step 2: Layer 1 空 (取引時間外 / WebSocket 未接続) なら yfinance に fallback
        - 日本の銘柄 (例: 7203) は yfinance では末尾に '.T' を付ける必要がある
        """
        # Step 1: Layer 1 deque から最新 tick を確認
        try:
            from services.market_data import get_recent_ticks
            ticks = get_recent_ticks(symbol, n=1)
            if ticks:
                return float(ticks[-1].get("last_price", 0))
        except Exception as e:
            # Layer 1 アクセス失敗は致命的ではない (yfinance fallback で続行)
            import logging
            logging.getLogger(__name__).debug(f"[PaperTrader] Layer 1 lookup failed for {symbol}: {e}")

        # Step 2: yfinance fallback
        try:
            yf_symbol = f"{symbol}.T" if not symbol.endswith(".T") else symbol
            ticker = yf.Ticker(yf_symbol)
            data = ticker.history(period="1d")
            if data.empty:
                return 0.0
            current_price = data['Close'].iloc[-1]
            return float(current_price)
        except Exception as e:
            print(f"yfinance Get Price Error for {symbol}: {e}")
            return 0.0

    def execute_virtual_order(self, symbol: str, name: str, qty: int, is_buy: bool, db=None, bucket=None) -> bool:
        """
        仮想注文を実行する。

        :param db: SQLAlchemy session (Optional)。指定されると buy 時にキルスイッチ判定を実施。
        :param bucket: bucket 名 ('Passive' / 'Long_Solid' / 'Long_Growth' / 'Short') を渡すと
                       全体 OR bucket 別フラグの OR で判定 (Phase 6 / ADR-0010)。
                       None のときは全体フラグのみチェック (Phase 4/5 互換挙動)。

        キルスイッチが Active なら買付をブロックする (要件 §6 / CLAUDE.md 絶対禁止 3)。
        PAPER モードでも DD>3% で kill_switch が発火するため、PAPER 買付もブロック対象。
        既存ポジションの決済 (sell) はブロック対象外 (非対称オーバーライド)。
        """
        # Kill Switch 事前チェック (要件 §6 fail-safe / 新規エントリーのみブロック)
        if is_buy and db is not None:
            from core.kill_switch import assert_inactive_for_entry, KillSwitchError
            try:
                assert_inactive_for_entry(db, bucket=bucket)
            except KillSwitchError:
                # ブロック自体は notify_critical 内部で通知済み (kill_switch.py)
                return False

        current_price = self.get_realtime_price(symbol)
        if current_price <= 0:
            print(f"Failed to get price for {symbol}. Order aborted.")
            return False

        cost = current_price * qty

        if is_buy:
            if self.cash_balance < cost:
                print("Insufficient virtual funds.")
                return False
            
            self.cash_balance -= cost
            
            # 既存ポジションの確認
            existing = next((p for p in self.positions if p["symbol"] == symbol), None)
            if existing:
                # 平均取得単価の更新 (簡易計算)
                total_cost = (existing["avg_price"] * existing["shares"]) + cost
                existing["shares"] += qty
                existing["avg_price"] = total_cost / existing["shares"]
                existing["current_price"] = current_price
            else:
                self.positions.append({
                    "symbol": symbol,
                    "name": name,
                    "category": "Short", # ペーパートレードは主にデイトレを想定
                    "shares": qty,
                    "avg_price": current_price,
                    "current_price": current_price,
                    "unrealized_pnl": 0.0
                })
            
            self.trade_history.append({"symbol": symbol, "action": "BUY", "price": current_price, "qty": qty})
            print(f"[PAPER TRADE] BOUGHT {qty} shares of {symbol} at ¥{current_price}")
            return True
            
        else:
            # Sell (今回は簡易的な決済のみ)
            existing = next((p for p in self.positions if p["symbol"] == symbol), None)
            if not existing or existing["shares"] < qty:
                print("Insufficient shares to sell.")
                return False
                
            self.cash_balance += cost
            pnl = (current_price - existing["avg_price"]) * qty
            
            existing["shares"] -= qty
            if existing["shares"] == 0:
                self.positions.remove(existing)
                
            self.trade_history.append({"symbol": symbol, "action": "SELL", "price": current_price, "qty": qty, "pnl": pnl})
            print(f"[PAPER TRADE] SOLD {qty} shares of {symbol} at ¥{current_price}. PnL: ¥{pnl}")
            return True

    def update_positions_market_value(self):
        """
        全仮想ポジションの最新価格を yfinance から取得して再計算する
        """
        for p in self.positions:
            latest_price = self.get_realtime_price(p["symbol"])
            if latest_price > 0:
                p["current_price"] = latest_price
                p["unrealized_pnl"] = (latest_price - p["avg_price"]) * p["shares"]

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """
        ダッシュボード表示用のポートフォリオサマリを生成する。
        要件 §2.2 (4-bucket 構造) と schema.Daily_Asset_Snapshot に整合した出力。
        フロントエンド既存型 (PortfolioSummary) との互換性のため旧フィールドも併記。
        """
        self.update_positions_market_value()

        # bucket 別集計 (Asset_Master.category enum 値: Passive / Long_Solid / Long_Growth / Short)
        bucket_market: Dict[str, float] = {"Passive": 0.0, "Long_Solid": 0.0, "Long_Growth": 0.0, "Short": 0.0}
        bucket_capital: Dict[str, float] = {"Passive": 0.0, "Long_Solid": 0.0, "Long_Growth": 0.0, "Short": 0.0}
        for p in self.positions:
            category = p.get("category", "Short")
            market_value = p["current_price"] * p["shares"]
            capital = p["avg_price"] * p["shares"]
            bucket_market[category] = bucket_market.get(category, 0.0) + market_value
            bucket_capital[category] = bucket_capital.get(category, 0.0) + capital

        trust_value = bucket_market["Passive"]
        long_solid_value = bucket_market["Long_Solid"]
        long_growth_value = bucket_market["Long_Growth"]
        short_market = bucket_market["Short"]
        short_capital = bucket_capital["Short"]
        total_long_value = long_solid_value + long_growth_value
        total_value = self.cash_balance + trust_value + total_long_value + short_market

        # trade_history をフロントエンド用の recent_activity フォーマットに変換
        from datetime import datetime
        import uuid
        recent_activity = []
        for i, trade in enumerate(reversed(self.trade_history)): # 新しいものから順に
            action = trade.get("action", "BUY")
            qty = trade.get("qty", 0)
            price = trade.get("price", 0)
            symbol = trade.get("symbol", "")

            amount = qty * price
            msg = f"Paper Trade: Bought {qty} shares of {symbol}" if action == "BUY" else f"Paper Trade: Sold {qty} shares of {symbol}"
            if "pnl" in trade:
                msg += f" (PnL: ¥{trade['pnl']})"

            recent_activity.append({
                "id": str(uuid.uuid4()),
                "type": action.capitalize(),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"), # 簡易的に現在時刻（本来はトレード時刻を保存すべき）
                "message": msg,
                "amount": amount,
                "symbol": symbol
            })

        return {
            # ===== 新 schema (Daily_Asset_Snapshot 整合 / 要件 §2.2 §8) =====
            "bank_balance": 0,                                  # PAPER モード: 銀行プールなし (証券口座のみシミュレート)
            "buying_power": int(self.cash_balance),             # 証券口座キャッシュ (= 買付余力)
            "trust_value": int(trust_value),
            "long_solid_value": int(long_solid_value),
            "long_growth_value": int(long_growth_value),
            "short_term_capital": int(short_capital),
            "short_term_market_value": int(short_market),
            "cumulative_sweep_to_long_solid": 0,                # PAPER モードでは累計振替を別途追跡
            # ===== 互換性: フロントエンド既存 PortfolioSummary 型 (frontend M11 で段階移行予定) =====
            "long_value": int(total_long_value),
            "short_value": int(short_market),
            "cash_balance": int(self.cash_balance),
            "total_value": int(total_value),
            "accumulated_sweep": 0,
            # ===== 表示・メタ =====
            "positions": self.positions,
            "recent_activity": recent_activity,
            "is_mock": True,
            "trade_mode": "PAPER",
        }

# グローバルインスタンス
paper_trader_engine = PaperTrader()
