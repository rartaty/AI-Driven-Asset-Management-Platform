"""
データベーススキーマ定義 (ORMモデル)

参照仕様:
- docs/REQUIREMENTS_DEFINITION.md §8 (主要エンティティ一覧)
- docs/TECHNICAL_SPECIFICATION.md §6 (DB構造詳細)
- docs/infrastructure/DATA_FOUNDATION.md §4 (テーブル一覧と設計原則)
- docs/operations/KILL_SWITCH_V2_SPEC.md §3 (Kill Switch 永続化)

設計原則:
- 金額系カラムは BigInteger (32bit Int = ¥21億 上限の将来性問題回避)
- 時刻情報不要なら DateTime ではなく Date を使う
- JSON 型は SQLAlchemy 汎用 JSON (SQLite/PostgreSQL 両対応、本番化時は JSONB へ昇格検討)
- Survivor Bias 排除: 上場廃止銘柄は物理削除せず is_active=False で論理削除
- 冪等性: 売買ログ等は UUID (将来) で二重発注を弾く
"""
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, Float,
    DateTime, Date, Enum, Text, JSON, ForeignKey, Index
)
from sqlalchemy.sql import func
from .database import Base
import enum


# ===== Enums =====

class AssetCategory(str, enum.Enum):
    """銘柄カテゴリ (3層ポートフォリオ + 投信)"""
    passive = "Passive"          # 投資信託 (Passive Core)
    long_solid = "Long_Solid"    # 長期運用 V6 — コア
    long_growth = "Long_Growth"  # 長期運用 V6 — サテライト
    short = "Short"              # 短期運用 (VWAP)


class ReportType(str, enum.Enum):
    daily = "Daily"
    monthly = "Monthly"
    annual = "Annual"


class FinancialReportPeriod(str, enum.Enum):
    annual = "Annual"
    quarterly = "Quarterly"


class TickSide(str, enum.Enum):
    """歩み値の主導側推定 (Lee-Ready + EMO 拡張アルゴリズムによる)"""
    buy_aggressor = "BUY_AGGR"     # 買い手主導 (price >= ask または midpoint より上)
    sell_aggressor = "SELL_AGGR"   # 売り手主導 (price <= bid または midpoint より下)
    mid = "MID"                    # 中立 (midpoint ぴったり + tick test fallback も中立)


# ===== Tables =====

class User_Settings(Base):
    """
    6.1 ユーザー設定・運用パラメータ
    シングルトン想定 (個人利用前提のため id=1 のみ運用)

    Kill Switch 構造 (Phase 6 / ADR-0007 OQ-4):
    - is_kill_switch_active: 全体停止 (全 bucket の新規エントリーをブロック)
    - is_kill_switch_active_<bucket>: bucket 別停止 (該当 bucket のみブロック)
    - 全体 OR bucket 別 のどちらかが True なら該当 bucket の新規エントリーは止まる (非対称オーバーライド)
    - CLAUDE.md 絶対禁止 3 (キルスイッチ無断解除禁止) は **全フラグに適用**
    """
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    aws_kms_key_id = Column(String, nullable=True)             # SSM/KMS 参照キー
    discord_webhook_url = Column(String, nullable=True)        # 暗号化対象 (実値はSSM経由)
    trading_mode = Column(String, default="Mock")              # 'Live' or 'Mock' (PAPER)
    max_drawdown_limit = Column(Float, default=-0.03)          # ドローダウン閾値 (Kill Switch 発動条件)
    is_kill_switch_active = Column(Boolean, default=False)     # Kill Switch V2 永続化 (全体停止、DB側 + 物理ファイル二重防御)
    # bucket 別キルスイッチ (Phase 6 / ADR-0010 で追加)
    is_kill_switch_active_passive = Column(Boolean, default=False)       # 投資信託 (Passive Core) のみ停止
    is_kill_switch_active_long_solid = Column(Boolean, default=False)    # 長期コア (V6 堅実積立) のみ停止
    is_kill_switch_active_long_growth = Column(Boolean, default=False)   # 長期サテライト (V6 テンバガー狙い) のみ停止
    is_kill_switch_active_short = Column(Boolean, default=False)         # 短期トレード (VWAP) のみ停止


class Asset_Master(Base):
    """
    6.2 銘柄マスタ
    is_active=False で上場廃止・買収済銘柄を論理削除し、Survivor Bias排除を実現
    """
    __tablename__ = "asset_master"

    ticker_symbol = Column(String, primary_key=True, index=True)  # 銘柄コード (例: 7203)
    asset_name = Column(String, nullable=False)
    sector = Column(String, nullable=True)                        # 業種
    category = Column(Enum(AssetCategory), nullable=False)
    is_active = Column(Boolean, default=True)                     # 上場継続フラグ (論理削除用)


