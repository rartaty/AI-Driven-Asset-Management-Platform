"""
定期実行スケジューラー
APSchedulerを使用して、システムの自動運用（時間割）を管理する。
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime
import logging

# 各種サービスのインポート
from services.portfolio_sync import PortfolioSyncService
from services.ai_analyzer import TradeAnalyzer
from models.database import SessionLocal
from core.kill_switch import check_drawdown_and_trigger
from core.discord import notify_system, notify_trade

logger = logging.getLogger(__name__)

class SystemScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.portfolio_service = PortfolioSyncService()
        self.ai_analyzer = TradeAnalyzer()

    def start(self):
        """
        スケジューラーを起動し、各種ジョブを登録する。
        """
        self._register_jobs()
        self.scheduler.start()
        logger.info(f"[{datetime.now()}] System Scheduler Started.")
        notify_system("System Scheduler Started.", component="Scheduler")

    def shutdown(self):
        """
        スケジューラーを安全に終了する。
        """
        self.scheduler.shutdown()
        logger.info(f"[{datetime.now()}] System Scheduler Shutdown.")
        notify_system("System Scheduler Shutdown.", component="Scheduler")

    def _register_jobs(self):
        """
        時間割（運用ルール）に従ってジョブを登録する。
        """
        # 1. 朝の準備: 08:30にトークン取得と残高同期 (M5)
        self.scheduler.add_job(
            self.job_morning_sync,
            CronTrigger(day_of_week='mon-fri', hour=8, minute=30),
            id="morning_sync"
        )

        # 2. 短期トレード監視（前場）: 09:15 - 11:30 の間 (P6)
        self.scheduler.add_job(
            self.job_short_term_trade,
            CronTrigger(day_of_week='mon-fri', hour='9-11', minute='*'),
            id="morning_trade"
        )

        # 3. 短期トレード監視（後場）: 12:30 - 14:30 の間 (P6)
        self.scheduler.add_job(
            self.job_short_term_trade,
            CronTrigger(day_of_week='mon-fri', hour='12-14', minute='*'),
            id="afternoon_trade"
        )

        # 4. 強制全決済＆振替 (Profit Sweep): 14:50 に実行（オーバーナイトリスク排除）
        self.scheduler.add_job(
            self.job_profit_sweep,
            CronTrigger(day_of_week='mon-fri', hour=14, minute=50),
            id="profit_sweep"
        )

        # 5. AI反省レポート自動生成: 16:00 に実行
        self.scheduler.add_job(
            self.job_generate_ai_report,
            CronTrigger(day_of_week='mon-fri', hour=16, minute=0),
            id="ai_report"
        )

        # 6. ドローダウン監視 (Kill Switch自動発動判定): 毎日15:30に実行
        self.scheduler.add_job(
            self.job_check_kill_switch,
            CronTrigger(day_of_week='mon-fri', hour=15, minute=30),
            id="kill_switch_check"
        )

        # 7. 定期リバランス (Weekly Rebalance): 金曜15:00に実行 (M6)
        self.scheduler.add_job(
            self.job_weekly_rebalance,
            CronTrigger(day_of_week='fri', hour=15, minute=0),
            id="weekly_rebalance"
        )

        # 8. 四半期定期見直し: 1/4/7/10月 第1月曜 10:00 (要件 §2.2 (a) / P9)
        self.scheduler.add_job(
            self.job_quarterly_review,
            CronTrigger(month='1,4,7,10', day='1-7', day_of_week='mon', hour=10, minute=0),
            id="quarterly_review"
        )

        # 9. Market_Ticks Layer 2 永続化: 毎分 (取引時間中) (Phase 7 / ADR-0009)
        self.scheduler.add_job(
            self.job_flush_market_ticks,
            CronTrigger(day_of_week='mon-fri', hour='9-15', minute='*'),
            id="market_ticks_flush"
        )

        # 10. PAPER モード yfinance pump: 毎分 (取引時間中) (Phase 7 / ADR-0009)
        # PAPER 時のみ動作 (TRADE_MODE=REAL は kabu Push WebSocket が代替)
        self.scheduler.add_job(
            self.job_paper_pump,
            CronTrigger(day_of_week='mon-fri', hour='9-14', minute='*'),
            id="paper_pump"
        )

        # 11. 日次 SQLite バックアップ: 14:55 mon-fri (取引時間後・Profit Sweep 直後) (Phase 6 / ADR-0010 §6-2)
        self.scheduler.add_job(
            self.job_backup_daily,
            CronTrigger(day_of_week='mon-fri', hour=14, minute=55),
            id="backup_daily"
        )

        # 12. 週次 bucket 別 JSON 論理 dump: 金 16:00 (AI レポート後) (Phase 6 / ADR-0010 §6-2)
        self.scheduler.add_job(
            self.job_export_buckets_weekly,
            CronTrigger(day_of_week='fri', hour=16, minute=0),
            id="export_buckets_weekly"
        )

    # ==========================================
    # ジョブの実体（手動テスト用に外部からも呼び出し可能にする）
    # ==========================================

    def job_check_kill_switch(self):
        logger.info(f"[{datetime.now()}] JOB: Checking Drawdown for Kill Switch...")
        db = SessionLocal()
        try:
            triggered = check_drawdown_and_trigger(db)
            if triggered:
                logger.critical("Kill Switch was triggered by scheduled job!")
                # notify_critical is already called by check_drawdown_and_trigger internally
        finally:
            db.close()

    def job_morning_sync(self):
        logger.info(f"[{datetime.now()}] JOB: Morning Sync (Token Fetch & Prep)...")
        from api.kabucom import KabucomAPIClient
        client = KabucomAPIClient()
        if client.authenticate():
            logger.info("-> Kabucom API authenticated.")
            notify_system("Morning Sync: Kabucom API authenticated.", component="Scheduler")
        else:
            logger.error("-> Failed to authenticate Kabucom API.")
            notify_system("Morning Sync: Failed to authenticate Kabucom API.", component="Scheduler")

        # Phase 7: kabu Push WebSocket 接続開始 (REAL モードのみ実接続、PAPER は no-op)
        try:
            from services.kabu_push_client import get_client
            push_client = get_client()
            push_client.start()
            logger.info("[KabuPush] WebSocket client started for the day")
        except Exception as e:
            logger.error(f"[KabuPush] Failed to start client: {e}")
            notify_system(f"Kabu Push start failed: {e}", component="KabuPush")

        # ポートフォリオ同期も実施
        self.job_sync_portfolio()

    def job_sync_portfolio(self):
        """ポートフォリオ同期 + Daily_Asset_Snapshot への日次永続化 (要件 §8)。"""
        logger.info(f"[{datetime.now()}] JOB: Synchronizing Portfolio from APIs...")
        db = SessionLocal()
        try:
            snapshot = self.portfolio_service.write_daily_snapshot(db)
            total_value = (
                snapshot.bank_balance + snapshot.buying_power
                + snapshot.trust_value + snapshot.long_solid_value + snapshot.long_growth_value
                + snapshot.short_term_market_value
            )
            logger.info(f"-> Synced & Persisted Total Asset: ¥{total_value}")
            notify_system(f"Synced & Persisted Total Asset: ¥{total_value}", component="Scheduler")
        except Exception as e:
            logger.error(f"[Scheduler] portfolio sync failed: {e}")
            notify_system(f"Portfolio sync failed: {e}", component="Scheduler")
        finally:
            db.close()

    def job_short_term_trade(self):
        """短期トレード: Layer 1 deque から VWAP/Z スコアを評価しシグナル発火。

        Phase 7 (ADR-0009) で実装、Phase 8c (ADR-0012) で TRADE_MODE 三値化:
        - PAPER     : qty=100 固定 + paper_trader 仮想発注 (yfinance 疑似 tick / 初期検証用)
        - PAPER_LIVE: position sizing + paper_trader 仮想発注 (kabu 実 tick / 実マーケット検証)
        - REAL      : position sizing + kabucom.place_order 実発注 (Phase 8d で実装)

        共通:
        - 取引時間 (09:15-11:30 / 12:30-14:30) 限定
        - Asset_Master.category=Short の銘柄のみ評価
        - bucket='Short' で kill switch チェック (Phase 6 / ADR-0010)
        """
        from datetime import time
        current_time = datetime.now().time()
        is_morning = time(9, 15) <= current_time <= time(11, 30)
        is_afternoon = time(12, 30) <= current_time <= time(14, 30)

        if not (is_morning or is_afternoon):
            return

        logger.info(f"[{datetime.now()}] JOB: Evaluating Short-Term Signals...")

        from strategy.vwap_short import evaluate_vwap_signal
        from services.paper_trader import paper_trader_engine
        from services.position_sizing import calculate_position_qty
        from models.schema import Asset_Master, AssetCategory
        import os

        trade_mode = os.getenv("TRADE_MODE", "PAPER").upper()

        db = SessionLocal()
        try:
            symbols = [
                a.ticker_symbol for a in db.query(Asset_Master).filter(
                    Asset_Master.category == AssetCategory.short,
                    Asset_Master.is_active == True,
                ).all()
            ]
            if not symbols:
                logger.debug("[VWAPSignal] No active short-bucket symbols in Asset_Master")
                return

            for symbol in symbols:
                sig = evaluate_vwap_signal(symbol)
                if sig["signal"] == "HOLD":
                    continue

                logger.info(f"[VWAPSignal] {symbol}: {sig['signal']} ({sig['reason']})")
                # 要件 §2.3: AI 推奨銘柄検知時に Discord へ即時プッシュ通知
                notify_trade(
                    f"VWAP Signal {sig['signal']} for {symbol}: {sig['reason']}",
                    component="VWAPSignal",
                )

                is_buy = sig["signal"] == "BUY"
                price = int(sig["current_price"])

                if trade_mode == "PAPER":
                    # PAPER: qty=100 固定 (yfinance 疑似 tick の初期検証用)
                    paper_trader_engine.execute_virtual_order(
                        symbol, name=f"Asset-{symbol}", qty=100, is_buy=is_buy,
                        db=db, bucket="Short",
                    )
                elif trade_mode == "PAPER_LIVE":
                    # PAPER_LIVE: 動的 position sizing + paper_trader 仮想発注
                    qty = calculate_position_qty(db, price, bucket="Short")
                    if qty == 0:
                        logger.info(
                            f"[VWAPSignal] {symbol}: insufficient buying_power or daily limit reached, skip"
                        )
                        continue
                    paper_trader_engine.execute_virtual_order(
                        symbol, name=f"Asset-{symbol}", qty=qty, is_buy=is_buy,
                        db=db, bucket="Short",
                    )
                else:  # REAL
                    # Phase 8d で本番発注パス完成 (kabucom.place_order)
                    logger.warning(
                        f"[VWAPSignal] REAL-mode order placement deferred to Phase 8d ({symbol}: {sig['signal']})"
                    )
        finally:
            db.close()

    def job_flush_market_ticks(self):
        """Layer 1 deque の tick を Layer 2 (Market_Ticks) へ bulk insert (Phase 7 / ADR-0009)。"""
        from datetime import time
        current_time = datetime.now().time()
        if not (time(9, 0) <= current_time <= time(15, 30)):
            return

        from services.market_data import get_layer1, flush_layer2
        layer1 = get_layer1()
        ticks = layer1.drain_all()
        if not ticks:
            return

        db = SessionLocal()
        try:
            count = flush_layer2(db, ticks)
            logger.debug(f"[MarketData] Flushed {count} ticks to Layer 2 (Market_Ticks)")
        except Exception as e:
            logger.error(f"[MarketData] flush_layer2 failed: {e}")
            notify_system(f"Market_Ticks flush failed: {e}", component="MarketData")
        finally:
            db.close()

    def job_paper_pump(self):
        """PAPER モード: yfinance 1 分足を疑似 tick に変換して Layer 1 へ feed (Phase 7)。

        REAL モードでは kabu Push WebSocket が代替経路となるため no-op。
        """
        import os
        if os.getenv("TRADE_MODE", "PAPER").upper() != "PAPER":
            return

        from datetime import time as _time
        current_time = datetime.now().time()
        if not (_time(9, 0) <= current_time <= _time(15, 0)):
            return

        from services.market_data import yfinance_to_pseudo_ticks, get_layer1
        from models.schema import Asset_Master, AssetCategory
        import yfinance as yf

        db = SessionLocal()
        try:
            symbols = [
                a.ticker_symbol for a in db.query(Asset_Master).filter(
                    Asset_Master.category == AssetCategory.short,
                    Asset_Master.is_active == True,
                ).all()
            ]
        finally:
            db.close()

        if not symbols:
            return

        layer1 = get_layer1()
        for symbol in symbols:
            try:
                yf_symbol = f"{symbol}.T" if not symbol.endswith(".T") else symbol
                tk = yf.Ticker(yf_symbol)
                df = tk.history(period="2m", interval="1m")
                if df.empty:
                    continue
                ticks = yfinance_to_pseudo_ticks(symbol, df.tail(1))
                for t in ticks:
                    layer1.push_tick(t)
            except Exception as e:
                logger.error(f"[PaperPump] {symbol} failed: {e}")

    def job_profit_sweep(self):
        logger.info(f"[{datetime.now()}] JOB: Executing Profit Sweep (Force Close All Day-Trade Positions)")
        notify_system("Executing Profit Sweep...", component="Scheduler")
        import os
        trade_mode = os.getenv("TRADE_MODE", "PAPER").upper()
        
        # Phase 8c (ADR-0012): PAPER + PAPER_LIVE 両方で paper_trader 経路 (実 price は Layer 1 から取得される)
        if trade_mode in ("PAPER", "PAPER_LIVE"):
            from services.paper_trader import paper_trader_engine
            # 仮想ポジションの全決済
            positions = list(paper_trader_engine.positions) # コピーを作成してイテレート
            db_for_sweep = SessionLocal()
            try:
                for p in positions:
                    symbol = p["symbol"]
                    qty = p["shares"]
                    name = p["name"]
                    logger.info(f"-> Closing Paper Position: {symbol} ({qty} shares)")
                    notify_trade(f"Closing Paper Position: {symbol} ({qty} shares)", component="ProfitSweep")
                    # is_buy=False なので Kill Switch はバイパスされるが、シグネチャ一貫性のため db を渡す
                    paper_trader_engine.execute_virtual_order(symbol, name, qty, is_buy=False, db=db_for_sweep)
            finally:
                db_for_sweep.close()
            logger.info("-> All paper positions closed.")
            notify_system("All paper positions closed.", component="ProfitSweep")
        else:  # REAL
            # Phase 8d で本実装 (kabucom.get_positions → 反対売買 place_order)
            logger.warning("[ProfitSweep] REAL-mode profit sweep deferred to Phase 8d")
            pass

        # Phase 7: 場引け時に kabu Push WebSocket 切断 (オーバーナイト不要)
        try:
            from services.kabu_push_client import get_client
            push_client = get_client()
            if push_client.is_running():
                push_client.stop()
                logger.info("[KabuPush] WebSocket stopped at market close")
                notify_system("Kabu Push WebSocket stopped at market close", component="KabuPush")
        except Exception as e:
            logger.error(f"[KabuPush] Stop failed: {e}")
            notify_system(f"Kabu Push stop failed: {e}", component="KabuPush")

    def job_generate_ai_report(self):
        logger.info(f"[{datetime.now()}] JOB: Generating Daily AI Report...")
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        import os
        trade_mode = os.getenv("TRADE_MODE", "PAPER").upper()
        
        if trade_mode == "PAPER":
            from services.paper_trader import paper_trader_engine
            trades = paper_trader_engine.trade_history
        else:
            # TODO: 本番環境でのDBからの取引履歴取得
            trades = [{"symbol": "7203", "pnl": 2000, "memo": "Scheduled job test"}]
        
        report = self.ai_analyzer.generate_daily_report(trades, today_str)
        logger.info(f"-> AI Report Generated (Length: {len(report)} chars)")
        notify_system(f"AI Report Generated (Length: {len(report)} chars)", component="AIReport")

    def job_weekly_rebalance(self):
        """週次リバランス (要件 §2.2 / P9)。

        1. 相場環境を更新 (VIXギア発火時は Target_Portfolio も自動更新される)
        2. 実評価額を Daily_Asset_Snapshot 経由で取得
        3. 動的比率を Target_Portfolio から読込
        4. 株式内 60/40 (Long/Short) 固定分解で passive_core 用比率にマップ
        5. リバランス計算を実行
        """
        logger.info(f"[{datetime.now()}] JOB: Executing Weekly Rebalance...")
        from strategy.passive_core import calculate_rebalance_amounts
        from services.market_context import update_market_context, evaluate_vix_gear
        from services.target_portfolio import get_active_ratios

        db = SessionLocal()
        try:
            # 1. 相場環境の更新 (VIXギア発火時は Target_Portfolio も自動書込される)
            context = update_market_context(db)
            db.commit()

            mode = evaluate_vix_gear(context.vix)
            vix_triggered = mode in ["DEFEND", "ATTACK"]

            # 2. 実評価額を取得 (Daily_Asset_Snapshot 整合の portfolio_sync 経由)
            portfolio = self.portfolio_service.get_consolidated_portfolio(db=db)
            trust_value = portfolio["trust_value"]
            long_value = portfolio["long_solid_value"] + portfolio["long_growth_value"]
            short_value = portfolio["short_term_market_value"]

            # 3. Target_Portfolio から動的比率を取得 (cash/trust/stocks)
            active = get_active_ratios(db)

            # 4. 株式内 60% Long / 40% Short の固定分解 (要件 §2.2)
            #    cash 比率は buying_power として保留される (rebalance 計算には含めない)
            #    invested 部分 (trust + stocks) で正規化
            invested_pct = active["trust"] + active["stocks"]
            if invested_pct <= 0:
                logger.warning("-> Target_Portfolio invested ratio is 0, skipping rebalance.")
                return

            target_ratios = {
                "trust": active["trust"] / invested_pct,
                "long":  (active["stocks"] * 0.60) / invested_pct,
                "short": (active["stocks"] * 0.40) / invested_pct,
            }

            # 5. リバランス計算
            res = calculate_rebalance_amounts(
                trust_value=trust_value,
                long_value=long_value,
                short_value=short_value,
                target_ratios=target_ratios,
                vix_triggered=vix_triggered,
            )

            if vix_triggered:
                logger.info(
                    f"-> Rebalance computed (VIX mode: {mode}, ratios={target_ratios}): {res}"
                )
                notify_system(f"Rebalance computed (VIX mode: {mode})", component="Scheduler")
            else:
                logger.info("-> VIX is NEUTRAL, no forced rebalance.")
        except Exception as e:
            logger.error(f"[Scheduler] Weekly rebalance failed: {e}")
            notify_system(f"Weekly rebalance failed: {e}", component="Scheduler")
        finally:
            db.close()

    def job_backup_daily(self):
        """日次 SQLite バックアップ + 30 日 rotate (Phase 6 / ADR-0010 §6-2)。

        取引時間後 (14:55) に backups/data-YYYYMMDD.db を生成し、30 日より古いものを削除。
        失敗時は Discord 通知 + ログのみ (メイン処理を止めない)。
        """
        logger.info(f"[{datetime.now()}] JOB: Daily SQLite Backup")
        try:
            # scripts/backup_db.py を import (sys.path に scripts/ が含まれない場合の保険)
            import sys as _sys
            from pathlib import Path as _Path
            scripts_dir = _Path(__file__).resolve().parent.parent.parent.parent / "scripts"
            if str(scripts_dir) not in _sys.path:
                _sys.path.insert(0, str(scripts_dir))
            from backup_db import run_daily_backup  # type: ignore[import-not-found]

            dest = run_daily_backup()
            msg = f"Daily backup completed: {dest.name}"
            logger.info(f"[Backup] {msg}")
            notify_system(msg, component="Backup")
        except Exception as e:
            logger.error(f"[Backup] Daily backup failed: {e}")
            notify_system(f"Daily backup failed: {e}", component="Backup")

    def job_export_buckets_weekly(self):
        """週次 bucket 別 JSON 論理 dump (Phase 6 / ADR-0010 §6-2)。

        金 16:00 (AI レポート後) に bucket 単位で Trade_Logs を JSON 出力。
        失敗時は Discord 通知 + ログのみ。
        """
        logger.info(f"[{datetime.now()}] JOB: Weekly Bucket Export")
        try:
            import sys as _sys
            from pathlib import Path as _Path
            scripts_dir = _Path(__file__).resolve().parent.parent.parent.parent / "scripts"
            if str(scripts_dir) not in _sys.path:
                _sys.path.insert(0, str(scripts_dir))
            from export_buckets import export_all_buckets  # type: ignore[import-not-found]

            db = SessionLocal()
            try:
                paths = export_all_buckets(db)
                msg = f"Weekly bucket export completed: {len(paths)} files"
                logger.info(f"[Export] {msg}")
                notify_system(msg, component="Export")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[Export] Weekly bucket export failed: {e}")
            notify_system(f"Weekly bucket export failed: {e}", component="Export")

    def job_quarterly_review(self):
        """四半期定期 Target_Portfolio 見直し (要件 §2.2 (a) / P9)。

        現状はベースライン比率を書込するプレースホルダ実装。
        将来は AI (Gemma 2 + マクロ評価) による動的提案へ置換予定 (Phase 6+)。
        """
        logger.info(f"[{datetime.now()}] JOB: Executing Quarterly Portfolio Review...")
        from services.target_portfolio import write_for_quarterly_review

        db = SessionLocal()
        try:
            target = write_for_quarterly_review(db)
            msg = (
                f"Quarterly review: Target updated "
                f"(cash={target.cash_target_pct:.2f}, "
                f"trust={target.trust_target_pct:.2f}, "
                f"stocks={target.stocks_target_pct:.2f})"
            )
            logger.info(f"-> {msg}")
            notify_system(msg, component="QuarterlyReview")
        except Exception as e:
            logger.error(f"[QuarterlyReview] failed: {e}")
            notify_system(f"Quarterly review failed: {e}", component="QuarterlyReview")
        finally:
            db.close()

# シングルトンインスタンスとして作成（main.pyで使い回すため）
system_scheduler = SystemScheduler()
