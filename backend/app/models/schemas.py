"""
Pydantic 请求/响应 Schema 定义。

依据 api-contract §2.1 ConnectionConfig 实体定义。
与 ORM 模型分离：Schema 层控制序列化行为（如排除 password 字段）。
"""

from __future__ import annotations

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

    model_config = {
        "from_attributes": True,  # 支持从 ORM 模型自动转换
    }


class ConnectionListResponse(BaseModel):
    """分页连接列表响应（api-contract §1.1）。

    注意：pageSize 字段使用 Pydantic alias 以匹配前端 camelCase 约定，
    Python 内部使用 snake_case。
    """
    items: list[ConnectionResponse] = Field(..., description="连接列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(
        default=20, alias="pageSize", validation_alias="pageSize",
        description="每页条数",
    )

    model_config = {"populate_by_name": True}


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

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    """消息响应体，对应 api-contract §2.2 Message 实体。"""
    id: str = Field(..., description="消息唯一标识")
    session_id: str = Field(..., description="所属会话 ID")
    role: str = Field(..., description="消息角色：user / assistant / system")
    content: str = Field(..., description="消息内容")
    message_type: str = Field(
        ...,
        description="消息类型：natural_language / sql / diagnosis / "
        "troubleshoot / health_check",
    )
    sql_generated: str | None = Field(default=None, description="LLM 生成的原始 SQL")
    sql_executed: str | None = Field(default=None, description="实际执行的 SQL")
    result_preview: dict[str, Any] | None = Field(
        default=None, description="查询结果预览（最多 20 行）",
    )
    error_info: dict[str, Any] | None = Field(
        default=None, description="错误信息",
    )
    created_at: datetime = Field(..., description="创建时间")
    tokens_used: int = Field(..., description="本条消息 token 消耗")

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    """分页会话列表响应。"""
    items: list[SessionResponse] = Field(..., description="会话列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(
        default=20, alias="pageSize", validation_alias="pageSize",
        description="每页条数",
    )

    model_config = {"populate_by_name": True}


class MessageListResponse(BaseModel):
    """分页消息列表响应。"""
    items: list[MessageResponse] = Field(..., description="消息列表")
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(
        default=50, alias="pageSize", validation_alias="pageSize",
        description="每页条数",
    )

    model_config = {"populate_by_name": True}