class Financial_Reports(Base):
    """
    財務データ (XBRL由来)
    JSONB活用で将来の科目追加に柔軟対応 (SQLite互換のため現状はJSON、PG本番化時にJSONB昇格)
    """
    __tablename__ = "financial_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker_symbol = Column(String, ForeignKey("asset_master.ticker_symbol"), nullable=False, index=True)
    fiscal_year = Column(Integer, nullable=False)                 # 会計年度
    fiscal_period = Column(String, nullable=False)                # Q1/Q2/Q3/Q4/FY
    report_type = Column(Enum(FinancialReportPeriod), nullable=False)

    # 主要数値 (頻繁にクエリされるカラム)
    fcf = Column(BigInteger, nullable=True)                       # Free Cash Flow
    ebitda = Column(BigInteger, nullable=True)
    total_assets = Column(BigInteger, nullable=True)
    shares_outstanding = Column(BigInteger, nullable=True)        # 発行済株式数

    # 拡張データ (将来追加科目)
    additional_data = Column(JSON, nullable=True)

    reported_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_financial_reports_ticker_year", "ticker_symbol", "fiscal_year"),
    )


class Daily_Price_History(Base):
    """
    日次株価・出来高 (Adjusted Price 補正済み)
    株式分割・併合発生時は過去の adjusted_close を再計算してDB上書き
    複合PK: (date, ticker_symbol)
    """
    __tablename__ = "daily_price_history"

    date = Column(Date, primary_key=True)
    ticker_symbol = Column(String, ForeignKey("asset_master.ticker_symbol"), primary_key=True, index=True)
    open = Column(BigInteger, nullable=True)
    high = Column(BigInteger, nullable=True)
    low = Column(BigInteger, nullable=True)
    close = Column(BigInteger, nullable=True)
    volume = Column(BigInteger, nullable=True)
    adjusted_close = Column(BigInteger, nullable=True)            # 株式分割・併合補正後

    __table_args__ = (
        Index("ix_dph_date_ticker", "date", "ticker_symbol"),
    )


class Daily_Asset_Snapshot(Base):
    """
    6.1 日次資産スナップショット (両プール独立管理)
    docs/TECHNICAL_SPECIFICATION.md §6.1 に基づく
    PK: date (1日1レコード)
    """
    __tablename__ = "daily_asset_snapshot"

    date = Column(Date, primary_key=True)
    bank_balance = Column(BigInteger, default=0)                   # 銀行口座残高 (生活防衛費含む) ※読取専用
    buying_power = Column(BigInteger, default=0)                   # 証券口座 現金 (買付余力)、下限なし
    trust_value = Column(BigInteger, default=0)                    # 投資信託 評価額
    long_solid_value = Column(BigInteger, default=0)               # 長期コア 評価額
    long_growth_value = Column(BigInteger, default=0)              # 長期サテライト 評価額
    short_term_capital = Column(BigInteger, default=0)             # 短期枠 元本 (利益再投資分含む)
    short_term_market_value = Column(BigInteger, default=0)        # 短期枠 評価額 (元本との差が含み損益)
    cumulative_sweep_to_long_solid = Column(BigInteger, default=0) # 短期→長期コア 累計振替額


class Trade_Tick_Log(Base):
    """
    6.2 短期トレード イントラデイログ (1〜5分間隔)
    自ポジションの分単位 PnL 推移ログ。市場の歩み値とは別物 (歩み値は Market_Ticks)。
    """
    __tablename__ = "trade_tick_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    ticker_symbol = Column(String, ForeignKey("asset_master.ticker_symbol"), index=True)
    unrealized_pnl = Column(BigInteger, default=0)                 # 含み損益
    realized_pnl = Column(BigInteger, default=0)                   # 実現損益
    regime_type = Column(String, nullable=True)                    # AIによるレジーム判定 (トレンド・デイ等)


