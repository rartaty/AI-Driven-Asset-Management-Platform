"""
kabu Station Push API WebSocket クライアント (M10 解消)

要件: §B.2 (短期は ms 級レイテンシ要件), §6 (Fail-safe 自動再接続)
関連: ADR-0008 (Tick Data 設計決定), ADR-0009 (Phase 7 スコープ)

責任:
- kabu Station の WebSocket エンドポイント (ローカル localhost:18080) に接続
- 受信 Push メッセージを services/market_data.on_push() に転送
- 切断時の自動再接続 (exponential backoff)
- TRADE_MODE=PAPER 時は接続せず no-op (PAPER pump は別経路で yfinance を使用)

注意:
- 本クライアントは REAL モード専用
- PAPER モードでは services/market_data.yfinance_to_pseudo_ticks 系を別途呼び出す
- 認証 token は core.secrets.get_secret('KABUCOM_API_PASSWORD') 経由で取得 (ADR-0003 整合)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

import websocket

from core.discord import notify_system
from services import market_data

logger = logging.getLogger(__name__)


# ===== 定数 =====
DEFAULT_WS_URL = os.getenv("KABUCOM_WS_URL", "ws://localhost:18080/kabusapi/websocket")
RECONNECT_BACKOFF_BASE_SEC = 1.0
RECONNECT_BACKOFF_MAX_SEC = 60.0


class KabuPushClient:
    """kabu Station Push API WebSocket クライアント。

    別スレッドで WebSocket 接続を維持し、受信メッセージを market_data.on_push に転送。

    使い方:
        client = KabuPushClient(token_provider=lambda: get_secret("KABUCOM_API_PASSWORD"))
        client.start()                # 別スレッドで接続開始
        ...
        client.stop()                 # 安全に切断
    """

    def __init__(
        self,
        ws_url: str = DEFAULT_WS_URL,
        token_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        self.ws_url = ws_url
        self._token_provider = token_provider
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_attempts = 0

    def start(self) -> bool:
        """WebSocket 接続を別スレッドで開始。

        TRADE_MODE=PAPER の場合は接続せず True を返す (no-op)。
        既に稼働中なら False を返す。
        """
        if os.getenv("TRADE_MODE", "PAPER").upper() == "PAPER":
            logger.info("[KabuPush] PAPER mode: WebSocket connection skipped")
            return True

        if self._thread is not None and self._thread.is_alive():
            logger.warning("[KabuPush] Client already running")
            return False

        self._stop_event.clear()
        self._reconnect_attempts = 0
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="KabuPushClient"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """安全に切断 (再接続停止 + WebSocket close + thread join)。"""
        self._stop_event.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception as e:
                logger.warning(f"[KabuPush] Close warning: {e}")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ===== 内部: 接続ループ =====

    def _run_loop(self) -> None:
        """切断時の自動再接続ループ (exponential backoff)。"""
        while not self._stop_event.is_set():
            try:
                token = self._token_provider() if self._token_provider else ""
                headers = [f"X-API-KEY: {token}"] if token else []
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    header=headers,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                # blocking call until ws closes or stop
                self._ws.run_forever()
            except Exception as e:
                logger.error(f"[KabuPush] run_forever exception: {e}")

            if self._stop_event.is_set():
                break

            # 自動再接続 (exponential backoff)
            self._reconnect_attempts += 1
            backoff = min(
                RECONNECT_BACKOFF_BASE_SEC * (2 ** (self._reconnect_attempts - 1)),
                RECONNECT_BACKOFF_MAX_SEC,
            )
            logger.info(
                f"[KabuPush] Reconnecting in {backoff:.1f}s (attempt {self._reconnect_attempts})"
            )
            self._stop_event.wait(timeout=backoff)

    # ===== 内部: WebSocket イベントハンドラ =====

    def _on_open(self, ws) -> None:
        logger.info(f"[KabuPush] Connected to {self.ws_url}")
        notify_system("Kabu Push WebSocket connected", component="KabuPush")
        self._reconnect_attempts = 0

    def _on_message(self, ws, message: str) -> None:
        try:
            payload = json.loads(message)
            push = self._parse_push(payload)
            if push is None:
                return
            ticker = push.pop("ticker_symbol")
            market_data.on_push(ticker, push)
        except json.JSONDecodeError as e:
            logger.error(f"[KabuPush] JSON parse error: {e}, raw={message[:200]}")
        except Exception as e:
            logger.error(f"[KabuPush] on_message exception: {e}")

    def _on_error(self, ws, error) -> None:
        logger.error(f"[KabuPush] WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        logger.warning(
            f"[KabuPush] Disconnected: code={close_status_code}, msg={close_msg}"
        )
        if not self._stop_event.is_set():
            notify_system(
                f"Kabu Push WebSocket disconnected (code={close_status_code})",
                component="KabuPush",
            )

    @staticmethod
    def _parse_push(payload: dict) -> Optional[dict]:
        """kabu Push の JSON を market_data.on_push() に渡す形式に変換。

        kabu Push のメッセージは複数のフィールドを含むが、最低限以下の 3 つが揃えば tick として扱う:
        - Symbol (銘柄コード)
        - CurrentPrice (現在値)
        - TradingVolume (当日累積出来高)

        BidPrice / AskPrice は欠損可 (LR-EMO の tick test fallback で吸収)。
        """
        ticker = payload.get("Symbol")
        price = payload.get("CurrentPrice")
        volume = payload.get("TradingVolume")
        if ticker is None or price is None or volume is None:
            return None

        return {
            "ticker_symbol": str(ticker),
            "timestamp": datetime.now(timezone.utc),
            "last_price": int(price),
            "cumulative_volume": int(volume),
            "bid_price": int(payload["BidPrice"]) if payload.get("BidPrice") else None,
            "ask_price": int(payload["AskPrice"]) if payload.get("AskPrice") else None,
            "is_synthetic": False,
        }


# ===== グローバルシングルトン =====

_client: Optional[KabuPushClient] = None


def get_client() -> KabuPushClient:
    global _client
    if _client is None:
        _client = KabuPushClient()
    return _client


def reset_client() -> None:
    """テスト用シングルトンリセット。"""
    global _client
    if _client is not None:
        _client.stop()
    _client = None
