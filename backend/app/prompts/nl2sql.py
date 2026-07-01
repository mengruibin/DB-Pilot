"""
NL2SQL Prompt 模板定义。

依据 backend AGENTS.md §提示（Prompt 模板定义）：
  - Prompt 模板使用 Python 字面量（模块级常量），不可含运行时变量
  - 运行时变量通过 build_nl2sql_prompt() 函数注入
  - Prompt 中不含实际数据行（AGENTS.md §数据隐私）
"""

from __future__ import annotations

# =============================================================================
# 系统提示：数据库专家角色定义
# =============================================================================

NL2SQL_SYSTEM_PROMPT = """你是一个专业的数据库 SQL 专家。
你的任务是将用户的自然语言描述转换为正确的 SQL 查询语句。

## 核心规则
1. 只生成 SELECT 查询语句（只读），绝不生成 DDL/DML 写操作语句
2. 生成的 SQL 必须符合目标数据库的 SQL 方言标准
3. 充分利用提供的表结构信息（表名、列名、注释）来理解数据含义
4. 如果用户的问题不明确，优先选择最合理的解释而不是拒绝回答
5. 在 SQL 之外，简要解释查询的逻辑和用途

## 输出格式
你必须按以下格式输出：

```sql
你的 SQL 语句
```

然后在 SQL 代码块之外，用简短的一句话解释该查询做了什么。

## 禁止事项
- 禁止使用 DROP、ALTER、TRUNCATE、CREATE、DELETE、UPDATE、INSERT、GRANT、REVOKE 等写操作
- 禁止生成多条 SQL 语句
- 禁止在注释中包含实际数据行的内容"""

# =============================================================================
# Schema 描述模板
# =============================================================================

SCHEMA_SECTION_TEMPLATE = """## 数据库 Schema

数据库类型：{dialect}

以下是可用的表和列信息："""

TABLE_TEMPLATE = """
### 表：{table_name}
注释：{comment}
列清单：
{columns}"""

COLUMN_TEMPLATE = "  - {name}（{type}）{comment_suffix}"

# =============================================================================
# 用户查询模板
# =============================================================================

USER_QUERY_TEMPLATE = """## 用户问题

请根据上述 Schema 信息，为以下问题生成 SQL 查询：

{user_query}"""


# =============================================================================
# 模板构建函数
# =============================================================================

def build_nl2sql_prompt(
    dialect: str,
    schema_context: dict,
    user_query: str,
) -> tuple[str, str]:
    """构建 NL2SQL 系统提示和用户提示。

    依据 AGENTS.md §数据隐私：Prompt 中不含实际数据行，
    仅包含表/列结构元数据。

    Args:
        dialect: 数据库方言（mysql / postgresql / oracle）。
        schema_context: Schema 上下文（来自 B-10 元数据 API），
            格式：{"databases": [...], "tables": [...]}。
        user_query: 用户的自然语言查询。

    Returns:
        (system_prompt, user_prompt) 二元组。
    """
    # 构建 Schema 描述
    tables = schema_context.get("tables", [])
    schema_lines = [SCHEMA_SECTION_TEMPLATE.format(dialect=dialect)]

    for table in tables:
        cols = table.get("columns", [])
        col_lines = []
        for col in cols:
            comment = col.get("comment", "")
            comment_suffix = f"— {comment}" if comment else ""
            col_lines.append(COLUMN_TEMPLATE.format(
                name=col.get("name", "?"),
                type=col.get("type", "?"),
                comment_suffix=comment_suffix,
            ))
        schema_lines.append(TABLE_TEMPLATE.format(
            table_name=table.get("table_name", "?"),
            comment=table.get("comment", ""),
            columns="\n".join(col_lines),
        ))

    schema_text = "\n".join(schema_lines)

    # SAFETY: AGENTS.md §数据隐私——不将实际数据行注入 Prompt
    # Schema 上下文仅含表结构元数据，无数据行内容
    user_prompt = f"{schema_text}\n\n{USER_QUERY_TEMPLATE.format(user_query=user_query)}"

    return NL2SQL_SYSTEM_PROMPT, user_prompt
