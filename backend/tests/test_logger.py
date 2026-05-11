"""Tests for backend/src/core/logger.py"""
import json
import logging
import os
import sys
from io import StringIO

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core import logger as logger_module  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_logger_state():
    """各テスト前後でロガー初期化フラグとハンドラをリセット (グローバル状態の干渉防止)。"""
    logger_module._INITIALIZED = False
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers.clear()
    yield
    root.handlers.clear()
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    logger_module._INITIALIZED = False


# ===== setup_logger =====
def test_setup_logger_initializes_root_with_stream_handler():
    # シナリオ: 初期化後はルートロガーに StreamHandler が 1 つ付与される
    logger_module.setup_logger(level="INFO", json_output=True)

    root = logging.getLogger()
    assert root.level == logging.INFO
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.StreamHandler)


def test_setup_logger_idempotent():
    # シナリオ: 2回呼んでも handler が増えない (重複初期化防止)
    logger_module.setup_logger(level="INFO")
    logger_module.setup_logger(level="DEBUG")  # 2回目は無視される

    root = logging.getLogger()
    assert len(root.handlers) == 1
    # 1回目で INFO 設定済なので変わらない
    assert root.level == logging.INFO


def test_setup_logger_with_log_file_adds_file_handler(tmp_path):
    # シナリオ: log_file 指定時は StreamHandler + FileHandler の 2 つ
    log_file = tmp_path / "app.log"
    logger_module.setup_logger(level="INFO", log_file=str(log_file))

    root = logging.getLogger()
    assert len(root.handlers) == 2
    assert any(isinstance(h, logging.FileHandler) for h in root.handlers)


def test_setup_logger_text_mode_uses_plain_formatter():
    # シナリオ: json_output=False 時は通常の logging.Formatter
    logger_module.setup_logger(level="INFO", json_output=False)

    root = logging.getLogger()
    formatter = root.handlers[0].formatter
    assert isinstance(formatter, logging.Formatter)
    assert not isinstance(formatter, logger_module._ProjectBigJsonFormatter)


# ===== JSON formatter =====
def test_json_formatter_outputs_normalized_field_names():
    # シナリオ: name → component / asctime → timestamp / levelname → level に正規化
    buffer = StringIO()
    handler = logging.StreamHandler(buffer)
    formatter = logger_module._ProjectBigJsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    handler.setFormatter(formatter)

    test_logger = logging.getLogger("strategy.passive_core")
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False
    test_logger.info("portfolio synced", extra={"event": "sync_done"})

    output = buffer.getvalue().strip()
    parsed = json.loads(output)

    assert parsed["component"] == "strategy.passive_core"
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "portfolio synced"
    assert parsed["event"] == "sync_done"
    assert "timestamp" in parsed
    # 元の name / asctime / levelname フィールドは消えている
    assert "name" not in parsed
    assert "asctime" not in parsed
    assert "levelname" not in parsed


# ===== get_logger =====
def test_get_logger_returns_logger_with_correct_name():
    # シナリオ: get_logger(name) は標準 logging.getLogger と同じ
    log = logger_module.get_logger("services.scheduler")
    assert log.name == "services.scheduler"
    assert isinstance(log, logging.Logger)


# ===== DBLogHandler =====
def test_db_log_handler_writes_to_session(mocker):
    # シナリオ: emit() で session.add() + commit() が呼ばれる
    mock_session = mocker.Mock()
    mock_session_factory = mocker.Mock(return_value=mock_session)

    # System_Logs クラス自体を import 可能にするため、軽量 mock を作る
    fake_system_logs_cls = mocker.Mock()
    fake_models = mocker.Mock(System_Logs=fake_system_logs_cls)
    mocker.patch.dict(sys.modules, {"models.schema": fake_models})

    handler = logger_module.DBLogHandler(mock_session_factory)
    handler.setFormatter(logging.Formatter("%(message)s"))

    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname="", lineno=0,
        msg="warn message", args=(), exc_info=None,
    )
    handler.emit(record)

    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()
    mock_session.close.assert_called_once()


def test_db_log_handler_swallows_exceptions(mocker, capsys):
    # シナリオ: DB 書込失敗してもメイン処理を止めない (stderr に書くだけ)
    mock_session_factory = mocker.Mock(side_effect=Exception("DB connection lost"))

    handler = logger_module.DBLogHandler(mock_session_factory)
    handler.setFormatter(logging.Formatter("%(message)s"))

    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname="", lineno=0,
        msg="msg", args=(), exc_info=None,
    )
    # emit() は例外を投げない
    handler.emit(record)

    captured = capsys.readouterr()
    assert "[Logger] DBLogHandler.emit failed" in captured.err
