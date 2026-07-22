"""
FastAPI 认证依赖注入函数。

提供 get_current_user() 和 get_current_admin_user() 两个 FastAPI Depends 函数，
用于保护需要登录（或需要管理员权限）的 API 端点。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.database import get_session
from app.models.user import UserModel

# Bearer token 提取器（auto_error=False 以便返回自定义错误格式）
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> UserModel:
    """从 Authorization: Bearer <token> 中解析当前登录用户。

    验证流程：
      1. 检查 Authorization 头是否存在
      2. 解码并验证 JWT 令牌
      3. 从数据库加载用户并检查是否启用

    Raises:
        HTTPException 401: token 缺失、无效、用户不存在或已禁用。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "UNAUTHORIZED",
                "user_message": "请先登录",
            },
        )

    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "INVALID_TOKEN",
                "user_message": "登录已过期，请重新登录",
            },
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "INVALID_TOKEN",
                "user_message": "无效的登录凭证",
            },
        )

    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "USER_INACTIVE",
                "user_message": "账号不存在或已被禁用",
            },
        )

    return user


async def get_current_admin_user(
    current_user: UserModel = Depends(get_current_user),
) -> UserModel:
    """确保当前用户是 admin 角色，否则返回 403。

    用于用户管理、系统配置等管理员专属端点。

    Raises:
        HTTPException 403: 当前用户不是 admin 角色。
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "FORBIDDEN",
                "user_message": "需要管理员权限",
            },
        )
    return current_user


async def verify_resource_ownership(
    resource: Any | None,
    current_user: UserModel,
    resource_name: str = "资源",
) -> None:
    """校验当前用户对资源的归属权。

    所有用户（含 admin）一视同仁，只能访问自己的资源（user_id == 当前用户）。
    不存在或无权限均返回 404，使用相同错误报文防止信息泄露。

    Args:
        resource: 数据库查询返回的 ORM 对象（None 表示不存在）。
        current_user: 当前登录用户。
        resource_name: 资源中文名，用于 404 报错信息。

    Raises:
        HTTPException 404: 资源不存在或不属于当前用户。
    """
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"{resource_name} 不存在或已删除",
            },
        )
    # 所有用户（含 admin）只能访问自己的资源
    resource_user_id = getattr(resource, "user_id", None)
    if resource_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"{resource_name} 不存在或已删除",
            },
            )
