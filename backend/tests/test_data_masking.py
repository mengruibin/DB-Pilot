"""
数据脱敏助手单元测试（query-result-export-plan）。

覆盖：
  - 敏感列（password/phone 等）值掩码为 ***
  - 非敏感列原样保留
  - rows=None 时仅返回 flags
  - 列名大小写不敏感匹配
"""

from __future__ import annotations

from app.engine.data_masking import mask_query_result


class TestMaskQueryResult:
    def test_masks_sensitive_columns(self) -> None:
        columns = ["id", "password", "email"]
        rows = [[1, "secret123", "a@b.com"], [2, "p@ss", "c@d.com"]]
        flags, masked = mask_query_result(columns, rows)
        assert flags == [False, True, True]
        assert masked == [[1, "***", "***"], [2, "***", "***"]]

    def test_keeps_non_sensitive(self) -> None:
        columns = ["id", "username", "created_at"]
        rows = [[1, "alice", "2026-01-01"]]
        _, masked = mask_query_result(columns, rows)
        assert masked == [[1, "alice", "2026-01-01"]]

    def test_rows_none_returns_flags_only(self) -> None:
        flags, masked = mask_query_result(["name", "phone"], None)
        assert flags == [False, True]
        assert masked is None

    def test_case_insensitive(self) -> None:
        flags, _ = mask_query_result(["Password", "Token_ID"], None)
        assert flags == [True, True]
