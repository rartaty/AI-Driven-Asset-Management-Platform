"""
Market Context Service
市場環境（VIX指数など）の取得と、VIXギア発火判定を行います。
"""
import yfinance as yf
import logging
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import cast, Date
from models.schema import Market_Context
from core.discord import notify_system, NotifyLevel
from api.kabucom import KabucomAPIClient

logger = logging.getLogger(__name__)

VIX_DEFEND_THRESHOLD = 20.0
VIX_ATTACK_THRESHOLD = 35.0

def fetch_vix() -> float:
    """kabuStation API を優先し、失敗時に yfinance から現在のVIX指数を取得する"""
    vix = 0.0
    
    # 1. KabuStation API を試行
    try:
        client = KabucomAPIClient()
        # KabuStation上のVIXや日経VI等のシンボルを想定（ここでは仮に "VIX" または対応コード）
        vix = client.get_index("VIX")
    except Exception as e:
        logger.warning(f"[MarketContext] kabuStation failed to fetch VIX: {e}")
        
    # 2. yfinance にフォールバック
    if vix <= 0:
        logger.info("[MarketContext] Falling back to yfinance for VIX.")
        try:
            ticker = yf.Ticker("^VIX")
            hist = ticker.history(period="1d")
            if not hist.empty:
                vix = float(hist['Close'].iloc[-1])
        except Exception as e:
            logger.error(f"[MarketContext] Failed to fetch VIX from yfinance: {e}")
            
    return vix if vix > 0 else 0.0

def evaluate_vix_gear(vix_value: float) -> str:
    """VIX値に基づいて現在のモードを判定する"""
    if vix_value <= 0:
        return "NEUTRAL" # 取得エラー時など
        
    if vix_value <= VIX_DEFEND_THRESHOLD:
        return "DEFEND"
    elif vix_value >= VIX_ATTACK_THRESHOLD:
        return "ATTACK"
    else:
        return "NEUTRAL"

def update_market_context(session: Session) -> Market_Context:
    """
    最新の相場環境を取得し DB を更新する (要件 §2.2)。

    VIXギア発火時 (DEFEND/ATTACK) は Target_Portfolio も自動更新する (P9)。
    NEUTRAL モードは Target_Portfolio を更新せず、四半期見直しでベースラインへ復帰させる設計。
    """
    vix = fetch_vix()
    mode = evaluate_vix_gear(vix)

    today = date.today()
    context = session.query(Market_Context).filter(cast(Market_Context.timestamp, Date) == today).first()

    if not context:
        context = Market_Context(vix=vix, usd_jpy=0.0)
        session.add(context)
    else:
        context.vix = vix

    session.commit()
    session.refresh(context)

    logger.info(f"[MarketContext] Updated VIX={vix:.2f}, Mode={mode}")

    # P9: VIXギア発火時は Target_Portfolio を更新 (要件 §2.2 (b) 即時トリガー)
    from services.target_portfolio import write_for_vix_gear
    target = write_for_vix_gear(session, mode, vix)
    if target is not None:
        msg = (
            f"VIX gear fired ({mode}, VIX={vix:.2f}). "
            f"New target: cash={target.cash_target_pct:.2f}, "
            f"trust={target.trust_target_pct:.2f}, "
            f"stocks={target.stocks_target_pct:.2f}"
        )
        logger.info(f"[MarketContext] {msg}")
        notify_system(msg, component="MarketContext")

    return context
