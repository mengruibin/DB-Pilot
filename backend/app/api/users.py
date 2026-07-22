"""
用户管理 API 路由（仅 admin 可用）。

端点：
  - GET    /api/users          分页列出用户
  - POST   /api/users          创建用户
  - GET    /api/users/{id}     获取用户详情
  - PUT    /api/users/{id}     更新用户（角色、启用状态、密码）
  - DELETE /api/users/{id}     删除用户
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin_user
from app.auth.security import hash_password
from app.database import get_session
from app.models.schemas import (
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from app.models.user import UserModel

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize", description="每页条数"),
    db: AsyncSession = Depends(get_session),
    _admin: UserModel = Depends(get_current_admin_user),
):
    """列出所有用户（分页），仅 admin 可访问。"""
    # 统计总数
    count_q = select(func.count()).select_from(UserModel)
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    # 分页查询
    stmt = (
        select(UserModel)
        .order_by(UserModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    users = result.scalars().all()

    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    db: AsyncSession = Depends(get_session),
    _admin: UserModel = Depends(get_current_admin_user),
):
    """创建新用户（仅 admin 可操作）。"""
    # 检查用户名唯一性
    existing = await db.execute(
        select(UserModel).where(UserModel.username == body.username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "USERNAME_EXISTS",
                "user_message": f"用户名 '{body.username}' 已存在",
            },
        )

    # 创建用户
    user = UserModel(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_session),
    _admin: UserModel = Depends(get_current_admin_user),
):
    """获取单个用户详情（仅 admin 可访问）。"""
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"用户 {user_id} 不存在",
            },
        )
    return UserResponse.model_validate(user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_session),
    _admin: UserModel = Depends(get_current_admin_user),
):
    """更新用户（角色、启用状态、密码，仅 admin 可操作）。"""
    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"用户 {user_id} 不存在",
            },
        )

    # 更新可选字段
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.password_hash = hash_password(body.password)

    user.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_session),
    current_admin: UserModel = Depends(get_current_admin_user),
):
    """删除用户（仅 admin 可操作，不能删除自己）。"""
    # 不能删除自己
    if user_id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "CANNOT_DELETE_SELF",
                "user_message": "不能删除自己的账号",
            },
        )

    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NOT_FOUND",
                "user_message": f"用户 {user_id} 不存在",
            },
        )

    await db.delete(user)
    await db.commit()
