"""
Pydantic 请求/响应 Schema 定义。

依据 api-contract §2.1 ConnectionConfig 实体定义。
与 ORM 模型分离：Schema 层控制序列化行为（如排除 password 字段）。
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# =============================================================================
# 数据库类型枚举
# =============================================================================


class DBType(StrEnum):
    """支持的数据库类型（api-contract §2.1）。"""

    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    ORACLE = "oracle"


# =============================================================================
# 连接状态枚举
# =============================================================================


class ConnectionStatus(StrEnum):
    """连接状态枚举（api-contract §2.1）。"""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNREACHABLE = "unreachable"
    DEGRADED = "degraded"


# =============================================================================
# 端口默认值映射
# =============================================================================

DB_DEFAULT_PORTS: dict[str, int] = {
    "mysql": 3306,
    "postgresql": 5432,
    "oracle": 1521,
}


# =============================================================================
# 请求 Schema
# =============================================================================


class ConnectionCreateRequest(BaseModel):
    """创建连接请求体。

    依据 api-contract §2.1：
    - db_type 枚举 mysql/postgresql/oracle
    - name 1-64 字符
    - port 1-65535，未传则按 db_type 填默认值
    - password 仅在请求中出现，不入库
    - ssl_ca_cert 可选
    - extra_params 可选
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="连接名称，1-64 字符",
        examples=["生产 MySQL"],
    )
    db_type: DBType = Field(
        ...,
        description="数据库类型：mysql / postgresql / oracle",
        examples=["mysql"],
    )
    host: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="数据库主机地址（IP 或域名）",
        examples=["10.0.1.100"],
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="数据库端口号，未传则按 db_type 填默认值（3306/5432/1521）",
        examples=[3306],
    )
    database: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="目标数据库名",
        examples=["orders"],
    )
    user: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="连接用户名",
        examples=["readonly_user"],
    )
    password: str | None = Field(
        default=None,
        description="连接密码。仅在创建/更新时传入，所有响应中不包含此字段。"
        "依据 AGENTS.md §安全与合规红线：密码不落盘。",
        examples=["s3cret"],
    )
    ssl_enabled: bool = Field(
        default=False,
        description="是否启用 SSL/TLS 加密连接",
    )
    ssl_ca_cert: str | None = Field(
        default=None,
        description="SSL CA 证书 PEM 格式内容（可选）",
    )
    extra_params: dict[str, Any] | None = Field(
        default=None,
        description="额外连接参数（JSON 对象），如 charset、connect_timeout 等",
        examples=[{"charset": "utf8mb4"}],
    )

    @field_validator("db_type", mode="before")
    @classmethod
    def _validate_db_type(cls, v: Any) -> str:
        """校验 db_type 并返回标准化字符串。"""
        if isinstance(v, DBType):
            return v.value
        if isinstance(v, str) and v.lower() in {"mysql", "postgresql", "oracle"}:
            return v.lower()
        raise ValueError(f"不支持的数据库类型：{v!r}，仅支持 mysql / postgresql / oracle")

    @model_validator(mode="after")
    def _fill_default_port(self) -> ConnectionCreateRequest:
        """若 port 未传，根据 db_type 填写默认端口。"""
        if self.port is None:
            self.port = DB_DEFAULT_PORTS.get(str(self.db_type), 3306)
        return self


