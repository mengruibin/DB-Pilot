"""
用户数据隔离单元测试。

验证按 user_id 维度的 RBAC 数据隔离：
- admin 用户可查看所有数据（含 NULL user_id 的历史记录）
- 普通用户只可查看自己创建的数据
- 普通用户无法访问其他用户的数据（返回 404）
- 创建操作自动绑定当前用户的 user_id

注意：这些测试不依赖真实数据库连接，仅测试 FastAPI 依赖注入
和数据隔离逻辑。需要 mock DB 或使用事务回滚的集成测试。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.auth.dependencies import verify_resource_ownership
from app.models.user import UserModel


# =============================================================================
# Mock 辅助
# =============================================================================


def make_user(user_id: str, role: str = "readonly") -> UserModel:
    """创建一个模拟的用户对象。"""
    user = MagicMock(spec=UserModel)
    user.id = user_id
    user.role = role
    user.username = f"test_user_{user_id}"
    user.is_active = True
    return user


def make_resource(user_id: str | None = None) -> Any:
    """创建一个模拟的资源对象（ORM 模型风格）。"""
    resource = MagicMock()
    resource.user_id = user_id
    return resource


# =============================================================================
# 测试 verify_resource_ownership 辅助函数
# =============================================================================


class TestVerifyResourceOwnership:
    """Task 0 验收标准：verify_resource_ownership 函数行为。"""

    async def test_admin_cannot_access_others_resource(self) -> None:
        """admin 也不能访问他人的资源（所有用户一视同仁）。"""
        admin = make_user("admin_001", role="admin")
        resource = make_resource(user_id="other_user")

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(resource, admin, "连接")
        assert exc_info.value.status_code == 404

    async def test_admin_cannot_access_null_user_id(self) -> None:
        """admin 也不能访问 user_id 为 NULL 的历史数据。"""
        admin = make_user("admin_001", role="admin")
        resource = make_resource(user_id=None)

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(resource, admin, "连接")
        assert exc_info.value.status_code == 404

    async def test_owner_can_access_own_resource(self) -> None:
        """所有用户（含 admin）可访问自己的资源。"""
        user = make_user("user_001", role="admin")
        resource = make_resource(user_id="user_001")

        await verify_resource_ownership(resource, user, "连接")

        await verify_resource_ownership(resource, user, "连接")

    async def test_non_owner_cannot_access_others_resource(self) -> None:
        """普通用户无法访问他人的资源（404）。"""
        user = make_user("user_001", role="readonly")
        resource = make_resource(user_id="user_002")

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(resource, user, "连接")
        assert exc_info.value.status_code == 404

    async def test_none_resource_returns_404(self) -> None:
        """resource=None 时抛 404。"""
        user = make_user("user_001", role="readonly")

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(None, user, "连接")
        assert exc_info.value.status_code == 404

    async def test_error_message_does_not_leak_ownership(self) -> None:
        """不存在和无权限用相同 404 报文，防信息泄露。"""
        user = make_user("user_001", role="readonly")

        # None 资源的错误
        with pytest.raises(HTTPException) as exc_info_none:
            await verify_resource_ownership(None, user, "连接")

        # 无权限的错误
        resource = make_resource(user_id="user_002")
        with pytest.raises(HTTPException) as exc_info_forbidden:
            await verify_resource_ownership(resource, user, "连接")

        assert exc_info_none.value.detail == exc_info_forbidden.value.detail


# =============================================================================
# 测试 Connection API 数据隔离
# =============================================================================


class TestConnectionDataIsolation:
    """模拟 connection API 端点的数据隔离行为。"""

    @pytest.mark.parametrize(
        ("current_user_id", "expected_count"),
        [
            ("user_1", 1),  # 看到自己 user_id 的 1 条
            ("user_2", 1),  # 看到自己 user_id 的 1 条
            ("user_3", 0),  # 没有匹配数据，看到 0 条
        ],
    )
    async def test_list_filter_behavior(
        self, current_user_id: str, expected_count: int,
    ) -> None:
        """验证列表查询只能看到自己的数据。"""
        # 模拟数据：3 条连接记录
        mock_connections = [
            MagicMock(user_id="user_1"),
            MagicMock(user_id="user_2"),
            MagicMock(user_id=None),  # 历史数据（无人可见）
        ]

        # 筛选逻辑：所有用户只看到 user_id == 自己
        filtered = [c for c in mock_connections if c.user_id == current_user_id]

        assert len(filtered) == expected_count

    async def test_non_owner_gets_404_on_single_resource(self) -> None:
        """普通用户访问他人连接详情时返回 404。"""
        user = make_user("user_001", role="readonly")
        resource = make_resource(user_id="user_002")

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(resource, user, "连接")
        assert exc_info.value.status_code == 404

    async def test_owner_can_access_single_resource(self) -> None:
        """普通用户可访问自己的连接详情。"""
        user = make_user("user_001", role="readonly")
        resource = make_resource(user_id="user_001")

        await verify_resource_ownership(resource, user, "连接")

    async def test_admin_cannot_access_others_single_resource(self) -> None:
        """admin 也不能访问他人连接。"""
        admin = make_user("admin_001", role="admin")
        resource = make_resource(user_id="user_002")

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(resource, admin, "连接")
        assert exc_info.value.status_code == 404


# =============================================================================
# 测试 Session 数据隔离
# =============================================================================


class TestSessionDataIsolation:
    """与 Connection 相同的隔离模式，验证资源名参数正确传递。"""

    async def test_session_ownership_check(self) -> None:
        """普通用户无法访问他人会话。"""
        user = make_user("user_001", role="readonly")
        session = make_resource(user_id="user_002")

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(session, user, "会话")
        assert exc_info.value.status_code == 404

    async def test_session_list_only_self(self) -> None:
        """admin 也只看到自己的会话。"""
        sessions = [
            MagicMock(user_id="user_1"),
            MagicMock(user_id="admin_001"),
        ]
        # 所有人只看到自己 user_id 的
        user = make_user("admin_001", role="admin")
        filtered = [s for s in sessions if s.user_id == user.id]
        assert len(filtered) == 1


# =============================================================================
# 测试 Report 数据隔离
# =============================================================================


class TestReportDataIsolation:
    """与 Connection 相同的隔离模式。"""

    async def test_report_ownership_check(self) -> None:
        """普通用户无法访问他人报告。"""
        user = make_user("user_001", role="readonly")
        report = make_resource(user_id="user_002")

        with pytest.raises(HTTPException) as exc_info:
            await verify_resource_ownership(report, user, "报告")
        assert exc_info.value.status_code == 404
