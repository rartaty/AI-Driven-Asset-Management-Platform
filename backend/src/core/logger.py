"""
構造化ロガー設定 (python-json-logger)

参照仕様:
- docs/REQUIREMENTS_DEFINITION.md §E.7 (不正追跡・監視)
- docs/infrastructure/DATA_FOUNDATION.md §7.1
- docs/audit/2026-05-03-pre-phase4.md M8 (System_Logs DB 書込未実装)

設計方針:
- 全ログを JSON 形式で stdout へ出力 (CloudWatch Logs / Loki 等で集約しやすい)
- 共通フィールド: timestamp / level / component (= logger name) / message / 任意 extra
- DB 書込は **オプトイン** の `DBLogHandler` を別途有効化することで対応
  (System_Logs テーブルへの非同期書込は呼び出し側の責任)

使用例:
    # main.py 起動時
    setup_logger(level="INFO", json_output=True)

    # 各モジュール内
    log = get_logger(__name__)
    log.info("portfolio synced", extra={"event": "portfolio_sync", "duration_ms": 123})
"""

import logging
import sys
from typing import Optional

from pythonjsonlogger.json import JsonFormatter


_INITIALIZED = False


# ===== JSON Formatter (component / event 共通フィールド付与) =====
class _ProjectBigJsonFormatter(JsonFormatter):
    """JSON Formatter: timestamp/level/component を整形。"""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        # 共通フィールド名の正規化
        if "asctime" in log_record:
            log_record["timestamp"] = log_record.pop("asctime")
        if "levelname" in log_record:
            log_record["level"] = log_record.pop("levelname")
        if "name" in log_record:
            log_record["component"] = log_record.pop("name")


def setup_logger(
    level: str = "INFO",
    json_output: bool = True,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    アプリケーション全体のロガーを初期化する。
    `main.py` 起動時に1回だけ呼び出す想定。

    :param level: ログレベル (DEBUG / INFO / WARNING / ERROR / CRITICAL)
    :param json_output: True なら JSON 形式、False なら人間可読のテキスト
    :param log_file: ファイルにも出力する場合のパス。None なら stdout のみ
    :return: ルートロガー
    """
    global _INITIALIZED
    if _INITIALIZED:
        return logging.getLogger()

    # フォーマッター選択
    if json_output:
        formatter = _ProjectBigJsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    else:
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        )

    # 標準出力ハンドラ
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    # ルートロガー初期化
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.setLevel(level.upper())

    # ファイルハンドラ (任意)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _INITIALIZED = True
    return root


def get_logger(name: str) -> logging.Logger:
    """
    モジュール固有のロガーを取得する。

    :param name: モジュール名 (通常 `__name__` を渡す)
    :return: ロガーインスタンス
    """
    return logging.getLogger(name)


# ===== Optional: DB Log Handler (System_Logs テーブルへの書込) =====
# 監査 M8 対応: 必要時のみ setup_logger 後に `enable_db_logging()` で有効化。
# DB 書込で例外が発生してもメイン処理を止めないよう、handler 内で例外捕捉する。

class DBLogHandler(logging.Handler):
    """
    System_Logs テーブルへの logging handler。
    使用前に SQLAlchemy session_factory を渡して enable_db_logging() で有効化する。
    """

    def __init__(self, session_factory):
        super().__init__()
        self._session_factory = session_factory

    def emit(self, record: logging.LogRecord) -> None:
        """非ブロッキング: 失敗してもメイン処理を止めない (handle 内で握りつぶし、別 logger でメモ)。"""
        try:
            # 遅延 import (循環参照回避)
            from models.schema import System_Logs

            session = self._session_factory()
            try:
                entry = System_Logs(
                    level=record.levelname,
                    component=record.name,
                    message=self.format(record),
                )
                session.add(entry)
                session.commit()
            finally:
                session.close()
        except Exception as e:
            # ロガー自身の失敗は別経路で。stderr に直接書く (logger 経由だと無限ループ)
            sys.stderr.write(f"[Logger] DBLogHandler.emit failed: {e}\n")


def enable_db_logging(session_factory, level: str = "WARNING") -> None:
    """
    DB ログ出力を有効化する。setup_logger 完了後に呼ぶ。
    全レベルを DB 書込すると性能問題になるため、デフォルトは WARNING 以上のみ。

    :param session_factory: SQLAlchemy sessionmaker (例: SessionLocal)
    :param level: DB に書き込む最低レベル (DEBUG / INFO / WARNING / ERROR / CRITICAL)
    """
    handler = DBLogHandler(session_factory)
    handler.setLevel(level.upper())
    logging.getLogger().addHandler(handler)
