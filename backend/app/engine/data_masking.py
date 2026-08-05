"""
数据脱敏助手（query-result-export-plan）。

敏感列掩码逻辑从 app/agent/tools/query.py::_run_sql 抽出，供工具路径与
导出端点共用同一份脱敏规则，避免导出成为绕过脱敏的路径。
"""

from __future__ import annotations

from typing import Any

# 敏感列名匹配模式（与 query.py 原实现一致，小写子串匹配）
SENSITIVE_COLUMN_PATTERNS: list[str] = [
    "password",
    "token",
    "secret",
    "key",
    "auth",
    "credit",
    "card",
    "ssn",
    "id_card",
    "phone",
    "email",
    "cert",
]

# 掩码值（与前端 useSensitiveData.ts 的 MASK_VALUE 一致）
MASK_VALUE = "***"


def mask_query_result(
    columns: list[str],
    rows: list[list[Any]] | None,
) -> tuple[list[bool], list[list[Any]] | None]:
    """对查询结果集执行敏感列掩码。

    Args:
        columns: 结果集列名列表。
        rows: 结果集行列表；为 None 时仅计算掩码标记、不掩码。

    Returns:
        (masked_column_flags, masked_rows)。
        masked_column_flags[i] 表示第 i 列是否敏感；
        masked_rows 为 None 时表示未掩码（调用方只需 flags）。
    """
    flags = [
        any(p in col.lower() for p in SENSITIVE_COLUMN_PATTERNS)
        for col in columns
    ]
    if rows is None:
        return flags, None
    masked = [
        [MASK_VALUE if flags[i] else row[i] for i in range(len(row))]
        for row in rows
    ]
    return flags, masked