class Market_Ticks(Base):
    """
    歩み値 (Time & Sales) の再構築データ — 市場の連続価格・出来高ストリーム

    要件: §B.2 (短期は ms 級レイテンシ要件)
    関連: ADR-0008 (Tick Data Pipeline 設計決定), ADR-0009 (Phase 7 スコープ)

    重要: 歩み値はどの API も直接提供しない。kabu Station Push API (REAL) または
          yfinance 1 分足 (PAPER) から services/market_data.py で **再構築する**。

    構築アルゴリズム (Phase 7 で確定):
    - 集約粒度: 1 秒 bucket (bucket 内の最後の push を採用)
    - side 推定: Lee-Ready + EMO 拡張 (Ellis-Michaely-O'Hara 1996)
        1. price >= ask → BUY_AGGR (買い手主導: ask に当てに行った)
        2. price <= bid → SELL_AGGR (売り手主導: bid に当てに行った)
        3. price > midpoint → BUY_AGGR
        4. price < midpoint → SELL_AGGR
        5. price == midpoint → tick test (前 bucket の price と比較)
        6. quote 欠損時は tick test のみで判定
    - volume delta: 当 bucket の cumulative_volume - 前 bucket の cumulative_volume
        - delta < 0 は異常 (場前リセット除く) → 短期 bucket キルスイッチ発火
        - 場前リセット (09:00 跨ぎ) は特例処理

    PK: (ticker_symbol, timestamp) 複合キー — ticker 先で「銘柄絞り込み + 時系列スキャン」クエリを最適化
    配置: bucket 隔離対象外の共有テーブル (ADR-0007 / Asset_Master 同等扱い)
    """
    __tablename__ = "market_ticks"

    # PK 順序: ticker_symbol 先 / timestamp 後 (イントラデイ分析クエリの局所性最適化)
    ticker_symbol = Column(String, ForeignKey("asset_master.ticker_symbol"), primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True)

    last_price = Column(BigInteger, nullable=False)                 # bucket 終端の last_price (円単位整数)
    cumulative_volume = Column(BigInteger, nullable=False)          # bucket 終端の当日累積出来高
    delta_volume = Column(BigInteger, nullable=False, default=0)    # 当 bucket 内の出来高 (前 bucket との差分)
    bid_price = Column(BigInteger, nullable=True)                   # bucket 終端の bid (push 由来)
    ask_price = Column(BigInteger, nullable=True)                   # bucket 終端の ask (push 由来)
    side_inference = Column(Enum(TickSide), nullable=True)          # LR-EMO 結果 (TickSide enum)
    is_synthetic = Column(Boolean, default=False)                   # gap 補完 / yfinance 由来 = True
    # push_count は ¥ 換算不要のカウント値のため Integer (BigInteger 不要)
    push_count = Column(Integer, default=1)                         # bucket 内 push 数 (流動性指標 / デバッグ用)

    __table_args__ = (
        # PK 自体が (ticker_symbol, timestamp) で時系列スキャンを最適化済のため追加 index は不要だが、
        # 互換性のため一時的に保持 (Phase 8 PG 移行時に PK index に統合予定)
        Index("ix_market_ticks_ticker_ts", "ticker_symbol", "timestamp"),
    )


class Cash_Pool_Status(Base):
    """
    リアルタイム両プール残高
    銀行 (OpenCanvas) + 証券口座 (kabuステーション) を独立管理
    """
    __tablename__ = "cash_pool_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    bank_balance = Column(BigInteger, default=0)                   # OpenCanvas API 由来
    brokerage_cash = Column(BigInteger, default=0)                 # kabuステーション API 由来 = buying_power


class Target_Portfolio(Base):
    """
    動的トップレベル目標比率 (四半期 + VIXギア発火 + 手動 で更新)
    REQUIREMENTS_DEFINITION.md §2.2 ポートフォリオ見直しタイミングを参照
    """
    __tablename__ = "target_portfolio"

    effective_date = Column(Date, primary_key=True)
    cash_target_pct = Column(Float, nullable=False)                # 0.0-1.0
    trust_target_pct = Column(Float, nullable=False)
    stocks_target_pct = Column(Float, nullable=False)
    trigger = Column(String, nullable=True)                        # 'Quarterly' / 'VIX_Defense' / 'VIX_Aggressive' / 'Manual'
    notes = Column(Text, nullable=True)                            # AI判断根拠の自然言語ログ


class Market_Context(Base):
    """
    市況スナップショット (VIX / 為替 / 金利)
    マクロスコアリング・VIXギア判定の入力データ
    """
    __tablename__ = "market_context"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    vix = Column(Float, nullable=True)                             # 米国 VIX 指数
    usd_jpy = Column(Float, nullable=True)                         # ドル円レート
    jp10y_yield = Column(Float, nullable=True)                     # 日本10年国債利回り


class Trade_Logs(Base):
    """
    売買履歴 + AI意思決定理由
    "なぜ買った/売った" を構造化テキストで永続化 (要件 §3 損失理由ドリルダウン)
    """
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    ticker_symbol = Column(String, ForeignKey("asset_master.ticker_symbol"), index=True)
    action = Column(String, nullable=False)                        # 'BUY' / 'SELL' / 'BUY_TO_COVER' / 'SHORT_SELL'
    quantity = Column(Integer, nullable=False)
    price = Column(BigInteger, nullable=False)
    pnl = Column(BigInteger, nullable=True)                        # 決済時のみ
    decision_reason = Column(Text, nullable=True)                  # AI判断理由 (構造化テキスト or JSON文字列)


class System_Logs(Base):
    """
    システムイベント・エラーログ
    python-json-logger と並行して、永続的に DB へも記録 (Discord通知履歴の追跡用)
    """
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    level = Column(String, nullable=False)                         # 'INFO' / 'WARN' / 'ERROR' / 'CRITICAL'
    component = Column(String, nullable=False)                     # '[DB]' / '[API:Kabucom]' / '[KillSwitch]' 等
    event = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)


class Report_Archive(Base):
    """
    6.3 自動生成レポートのメタデータ (PDF/MD実体は file_path 参照)
    """
    __tablename__ = "report_archive"

    report_id = Column(String, primary_key=True, index=True)       # UUID
    report_type = Column(Enum(ReportType), nullable=False)
    target_date = Column(Date, nullable=False)
    file_path = Column(String, nullable=False)                     # /data/reports/...
    ai_summary = Column(Text, nullable=True)                       # AIによる振り返りテキスト
    created_at = Column(DateTime(timezone=True), server_default=func.now())
