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

# =============================================================================
# 系统提示模板（角色感知）
# =============================================================================
# NL2SQL 提示词根据用户角色分为两个版本：
#   - readonly：仅允许 SELECT 只读查询，拒绝一切 DML/DDL
#   - admin：允许 DML（INSERT/UPDATE/DELETE），仍拒绝 DDL（DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE）
# 运行时由 build_nl2sql_prompt() 根据 user_role 参数组装对应的系统提示。

# ---------------------------------------------------------------------------
# 通用部分（两个角色共用）
# ---------------------------------------------------------------------------

_NL2SQL_COMMON_RULES = """你是一个专业的数据库 SQL 专家。
你的任务是将用户的自然语言描述转换为正确的 SQL 查询语句。

## 通用规则
1. 生成的 SQL 必须符合目标数据库的 SQL 方言标准
2. 充分利用提供的表结构信息（表名、列名、注释）来理解数据含义
3. 如果用户的问题不明确，优先选择最合理的解释而不是拒绝回答
4. 在 SQL 之外，简要解释查询的逻辑和用途

## 性能优化规则
5. 优先使用有索引的列作为 WHERE 条件和 JOIN 条件，充分利用已有索引
6. 避免使用 SELECT *，明确列出实际需要的列名
7. 对大表查询推荐添加合理的 LIMIT 限制返回行数
8. 避免在 WHERE 子句中对列使用函数（如 WHERE YEAR(create_time)=2024），这会阻止索引使用
9. LIKE '%keyword'（前置通配符）会导致全表扫描，仅在必要时使用
10. 涉及多表 JOIN 时，注意 JOIN 顺序和驱动表选择，小表驱动大表"""

# ---------------------------------------------------------------------------
# 角色专属部分
# ---------------------------------------------------------------------------

# readonly 角色（默认）：只允许 SELECT 只读查询
_NL2SQL_READONLY_RULES = """
## 查询范围限制（重要）
- **你只能生成 SELECT 只读查询语句**，绝不生成任何写操作语句
- 用户的写操作需求（增删改）应礼貌告知需要管理员权限
- 禁止生成 DDL 语句（DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE）
- 禁止生成 DML 写操作语句（INSERT/UPDATE/DELETE/MERGE）

## 禁止事项
- 禁止使用 DROP、ALTER、TRUNCATE、CREATE、DELETE、UPDATE、INSERT、GRANT、REVOKE、MERGE 等写操作
- 禁止生成多条 SQL 语句
- 禁止在注释中包含实际数据行的内容
- 禁止查询 information_schema、pg_catalog、mysql 等系统数据库/系统表
- 禁止查询 sys、performance_schema 等元数据视图"""

# admin 角色：允许 DML 写操作，仍拒绝 DDL
_NL2SQL_ADMIN_RULES = """
## 查询范围限制（重要）
- 你可以生成 **SELECT 只读查询**和 **DML 写操作语句**（INSERT/UPDATE/DELETE/MERGE）
- 在生成写操作 SQL 前，确保理解操作的业务含义和影响范围
- 生成 DELETE/UPDATE 时，务必包含精确的 WHERE 条件，避免误删误改全表数据
- **仍禁止生成 DDL 语句**（DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE），这些操作不在你的权限范围内

## 禁止事项
- 禁止使用 DROP、ALTER、TRUNCATE、CREATE、GRANT、REVOKE 等 DDL 操作
- 禁止生成多条 SQL 语句
- 禁止在注释中包含实际数据行的内容
- 禁止查询 information_schema、pg_catalog、mysql 等系统数据库/系统表
- 禁止查询 sys、performance_schema 等元数据视图"""

# ---------------------------------------------------------------------------
# 输出格式（两个角色共用）
# ---------------------------------------------------------------------------

