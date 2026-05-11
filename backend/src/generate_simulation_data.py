import os
import sys
from datetime import datetime, timedelta, date

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from models.database import engine, Base, SessionLocal
from models.schema import Daily_Asset_Snapshot, Trade_Logs, System_Logs, Asset_Master, AssetCategory

def generate_data():
    # データベースのテーブルを作成
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # 1. 既存のデモデータをクリア
    db.query(Daily_Asset_Snapshot).delete()
    db.query(Trade_Logs).delete()
    db.query(System_Logs).delete()
    db.query(Asset_Master).delete()
    
    # 2. 銘柄マスタの登録
    assets = [
        Asset_Master(ticker_symbol="7203", asset_name="Toyota Motor", category=AssetCategory.long_solid),
        Asset_Master(ticker_symbol="9984", asset_name="SoftBank Group", category=AssetCategory.long_growth),
        Asset_Master(ticker_symbol="1570", asset_name="Nikkei 225 Lev", category=AssetCategory.short)
    ]
    db.add_all(assets)
    db.commit()

    # 3. 日次資産スナップショット（過去30日分の右肩上がりのデータ）
    today = date.today()
    for i in range(30, -1, -1):
        d = today - timedelta(days=i)
        # ランダム性を持たせつつ成長するデータ
        growth_factor = (30 - i)
        
        # 5日前に仮想的な「下落（ショック）」を演出
        dip = 300000 if i == 5 else 0
        
        snap = Daily_Asset_Snapshot(
            date=d,
            bank_balance=1000000, # 生活防衛費
            buying_power=8000000 + growth_factor * 10000, # 買付余力
            trust_value=1000000 + growth_factor * 8000, # 投資信託
            long_solid_value=500000 + growth_factor * 15000 - dip,
            long_growth_value=500000 + growth_factor * 25000 - dip * 1.5,
            short_term_capital=3000000,
            short_term_market_value=3000000 + growth_factor * 30000 - dip * 2,
            cumulative_sweep_to_long_solid=growth_factor * 10000
        )
        db.add(snap)
    db.commit()
    
    # 4. トレード履歴とAI判断理由（損失ドリルダウンのデモ用）
    logs = [
        Trade_Logs(
            timestamp=datetime.now() - timedelta(days=2),
            ticker_symbol="7203", action="BUY", quantity=100, price=3500,
            decision_reason="[Long Solid Strategy / 割安判定]\\n[PROPRIETARY LOGIC REDACTED]\\nセキュリティおよび戦略上の優位性を保護するため、具体的な購入理由は非公開です。"
        ),
        Trade_Logs(
            timestamp=datetime.now() - timedelta(days=1),
            ticker_symbol="1570", action="BUY", quantity=50, price=25000,
            decision_reason="[Short VWAP Strategy / モメンタム]\\n[PROPRIETARY LOGIC REDACTED]\\nセキュリティおよび戦略上の優位性を保護するため、具体的なエントリー理由は非公開です。"
        ),
        Trade_Logs(
            timestamp=datetime.now() - timedelta(hours=5),
            ticker_symbol="1570", action="SELL", quantity=50, price=24500, pnl=-25000,
            decision_reason="[Short VWAP Strategy / Loss Cut (損切り)]\\n[PROPRIETARY LOGIC REDACTED]\\nセキュリティおよび戦略上の優位性を保護するため、具体的な損切り理由は非公開です。"
        ),
        Trade_Logs(
            timestamp=datetime.now() - timedelta(hours=1),
            ticker_symbol="9984", action="BUY", quantity=100, price=8500,
            decision_reason="[Long Growth Strategy / NAVディスカウント]\\n[PROPRIETARY LOGIC REDACTED]\\nセキュリティおよび戦略上の優位性を保護するため、具体的な購入理由は非公開です。"
        )
    ]
    db.add_all(logs)
    db.commit()

    # 5. システムログ（ステータス正常化のデモ）
    syslogs = [
        System_Logs(timestamp=datetime.now() - timedelta(minutes=10), level="INFO", component="[Scheduler]", event="Daily Rebalance Check: No major deviations found."),
        System_Logs(timestamp=datetime.now() - timedelta(minutes=5), level="INFO", component="[Risk Mgmt]", event="Overnight Risk Cleared: All Intraday short positions safely closed."),
        System_Logs(timestamp=datetime.now() - timedelta(minutes=1), level="INFO", component="[Bank API]", event="Bank Reserve Confirmed: ¥1,000,000 (Secured)")
    ]
    db.add_all(syslogs)
    db.commit()
    db.close()
    print("Successfully generated Paper Trading Simulation data!")

if __name__ == '__main__':
    generate_data()
