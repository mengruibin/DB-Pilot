"""
导出端点单元测试（mock 适配器与审计，不连真实数据库）。

覆盖：
  - 只读校验：写 SQL 返回 400
  - 非 MySQL 连接返回 400
  - 正常导出流式返回 CSV，且最终断开连接
  - max_rows 生效：超过即截断并标注
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///test.db")
os.environ.setdefault(
    "JWT_SECRET", "test-secret-test-secret-test-secret-32chars!"
)

from app.api.query import export_query_result  # noqa: E402
from app.engine.sql_auditor import AuditResult  # noqa: E402
from app.models.schemas import ExportRequest  # noqa: E402


def _make_user(role: str = "readonly"):
    u = MagicMock()
    u.role = role
    u.id = "u1"
    return u


def _make_config(db_type: str = "mysql"):
    cfg = MagicMock()
    cfg.db_type = db_type
    return cfg


class _FakeAdapter:
    """最小假适配器：三行数据两批产出。"""

    def __init__(self) -> None:
        self.disconnect_called = False

    async def connect(self, config) -> None:  # noqa: D102
        pass

    async def disconnect(self) -> None:
        self.disconnect_called = True

    async def explain(self, sql: str) -> dict:
        return {"explain_output": "{}", "format": "json"}

    async def stream_query(self, sql, params=None, batch_size=2000):
        cols = ["id", "name"]
        yield cols, [[1, "alice"], [2, "bob"]]
        yield cols, [[3, "carol"]]


async def _consume(resp) -> str:
    """消费 StreamingResponse 的全部内容。"""
    parts = []
    async for chunk in resp.body_iterator:
        parts.append(chunk)
    return b"".join(parts).decode("utf-8")


class TestExportEndpoint:
    @pytest.mark.asyncio
    async def test_export_streams_csv_and_disconnects(self) -> None:
        adapter = _FakeAdapter()
        with patch("app.api.query._load_and_create_adapter", new_callable=AsyncMock) as m:
            m.return_value = (adapter, _make_config())
            with patch("app.api.query.audit") as m_audit:
                m_audit.return_value = AuditResult(passed=True, is_readonly=True)
                resp = await export_query_result(
                    "conn1",
                    ExportRequest(sql="SELECT * FROM t"),
                    session=MagicMock(),
                    current_user=_make_user(),
                )
        text = await _consume(resp)
        assert "id,name" in text
        assert "alice" in text and "carol" in text
        assert adapter.disconnect_called is True

    @pytest.mark.asyncio
    async def test_export_respects_max_rows(self) -> None:
        adapter = _FakeAdapter()
        with patch("app.api.query._load_and_create_adapter", new_callable=AsyncMock) as m:
            m.return_value = (adapter, _make_config())
            with patch("app.api.query.audit") as m_audit:
                m_audit.return_value = AuditResult(passed=True, is_readonly=True)
                resp = await export_query_result(
                    "conn1",
                    ExportRequest(sql="SELECT * FROM t", max_rows=2),
                    session=MagicMock(),
                    current_user=_make_user(),
                )
        text = await _consume(resp)
        assert "3,carol" not in text
        assert "结果已截断至 2 行（导出上限）" in text

    @pytest.mark.asyncio
    async def test_rejects_write_sql(self) -> None:
        adapter = _FakeAdapter()
        with patch("app.api.query._load_and_create_adapter", new_callable=AsyncMock) as m:
            m.return_value = (adapter, _make_config())
            with patch("app.api.query.audit") as m_audit:
                m_audit.return_value = AuditResult(passed=True, is_readonly=False)
                with pytest.raises(HTTPException) as exc_info:
                    await export_query_result(
                        "conn1",
                        ExportRequest(sql="DELETE FROM t"),
                        session=MagicMock(),
                        current_user=_make_user(),
                    )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_non_mysql(self) -> None:
        adapter = _FakeAdapter()
        with patch("app.api.query._load_and_create_adapter", new_callable=AsyncMock) as m:
            m.return_value = (adapter, _make_config(db_type="postgresql"))
            with patch("app.api.query.audit") as m_audit:
                m_audit.return_value = AuditResult(passed=True, is_readonly=True)
                with pytest.raises(HTTPException) as exc_info:
                    await export_query_result(
                        "conn1",
                        ExportRequest(sql="SELECT * FROM t"),
                        session=MagicMock(),
                        current_user=_make_user(),
                    )
        assert exc_info.value.status_code == 400
        assert "暂不支持" in exc_info.value.detail["user_message"]