_NL2SQL_OUTPUT_FORMAT = """## 输出格式
你必须按以下格式输出：

```sql
你的 SQL 语句
```

然后在 SQL 代码块之外，用简短的一句话解释该查询做了什么。

## 重要提示
- 上述已提供完整的表名、列名和注释信息，请直接使用这些信息构建 SQL
- 不要试图从 information_schema 等系统表中获取元数据——这些信息已提供
- 不要生成描述表结构的 SQL（如 SHOW TABLES），直接使用已提供的 Schema"""


# ---------------------------------------------------------------------------
# 最终系统提示组装函数
# ---------------------------------------------------------------------------

def _build_system_prompt(user_role: str = "readonly") -> str:
    """根据用户角色组装对应的 NL2SQL 系统提示词。

    Args:
        user_role: 用户角色（readonly / admin）。

    Returns:
        组装后的完整系统提示词字符串。
    """
    # 角色专属规则
    role_rules = (
        _NL2SQL_ADMIN_RULES
        if user_role == "admin"
        else _NL2SQL_READONLY_RULES
    )
    return _NL2SQL_COMMON_RULES + role_rules + _NL2SQL_OUTPUT_FORMAT


# 向后兼容：保留 NL2SQL_SYSTEM_PROMPT 常量（readonly 默认提示）
NL2SQL_SYSTEM_PROMPT = _build_system_prompt("readonly")

# =============================================================================
# Schema 描述模板
# =============================================================================

SCHEMA_SECTION_TEMPLATE = """## 数据库 Schema

数据库类型：{dialect}
数据库名称：{database}

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
    conversation_history: str | None = None,
    previous_error: str | None = None,
    previous_sql: str | None = None,
    user_role: str = "readonly",
) -> tuple[str, str]:
    """构建 NL2SQL 系统提示和用户提示。

    依据 AGENTS.md §数据隐私：Prompt 中不含实际数据行，
    仅包含表/列结构元数据。

    Args:
        dialect: 数据库方言（mysql / postgresql / oracle）。
        schema_context: Schema 上下文（来自 B-10 元数据 API），
            格式：{"databases": [...], "tables": [...]}。
        user_query: 用户的自然语言查询。
        conversation_history: 可选的会话历史文本，注入到用户提示
            尾部用于上下文理解。
        previous_error: 上一次执行的错误信息，用于自动修正 SQL。
        previous_sql: 上一次生成的错误 SQL。
        user_role: 用户角色（readonly / admin）。
            readonly 用户仅允许 SELECT 只读查询；
            admin 用户允许 DML 写操作（INSERT/UPDATE/DELETE），
            但仍禁止 DDL（DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE）。

    Returns:
        (system_prompt, user_prompt) 二元组。
    """
    # 构建 Schema 描述
    tables = schema_context.get("tables", [])
    database = schema_context.get("database", "（未知）")
    schema_lines = [SCHEMA_SECTION_TEMPLATE.format(
        dialect=dialect,
        database=database,
    )]

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

    # 会话记忆：若有历史上下文，追加到用户提示中（帮助 LLM 理解指代和省略）
    if conversation_history:
        user_prompt += (
            f"\n\n## 对话历史（用于上下文参考）\n"
            f"{conversation_history}\n\n"
            f"请结合对话历史理解上述用户问题的完整语义，并生成对应的 SQL。"
        )

    # SQL 错误修正：若提供了前次错误信息，追加修正提示
    if previous_error and previous_sql:
        user_prompt += (
            f"\n\n## SQL 错误修正（重要）\n"
            f"你之前生成了以下 SQL，但执行时出错：\n"
            f"```sql\n{previous_sql}\n```\n\n"
            f"错误信息：{previous_error}\n\n"
            f"请根据错误信息修正 SQL。确保修正后的 SQL 是有效的。\n"
            f"修正原则：\n"
            f"- 只修正语法/字段错误，不改变查询的业务逻辑\n"
            f"- 如果错误是字段不存在，检查字段名是否拼写正确或属于哪个表\n"
            f"- 绝对不要使用 information_schema 等系统表"
        )

    return _build_system_prompt(user_role), user_prompt