class ConnectionUpdateRequest(BaseModel):
    """更新连接请求体（所有字段可选，仅更新传入的字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    db_type: DBType | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=128)
    user: str | None = Field(default=None, min_length=1, max_length=128)
    password: str | None = Field(default=None)
    ssl_enabled: bool | None = None
    ssl_ca_cert: str | None = None
    extra_params: dict[str, Any] | None = None


class ResumeRequest(BaseModel):
    """LangGraph 中断恢复请求体。

    用户对写操作确认/拒绝后，前端通过此结构恢复已中断的图执行。
    """

    approved_tool_call_ids: list[str] = Field(
        default_factory=list,
        description="用户批准的 tool_call_id 列表",
    )
    denied_tool_call_ids: list[str] = Field(
        default_factory=list,
        description="用户拒绝的 tool_call_id 列表",
    )


class ChatRequest(BaseModel):
    """SSE 对话流请求体（api-contract §1.2 POST /api/chat/stream）。"""

    connection_id: str = Field(
        ...,
        description="目标数据库连接 ID",
    )
    message: str = Field(
        default="",
        max_length=4096,
        description="用户消息。恢复请求时可为空",
        examples=["最近一小时慢查询有哪些？"],
    )
    session_id: str | None = Field(
        default=None,
        description="会话 ID。null=新会话，非空=续接已有会话",
    )
    password: str | None = Field(
        default=None,
        description="连接密码。由浏览器 localStorage 持有，每次请求传入（AGENTS.md §8.2）。"
        "密码仅存于内存，不落盘。",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="前端附加上下文，如 {selected_table, user_role}",
    )
    resume: ResumeRequest | None = Field(
        default=None,
        description="中断恢复请求。非空时表示恢复已中断的图执行，message 可为空",
    )


# =============================================================================
# 响应 Schema
# =============================================================================


class ConnectionResponse(BaseModel):
    """连接响应体。

    依据 api-contract §1.1：响应中不包含 password 字段。
    依据 AGENTS.md §安全与合规红线：密码不出现在任何连接列表/详情响应中。
    """

    id: str = Field(..., description="连接唯一标识")
    name: str = Field(..., description="连接名称")
    db_type: str = Field(..., description="数据库类型")
    host: str = Field(..., description="主机地址")
    port: int = Field(..., description="端口号")
    database: str = Field(..., description="目标数据库名")
    user: str = Field(..., description="连接用户名")
    # 注意：没有 password 字段——SAFETY: 密码不落盘也不在响应中返回
    ssl_enabled: bool = Field(..., description="是否启用 SSL")
    ssl_ca_cert: str | None = Field(default=None, description="SSL CA 证书")
    extra_params: dict[str, Any] | None = Field(default=None, description="额外参数")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    last_tested_at: datetime | None = Field(default=None, description="最后测试时间")
    status: str = Field(..., description="连接状态")
    user_id: str | None = Field(default=None, description="所属用户 ID（null = 历史遗留数据）")

    model_config = {
        "from_attributes": True,  # 支持从 ORM 模型自动转换
    }


class ConnectionListResponse(BaseModel):
    """分页连接列表响应（api-contract §1.1）。

    注意：序列化时 page_size → pageSize（camelCase 约定），
    Python 代码统一使用 snake_case。
    """

    items: list[ConnectionResponse] = Field(..., description="连接列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(
        default=20,
        serialization_alias="pageSize",
        description="每页条数",
    )


# =============================================================================
# 会话 & 消息 Schema（api-contract §2.2）
# =============================================================================


class SessionResponse(BaseModel):
    """会话响应体，对应 api-contract §2.2 Session 实体。"""

    id: str = Field(..., description="会话唯一标识")
    connection_id: str | None = Field(default=None, description="关联的连接 ID")
    title: str = Field(..., description="会话标题")
    created_at: datetime = Field(..., description="创建时间")
    last_active_at: datetime = Field(..., description="最后活跃时间")
    status: str = Field(..., description="会话状态：active / idle / closed")
    message_count: int = Field(..., description="消息总数")
    tokens_used_total: int = Field(..., description="累计 token 消耗")
    user_id: str | None = Field(default=None, description="所属用户 ID（null = 历史遗留数据）")

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    """消息响应体，对应 api-contract §2.2 Message 实体。"""

    id: str = Field(..., description="消息唯一标识")
    session_id: str = Field(..., description="所属会话 ID")
    role: str = Field(..., description="消息角色：user / assistant / system")
    content: str = Field(..., description="消息内容")
    message_type: str = Field(
        ...,
        description="消息类型：natural_language / sql / diagnosis / troubleshoot / health_check",
    )
    sql_generated: str | None = Field(default=None, description="LLM 生成的原始 SQL")
    sql_executed: str | None = Field(default=None, description="实际执行的 SQL")
    result_preview: dict[str, Any] | None = Field(
        default=None,
        description="查询结果预览（最多 20 行）",
    )
    error_info: dict[str, Any] | None = Field(
        default=None,
        description="错误信息",
    )
    created_at: datetime = Field(..., description="创建时间")
    tokens_used: int = Field(..., description="本条消息 token 消耗")
    reasoning_content: str | None = Field(
        default=None,
        description="LLM 推理/思考过程全文",
    )
    thinking_steps: list[dict[str, Any]] | None = Field(
        default=None,
        description="思考步骤（tool_call, tool_result, sql）",
    )

    model_config = {"from_attributes": True}

    @field_validator("thinking_steps", mode="before")
    @classmethod
    def parse_thinking_steps(cls, v: Any) -> Any:
        """将数据库中的 JSON 字符串解析为 Python 列表。"""
        if isinstance(v, str):
            return json.loads(v) if v else None
        return v


class SessionRenameRequest(BaseModel):
    """重命名会话请求体（B1）。"""

    title: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="新的会话标题，1-128 字符",
        examples=["分析 orders 表性能"],
    )


class SessionListResponse(BaseModel):
    """分页会话列表响应。"""

    items: list[SessionResponse] = Field(..., description="会话列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(
        default=20,
        serialization_alias="pageSize",
        description="每页条数",
    )


class MessageListResponse(BaseModel):
    """分页消息列表响应。"""

    items: list[MessageResponse] = Field(..., description="消息列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(
        default=50,
        serialization_alias="pageSize",
        description="每页条数",
    )


# =============================================================================
# 查询 & 诊断 Schema（api-contract §1.3）
# =============================================================================


class QueryRequest(BaseModel):
    """直接查询请求体（api-contract §1.3 POST /api/connections/{id}/query）。

    接收 SQL 语句、参数化值和可选执行超时。
    密码由前端 localStorage 持有，不在服务端持久化。
    """

    sql: str = Field(
        ...,
        min_length=1,
        max_length=65535,
        description="要执行的 SQL 语句（只读查询）",
        examples=["SELECT COUNT(*) FROM users"],
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="参数化查询值，键为参数名，值为参数值",
    )
    max_execution_ms: int = Field(
        default=30000,
        ge=1000,
        le=120000,
        description="最大执行时间（毫秒），默认 30s",
    )
    password: str | None = Field(
        default=None,
        description="连接密码。由前端 localStorage 持有，每次请求传入（AGENTS.md §8.2）。"
        "密码仅存于内存，不落盘。",
    )


class ExportRequest(BaseModel):
    """查询结果导出请求体（query-result-export-plan §4.3）。

    导出时重新执行 SQL，游标流式生成 CSV。密码由前端内存持有，每次请求传入。
    """

    sql: str = Field(
        ...,
        min_length=1,
        max_length=65535,
        description="要导出的只读 SQL 语句（SELECT/SHOW/WITH）",
        examples=["SELECT * FROM orders WHERE status='pending'"],
    )
    password: str | None = Field(
        default=None,
        description="连接密码。仅存于内存，不落盘（AGENTS.md §安全红线）。",
    )
    max_rows: int | None = Field(
        default=None,
        ge=1,
        description="单次导出行数上限。默认 EXPORT_MAX_ROWS，传入值只允许缩小（端点内封顶）。",
    )
    max_execution_ms: int = Field(
        default=30000,
        ge=1000,
        le=120000,
        description="查询执行最大时长（毫秒），超时按首批拉取超时处理。",
    )


class SlowQueryItem(BaseModel):
    """慢查询记录项（api-contract §2.4 SlowQuery）。"""

    sql_text: str = Field(
        ...,
        description="慢查询 SQL 文本",
    )
    query_time_sec: float = Field(
        ...,
        description="查询耗时（秒）",
    )
    lock_time_sec: float | None = Field(
        default=None,
        description="锁等待时间（秒）",
    )
    rows_examined: int = Field(
        default=0,
        description="扫描行数",
    )
    rows_sent: int = Field(
        default=0,
        description="返回行数",
    )
    executed_at: str | None = Field(
        default=None,
        description="执行时间（ISO 8601 格式）",
    )


class SlowQueryListResponse(BaseModel):
    """分页慢查询列表响应（api-contract §1.3 GET /api/connections/{id}/slow-queries）。

    若目标数据库未开启慢查询日志，items 为空列表，warning 字段描述原因。
    """

    items: list[SlowQueryItem] = Field(
        ...,
        description="慢查询列表",
    )
    total: int = Field(
        ...,
        description="总数",
    )
    page: int = Field(
        default=1,
        description="当前页码",
    )
    page_size: int = Field(
        default=20,
        serialization_alias="pageSize",
        description="每页条数",
    )
    warning: str | None = Field(
        default=None,
        description="若慢查询日志未启用，描述原因；启用时为 null",
    )


# =============================================================================
# 健康巡检 Schema（api-contract §2.5 HealthReport）
# =============================================================================


class HealthReportItem(BaseModel):
    """检查项条目（api-contract §2.5 HealthReport items）。"""

    name: str = Field(..., description="检查项名称")
    status: str = Field(..., description="状态：pass / warning / error / skipped")
    value: str | None = Field(default=None, description="当前值")
    threshold: str | None = Field(default=None, description="阈值描述")
    suggestion: str | None = Field(default=None, description="优化建议")
    is_destructive: bool = Field(
        default=False,
        description="此建议是否会修改数据/结构",
    )


class HealthReportCategory(BaseModel):
    """检查项分类（api-contract §2.5 HealthReport categories）。"""

    name: str = Field(..., description="类别名（连接/复制/性能/慢查询/存储/安全）")
    items: list[HealthReportItem] = Field(..., description="该类别下的检查项列表")


class HealthReportResponse(BaseModel):
    """健康巡检报告详情（api-contract §2.5 HealthReport）。

    对应 ORM 模型 ReportModel，用于 GET /api/reports/{id} 响应。
    """

    id: str = Field(..., description="报告唯一标识")
    connection_id: str = Field(..., description="关联的连接 ID")
    status: str = Field(
        ...,
        description="报告状态：completed / cancelled / partial",
    )
    score: int = Field(..., description="健康评分 0-100")
    generated_at: datetime = Field(..., description="报告生成时间")
    duration_sec: float = Field(..., description="巡检总耗时（秒）")
    severity_counts: dict[str, int] = Field(
        ...,
        description="严重级别计数 {error, warning, pass, skipped}",
    )
    categories: list[HealthReportCategory] = Field(
        ...,
        description="检查项分类列表",
    )
    user_id: str | None = Field(default=None, description="所属用户 ID（null = 历史遗留数据）")

    model_config = {"from_attributes": True}


class HealthReportListResponse(BaseModel):
    """分页健康巡检报告列表。"""

    items: list[HealthReportResponse] = Field(..., description="报告列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(
        default=20,
        serialization_alias="pageSize",
        description="每页条数",
    )


# =============================================================================
# 故障排查 Schema（api-contract §1.5 POST /api/connections/{id}/troubleshoot）
# =============================================================================


class TroubleshootRequest(BaseModel):
    """故障排查请求体（api-contract §1.5 POST /api/connections/{id}/troubleshoot）。

    支持自动检测模式和指定问题类型。password 由前端 localStorage 持有。
    """

    issue_type: str = Field(
        default="auto",
        description="排查类型：auto（自动检测）/ deadlock / connection_flood "
        "/ replication_lag / slow_performance / disk_full",
    )
    context: str = Field(
        default="",
        max_length=2048,
        description="排查上下文描述（前端补充的用户描述）",
    )
    password: str | None = Field(
        default=None,
        description="连接密码。由前端 localStorage 持有，每次请求传入。密码仅存于内存，不落盘。",
    )
    session_id: str | None = Field(
        default=None,
        description="会话 ID，指定后将诊断结论写入该会话的消息历史",
    )


# =============================================================================
# 认证与用户管理 Schema
# =============================================================================


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="用户名",
    )
    password: str = Field(
        ...,
        min_length=1,
        description="密码",
    )


class UserResponse(BaseModel):
    """用户信息响应体（不含密码哈希）。"""

    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    """登录响应体——返回 JWT token 和用户基本信息。"""

    access_token: str = Field(..., description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    user: UserResponse  # 前向引用，使用 model_rebuild() 解析


class UserCreateRequest(BaseModel):
    """创建用户请求体（仅 admin 可用）。"""

    username: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="用户名",
    )
    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
        description="密码（至少 6 字符）",
    )
    role: str = Field(
        default="readonly",
        description="角色：admin 或 readonly",
    )

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in ("admin", "readonly"):
            raise ValueError("角色必须为 admin 或 readonly")
        return v


class UserUpdateRequest(BaseModel):
    """更新用户请求体（所有字段可选）。"""

    role: str | None = Field(default=None, description="新角色")
    is_active: bool | None = Field(default=None, description="是否启用")
    password: str | None = Field(
        default=None, min_length=6, max_length=128, description="新密码（可选）"
    )

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ("admin", "readonly"):
            raise ValueError("角色必须为 admin 或 readonly")
        return v


class UserListResponse(BaseModel):
    """分页用户列表响应。"""

    items: list[UserResponse]
    total: int
    page: int
    page_size: int = Field(serialization_alias="pageSize")


# 解析 LoginResponse 中的前向引用
LoginResponse.model_rebuild()
