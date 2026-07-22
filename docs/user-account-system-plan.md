# 用户账号系统 + 移除 grant_detector 自动检测

> **状态**: 已完成 | **创建日期**: 2026-07-21 | **完成日期**: 2026-07-22 | **版本**: v1.0

---

## 一、方案简述

当前 DB-Pilot 没有用户账号系统，所有连接全局可见，任何人都可以执行操作。
`grant_detector.py` 通过执行 `SHOW GRANTS FOR CURRENT_USER()` 自动检测 MySQL 用户角色，
存在多列权限逗号误拆分等解析 bug，且二值化（readonly/admin）无法反映真实的表级/列级权限情况。

本方案将：

1. **新建完整的用户账号系统** — users 表 + bcrypt 密码哈希 + JWT Bearer 令牌认证
2. **管理员手动管理用户角色** — admin 通过 UI 创建账号并分配 readonly/admin 角色，权限来源清晰明确
3. **移除 grant_detector.py** — 删除 276 行自动检测代码及其在 chat/troubleshoot/connection 三处的调用
4. **user_role 来源替换** — 原来来自 `SHOW GRANTS` 自动检测，改为来自当前登录用户的 `role` 字段
5. **安全护栏代码零改动** — safety.py / sql_auditor.py / tool_node.py / mysql.py 全部原样使用 user_role

### 核心数据流变化

```
BEFORE:  grant_detector.py (SHOW GRANTS)  →  config["user_role"]  →  safety/mysql/tools
AFTER:   JWT Bearer Token  →  current_user.role  →  config["user_role"]  →  safety/mysql/tools (不变)
```

### 角色定义

| 角色 | 权限 |
|------|------|
| **admin** | 可执行 INSERT / UPDATE / DELETE，可管理用户 |
| **readonly** | 仅可执行 SELECT 查询，写入操作在 SQL 审计层被拦截 |

---

## 二、任务目标

1. 实现 users 表 + ORM 模型 + Alembic 迁移
2. 实现 bcrypt 密码哈希 + JWT 令牌创建/验证
3. 实现 `POST /api/auth/login`（登录）和 `GET /api/auth/me`（获取当前用户）
4. 实现 `/api/users` CRUD 接口（仅 admin 可访问）
5. 移除 `grant_detector.py` 及相关所有调用
6. 所有现有 API 端点加上 `Depends(get_current_user)` JWT 认证
7. 前端实现登录页面、auth store、路由守卫
8. 前端实现用户管理页面（admin only）
9. 前端 Sidebar 加用户信息展示、退出登录、管理员导航项
10. 提供创建管理员账号的 CLI 脚本

---

## 三、改动文件清单

### 3.1 新建文件（11 个）

| # | 文件 | 说明 |
|---|------|------|
| 1 | `backend/app/models/user.py` | UserModel ORM 模型 |
| 2 | `backend/app/auth/__init__.py` | Python 包标记（空文件） |
| 3 | `backend/app/auth/security.py` | bcrypt 密码哈希 + JWT 创建/解码 |
| 4 | `backend/app/auth/dependencies.py` | `get_current_user()` / `get_current_admin_user()` FastAPI 依赖 |
| 5 | `backend/app/api/auth.py` | `POST /api/auth/login` + `GET /api/auth/me` |
| 6 | `backend/app/api/users.py` | 用户 CRUD 路由（admin only） |
| 7 | `backend/alembic/versions/007_create_users_table.py` | users 表迁移 |
| 8 | `backend/scripts/create_admin.py` | 交互式创建管理员 CLI 脚本 |
| 9 | `frontend/src/stores/auth.ts` | Pinia 认证 store |
| 10 | `frontend/src/views/LoginView.vue` | 登录页面 |
| 11 | `frontend/src/views/UserManagementView.vue` | 用户管理页面（admin only） |

### 3.2 修改文件（11 个）

| # | 文件 | 改动简述 |
|---|------|----------|
| 12 | `backend/app/config.py` | 加 `JWT_SECRET` + `ACCESS_TOKEN_EXPIRE_MINUTES` |
| 13 | `backend/app/main.py` | 注册 auth_router + users_router |
| 14 | `backend/app/models/schemas.py` | 加 6 个认证/用户 Pydantic Schema |
| 15 | `backend/app/api/chat.py` | 移除 grant_detector；加 `user_role` 参数 + `Depends(get_current_user)` |
| 16 | `backend/app/api/troubleshoot.py` | 同上模式 |
| 17 | `backend/app/api/connection.py` | 移除 grant_detector；`user_role = current_user.role`；所有端点加认证 |
| 18 | `backend/app/api/query.py` | 所有端点加 `Depends(get_current_user)` |
| 19 | `frontend/src/router.ts` | 加 `/login` + `/users` 路由；beforeEach 路由守卫 |
| 20 | `frontend/src/api/client.ts` | 加 `Authorization: Bearer` 头；401 自动跳转登录 |
| 21 | `frontend/src/components/common/Sidebar.vue` | 加用户信息区、退出登录、管理员"用户管理"入口 |
| 22 | `frontend/src/App.vue` | onMounted 调用 `authStore.initAuth()` |

### 3.3 删除文件（1 个）

| # | 文件 | 说明 |
|---|------|------|
| 23 | `backend/app/engine/grant_detector.py` | 276 行全部删除 |

### 3.4 不变文件（确认清单）

以下文件零改动，继续原样工作：

- `backend/app/agent/state.py` — `user_role` TypedDict 字段保留
- `backend/app/agent/safety.py` — `conn_config.get("user_role", "readonly")` 不变
- `backend/app/agent/tool_node.py` — tool_args `user_role` 注入逻辑不变
- `backend/app/agent/tools/query.py` — `InjectedToolArg` 参数不变
- `backend/app/agent/tools/diagnosis.py` — 同上
- `backend/app/agent/graph.py` — 无直接 user_role 处理
- `backend/app/db/base.py` — 抽象方法签名不变
- `backend/app/db/mysql.py` — `SET SESSION TRANSACTION READ ONLY` 逻辑不变
- `backend/app/db/postgresql.py` — 忽略参数，不变
- `backend/app/db/oracle.py` — 忽略参数，不变
- `backend/app/engine/sql_auditor.py` — `audit()` 参数不变
- `backend/tests/test_sql_auditor.py` — 直接传 `user_role`，不改
- `backend/tests/test_tool_node.py` — 默认 readonly 兜底测试仍有效
- `frontend/src/stores/connection.ts` — 不改
- `frontend/src/stores/chat.ts` — 不改
- `frontend/src/stores/report.ts` — 不改
- `frontend/src/composables/` — 全部 composable 不改
- `frontend/src/views/ChatView.vue` — 不改
- `frontend/src/views/ConnectionView.vue` — 不改
- `frontend/src/views/ReportView.vue` — 不改

---

## 四、详细任务列表

### Phase 1: 后端基础设施

---

#### 任务 1: 安装 Python 依赖

- [x] **状态: 已完成**

**描述**: 在后端虚拟环境中安装 passlib + python-jose 认证库。

**具体步骤**:
1. 激活后端 venv
2. 执行 `uv pip install passlib[bcrypt] python-jose[cryptography]`
3. 更新 `backend/requirements.txt`（或 `pyproject.toml`）添加依赖声明

**验收标准**:
- [x] `python -c "from passlib.context import CryptContext"` 无报错
- [x] `python -c "from jose import jwt"` 无报错
- [x] `requirements.txt` 中已添加 `passlib[bcrypt]>=1.7.4` 和 `python-jose[cryptography]>=3.3.0`

---

#### 任务 2: 添加认证配置项

- [x] **状态: 已完成**

**描述**: 在 config.py 中添加 JWT 密钥和过期时间配置。

**涉及文件**: `backend/app/config.py`, `backend/.env.example`, `backend/.env`

**具体步骤**:
1. 在 `Settings` 类中添加 `JWT_SECRET: str`（必填，≥32 字符），添加到 `_required_not_empty` 校验列表
2. 添加 `ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440`（默认 24 小时，≥5 分钟）
3. 添加 `@field_validator("JWT_SECRET")` 最小长度校验（32 字符）
4. 在 `.env.example` 和 `.env` 中添加对应的环境变量

**验收标准**:
- [x] 启动时 `JWT_SECRET` 为空或长度不足 32 字符时拒绝启动并给出明确错误信息
- [x] `JWT_SECRET` 合法时正常启动
- [x] `.env.example` 中有示例配置

---

#### 任务 3: 创建 users 表 Alembic 迁移

- [x] **状态: 已完成**

**描述**: 创建数据库迁移脚本，添加 `users` 表。

**涉及文件**: `backend/alembic/versions/007_create_users_table.py`（新建）

**具体步骤**:
1. `cd backend && alembic revision -m "create_users_table"`
2. 编辑生成的文件，设置 `down_revision = "006_add_thinking_steps_to_messages"`
3. 编写 `upgrade()`: 创建 `users` 表（id VARCHAR(36) PK, username VARCHAR(64) UNIQUE NOT NULL INDEX, password_hash VARCHAR(256) NOT NULL, role VARCHAR(16) NOT NULL DEFAULT 'readonly', is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at DATETIME, updated_at DATETIME）
4. 编写 `downgrade()`: `op.drop_table("users")`
5. 执行 `alembic upgrade head`

**验收标准**:
- [x] `alembic upgrade head` 执行成功，`users` 表存在
- [x] `alembic downgrade -1` 执行成功，`users` 表被删除
- [x] 再次 `upgrade head` 成功
- [x] 表中 `role` 列默认值为 `readonly`
- [x] 表中 `is_active` 列默认值为 `TRUE`

---

#### 任务 4: 创建 UserModel ORM 模型

- [x] **状态: 已完成**

**描述**: 创建 `UserModel` SQLAlchemy ORM 模型，映射到 `users` 表。

**涉及文件**: `backend/app/models/user.py`（新建）

**具体步骤**:
1. 创建文件，定义 `UserModel(Base)` 类
2. 字段: id（`usr_<uuid8>` 格式默认值）、username（unique index）、password_hash、role（默认 "readonly"）、is_active（默认 True）、created_at、updated_at
3. 添加 `__repr__` 方法（不暴露密码哈希）

**验收标准**:
- [x] `from app.models.user import UserModel` 导入成功
- [x] 模型字段映射与 `users` 表迁移一致
- [x] id 默认值格式为 `usr_` 前缀 + 8 位 hex

---

#### 任务 5: 创建认证安全工具模块

- [x] **状态: 已完成**

**描述**: 创建密码哈希和 JWT 令牌的创建/验证函数。

**涉及文件**: `backend/app/auth/__init__.py`（新建）, `backend/app/auth/security.py`（新建）

**具体步骤**:
1. 创建 `backend/app/auth/` 目录
2. 创建 `__init__.py`（空文件）
3. 创建 `security.py`:
   - `hash_password(password) -> str` — bcrypt 哈希
   - `verify_password(plain, hashed) -> bool` — bcrypt 验证
   - `create_access_token(user_id, username, role) -> str` — 生成 JWT（payload 含 sub/username/role/exp）
   - `decode_access_token(token) -> dict | None` — 解码 JWT，失败返回 None
   - 算法: HS256

**验收标准**:
- [x] `hash_password("test")` 返回 60 字符 bcrypt 哈希
- [x] `verify_password("test", hash)` 返回 `True`
- [x] `verify_password("wrong", hash)` 返回 `False`
- [x] `create_access_token(...)` 返回三段式 JWT 字符串
- [x] `decode_access_token(valid_token)` 返回正确 payload
- [x] `decode_access_token("invalid")` 返回 `None`
- [x] 过期 token 解码返回 `None`

---

#### 任务 6: 创建认证依赖注入函数

- [x] **状态: 已完成**

**描述**: 创建 FastAPI `Depends` 函数，从 Bearer token 解析当前用户。

**涉及文件**: `backend/app/auth/dependencies.py`（新建）

**具体步骤**:
1. 创建 `dependencies.py`:
   - `get_current_user()` — 从 `Authorization: Bearer <token>` 解析用户，token 缺失/无效/用户不存在/已禁用 → 401
   - `get_current_admin_user()` — 叠加检查 `role == "admin"`，否则 → 403
2. 使用 `HTTPBearer(auto_error=False)` 提取 token
3. 从 DB 加载 UserModel 实例（含 `is_active` 检查）

**验收标准**:
- [x] 无 Authorization 头 → 401 `UNAUTHORIZED`
- [x] 无效 token → 401 `INVALID_TOKEN`
- [x] token 对应用户不存在或 `is_active=False` → 401 `USER_INACTIVE`
- [x] 有效 token → 返回 `UserModel` 实例
- [x] `get_current_admin_user` 在 `role="readonly"` 时 → 403 `FORBIDDEN`

---

### Phase 2: 后端 API

---

#### 任务 7: 添加认证/用户 Pydantic Schema

- [x] **状态: 已完成**

**描述**: 在 schemas.py 中添加登录请求/响应、用户创建/更新/列表的 Pydantic 模型。

**涉及文件**: `backend/app/models/schemas.py`（修改）

**具体步骤**:
1. 添加 `LoginRequest`（username, password）
2. 添加 `LoginResponse`（access_token, token_type, user: UserResponse）
3. 添加 `UserResponse`（id, username, role, is_active, created_at, updated_at, `model_config = {"from_attributes": True}`）
4. 添加 `UserCreateRequest`（username, password（≥6 字符）, role（默认 "readonly"，validator 校验 admin/readonly））
5. 添加 `UserUpdateRequest`（role, is_active, password 全可选）
6. 添加 `UserListResponse`（items, total, page, page_size）
7. `LoginResponse.model_rebuild()` 解决前向引用

**验收标准**:
- [x] `LoginRequest(username="test", password="123456")` 校验通过
- [x] `UserCreateRequest(username="test", password="123", role="admin")` 校验通过
- [x] `UserCreateRequest(username="test", password="123", role="invalid")` → `ValidationError`
- [x] `UserCreateRequest(username="test", password="12")` → `ValidationError`（密码 < 6）
- [x] `UserResponse.model_validate(orm_instance)` 正确序列化

---

#### 任务 8: 创建认证 API 路由

- [x] **状态: 已完成**

**描述**: 创建登录和获取当前用户的 API 端点。

**涉及文件**: `backend/app/api/auth.py`（新建）

**具体步骤**:
1. 创建 `auth.py`，prefix=`/api/auth`
2. `POST /api/auth/login`:
   - 接收 `LoginRequest`
   - 查询 `users` 表匹配 username
   - bcrypt 验证密码
   - 检查 `is_active`
   - 返回 `LoginResponse`（JWT + 用户信息）
3. `GET /api/auth/me`:
   - 依赖 `get_current_user`
   - 返回当前登录用户的 `UserResponse`

**验收标准**:
- [x] `POST /api/auth/login` 正确凭据 → 200 + JWT token + 用户信息
- [x] `POST /api/auth/login` 错误密码 → 401 `INVALID_CREDENTIALS`
- [x] `POST /api/auth/login` 已禁用用户 → 403 `USER_INACTIVE`
- [x] `GET /api/auth/me` 有效 token → 200 + 用户信息
- [x] `GET /api/auth/me` 无 token → 401

---

#### 任务 9: 创建用户管理 API 路由

- [x] **状态: 已完成**

**描述**: 创建用户 CRUD 端点，仅 admin 可访问。

**涉及文件**: `backend/app/api/users.py`（新建）

**具体步骤**:
1. 创建 `users.py`，prefix=`/api/users`，所有端点依赖 `get_current_admin_user`
2. `GET /api/users` — 分页列表（支持 page/pageSize）
3. `POST /api/users` — 创建用户（检查用户名唯一性 → 409 if duplicate）
4. `GET /api/users/{user_id}` — 获取单个用户
5. `PUT /api/users/{user_id}` — 更新用户（角色/启用状态/密码，全可选），手动设置 `updated_at`
6. `DELETE /api/users/{user_id}` — 删除用户（不能删除自己 → 400）

**验收标准**:
- [x] `GET /api/users` 返回正确分页（total, page, pageSize, items）
- [x] `POST /api/users` 创建成功 → 201
- [x] `POST /api/users` 重复用户名 → 409 `USERNAME_EXISTS`
- [x] `PUT /api/users/{id}` 更新角色 → 成功
- [x] `DELETE /api/users/{id}` 删除自己 → 400 `CANNOT_DELETE_SELF`
- [x] 非 admin 用户访问 → 403
- [x] `GET /api/users/{id}` 用户不存在 → 404

---

#### 任务 10: 注册新路由

- [x] **状态: 已完成**

**描述**: 在 main.py 中注册 auth 和 users 的路由。

**涉及文件**: `backend/app/main.py`（修改）

**具体步骤**:
1. 在 `create_app()` 中添加:
   ```python
   from app.api.auth import router as auth_router
   app.include_router(auth_router)
   from app.api.users import router as users_router
   app.include_router(users_router)
   ```

**验收标准**:
- [x] `/api/auth/login` 和 `/api/auth/me` 在 OpenAPI docs 中可见
- [x] `/api/users` 系列端点 OpenAPI docs 中可见
- [x] 健康检查 `/api/health` 仍正常返回 200

---

### Phase 3: 移除 grant_detector + 更新调用方

---

#### 任务 11: 更新 chat.py

- [x] **状态: 已完成**

**描述**: 移除 grant_detector 调用，从认证用户获取 user_role。

**涉及文件**: `backend/app/api/chat.py`（修改）

**具体步骤**:
1. `_resolve_connection_config()` 加 `user_role: str = "readonly"` 参数
2. 删除第 277-309 行（grant_detector 整个 block + else 分支）
3. 替换为 `config["user_role"] = user_role`
4. `_stream_events()` 加 `user_role: str = "readonly"` 参数，透传至 `_resolve_connection_config`
5. `chat_stream` 端点加 `current_user: UserModel = Depends(get_current_user)`，传递 `current_user.role`
6. `cancel_chat` 端点加 `Depends(get_current_user)`
7. 所有 session 端点（list_sessions, list_session_messages, rename_session, delete_session）加 `Depends(get_current_user)`

**验收标准**:
- [x] 代码中不再有 `from app.engine.grant_detector import ...`
- [x] 不再引用 `detect_mysql_role`、`get_cached_role`、`set_cached_role`
- [x] SSE 聊天请求无 token → 401
- [x] SSE 聊天请求有效 token → user_role 正确传入 AgentState
- [x] session CRUD 端点全部需要认证

---

#### 任务 12: 更新 troubleshoot.py

- [x] **状态: 已完成**

**描述**: 同 chat.py 模式，移除 grant_detector + 加认证。

**涉及文件**: `backend/app/api/troubleshoot.py`（修改）

**具体步骤**:
1. `_resolve_conn_config()` 加 `user_role: str = "readonly"` 参数
2. 删除 grant_detector block，替换为 `config["user_role"] = user_role`
3. `_troubleshoot_stream()` 加 `user_role` 参数
4. `troubleshoot_stream` 端点加 `Depends(get_current_user)`

**验收标准**:
- [x] 无 grant_detector import
- [x] 故障排查 SSE 请求无 token → 401
- [x] user_role 正确传入 conn_config

---

#### 任务 13: 更新 connection.py

- [x] **状态: 已完成**

**描述**: 移除 grant_detector 调用，读 ORM 的 user_role。

**涉及文件**: `backend/app/api/connection.py`（修改）

**具体步骤**:
1. 所有 CRUD 端点加 `Depends(get_current_user)`:
   - `GET /api/connections` — list
   - `POST /api/connections` — create
   - `GET /api/connections/{id}` — detail
   - `PUT /api/connections/{id}` — update
   - `DELETE /api/connections/{id}` — delete
   - `GET /api/connections/{id}/metadata` — metadata
2. `test_connection` 端点加 `Depends(get_current_user)`
3. 删除 grant_detector import 和调用块（第 293-308 行）
4. 替换为 `user_role = current_user.role`

**验收标准**:
- [x] 所有连接端点无 token → 401
- [x] 连接测试端点用 `current_user.role` 作为 user_role
- [x] 无 grant_detector import

---

#### 任务 14: 更新 query.py + report.py

- [x] **状态: 已完成**

**描述**: 独立查询/报告端点加认证。

**涉及文件**: `backend/app/api/query.py`, `backend/app/api/report.py`（修改）

**具体步骤**:
1. 所有端点加 `current_user: UserModel = Depends(get_current_user)`
2. `execute_query` 中的 `audit()` 调用改为 `user_role=current_user.role`

**验收标准**:
- [x] 所有 query/report 端点无 token → 401
- [x] `execute_query` 基于用户 role 审计 SQL

---

#### 任务 15: 删除 grant_detector.py

- [x] **状态: 已完成**

**描述**: 删除整个文件。

**涉及文件**: `backend/app/engine/grant_detector.py`（删除）

**具体步骤**:
1. 删除文件
2. 全局搜索确认无残留引用: `rg "grant_detector|detect_mysql_role|get_cached_role|set_cached_role" backend/app/`

**验收标准**:
- [x] `backend/app/engine/grant_detector.py` 不存在
- [x] 全局搜索 grant_detector 引用仅存在于文档文件中（docs/目录）
- [x] 后端启动无 ImportError

---

### Phase 4: 前端

---

#### 任务 16: 创建前端 auth store

- [x] **状态: 已完成**

**描述**: 创建 Pinia store 管理 JWT token、当前用户、登录/登出逻辑。

**涉及文件**: `frontend/src/stores/auth.ts`（新建）

**具体步骤**:
1. 创建 auth store（Composition API style）:
   - state: `token`（localStorage 持久化 key=`db-pilot:token`）, `currentUser`, `isLoading`, `error`
   - getters: `isAuthenticated`, `isAdmin`
   - actions:
     - `login(username, password)` — `POST /api/auth/login` → 存 token 和 user
     - `logout()` — 清 token 和 user
     - `fetchMe()` — `GET /api/auth/me` → 恢复 user
     - `initAuth()` — 有 token 无 user 时调用 `fetchMe()`
2. 导出 `User` 和 `LoginResponse` TypeScript 接口

**验收标准**:
- [x] `login()` 成功 → token 写入 localStorage，currentUser 更新
- [x] `login()` 失败 → token 清除，error 更新
- [x] `logout()` → token 清除，currentUser 为 null
- [x] `fetchMe()` 有效 token → currentUser 恢复
- [x] `fetchMe()` 无效 token → 自动 logout
- [x] `isAdmin` 在 role="admin" 时返回 true
- [x] 页面刷新后 `initAuth()` 能恢复登录态

---

#### 任务 17: 更新 API 客户端

- [x] **状态: 已完成**

**描述**: 请求自动携带 Bearer token，401 时自动跳转登录。

**涉及文件**: `frontend/src/api/client.ts`（修改）

**具体步骤**:
1. 在 `request()` 函数中读取 `localStorage.getItem('db-pilot:token')`
2. 有 token 时设置 `Authorization: Bearer <token>` 头
3. 在错误处理中，`response.status === 401` 时:
   - 清除 localStorage token
   - 当前不在 `/login` 页面时跳转到 `/login`

**验收标准**:
- [x] 所有 API 请求自动携带 Authorization 头（有 token 时）
- [x] 无 token 时不携带 Authorization 头（登录请求本身）
- [x] 401 响应 → token 清除 + 页面跳转 `/login`
- [x] 已在 `/login` 页面时 401 不重复跳转

---

#### 任务 18: 更新路由（加守卫 + 新路由）

- [x] **状态: 已完成**

**描述**: 添加登录和用户管理路由，实现全局路由守卫。

**涉及文件**: `frontend/src/router.ts`（修改）

**具体步骤**:
1. 加路由:
   - `/login` → `LoginView.vue`, `meta: { title: '登录', noAuth: true }`
   - `/users` → `UserManagementView.vue`, `meta: { title: '用户管理', requiresAdmin: true }`
2. 添加 `beforeEach` 守卫:
   - 访问 `/login` 时已有 token → 重定向 `/`
   - 访问其他路由无 token → 重定向 `/login`
   - 访问 `requiresAdmin` 路由时调用 `fetchMe()` 检查 role → 非 admin 重定向 `/`

**验收标准**:
- [x] 未登录访问 `/` → 跳转 `/login`
- [x] 已登录访问 `/login` → 跳转 `/`
- [x] 非 admin 访问 `/users` → 跳转 `/`
- [x] admin 访问 `/users` → 正常显示
- [x] 已有路由（`/`, `/connections`, `/reports`）功能完整

---

#### 任务 19: 创建登录页面

- [x] **状态: 已完成**

**描述**: 创建居中登录卡片页面。

**涉及文件**: `frontend/src/views/LoginView.vue`（新建）

**具体步骤**:
1. 使用 Naive UI 组件（n-card, n-input, n-button, n-alert）
2. 表单: 用户名 + 密码（type="password"）+ 登录按钮（loading 态）
3. 错误提示: 登录失败显示 n-alert type="error"
4. 成功后: `router.push('/')`
5. 样式: 居中卡片，使用项目 CSS 变量（`--chat-*`）

**验收标准**:
- [x] 页面居中显示登录卡片
- [x] 输入用户名密码点击登录 → loading 态
- [x] 登录成功 → 跳转首页
- [x] 登录失败 → 显示错误提示
- [x] 页面与现有 UI 风格一致

---

#### 任务 20: 创建用户管理页面

- [x] **状态: 已完成**

**描述**: 创建 admin only 的用户管理页面。

**涉及文件**: `frontend/src/views/UserManagementView.vue`（新建）

**具体步骤**:
1. 使用 Naive UI（n-data-table, n-button, n-modal, n-form, n-input, n-select, n-switch, n-tag, n-popconfirm）
2. 功能:
   - 用户列表表格（username, role 标签, is_active 状态, created_at, 操作列）
   - "创建用户"按钮 → 弹窗表单（username, password, role select）
   - 编辑按钮 → 弹窗修改（role, is_active, 可选新密码）
   - 删除按钮 → popconfirm 确认
3. API 调用直接使用 `http` from `@/api/client`
4. 错误处理: 409 USERNAME_EXISTS → 表单错误提示；400 CANNOT_DELETE_SELF → warning toast

**验收标准**:
- [x] 页面显示用户列表（分页）
- [x] 创建用户弹窗: 填写信息 → 提交 → 列表刷新
- [x] 用户名重复 → 表单显示错误
- [x] 编辑用户: 修改角色/状态 → 保存 → 列表刷新
- [x] 删除用户 → 确认 → 删除成功
- [x] 删除自己 → 提示"不能删除自己"
- [x] 非 admin 无法访问（路由守卫已拦截）

---

#### 任务 21: 更新 Sidebar（用户信息 + 退出 + 管理入口）

- [x] **状态: 已完成**

**描述**: 在 Sidebar 底部添加用户信息展示区和管理员导航入口。

**涉及文件**: `frontend/src/components/common/Sidebar.vue`（修改）

**具体步骤**:
1. 引入 `useAuthStore` 和 `useRouter`
2. 在 nav items 后添加"用户管理"（仅 admin 可见，使用 computed）
3. 在 sidebar footer 上方添加用户信息区:
   - 用户头像（用户名首字母大写圆形）
   - 用户名
   - 角色标签（"管理员"绿色 / "只读"灰色）
   - 退出登录按钮
4. `handleLogout()`: 调用 `authStore.logout()` + `router.push('/login')`
5. 添加对应 CSS 样式
6. 需要新增 SVG 图标: `users`（用户管理）和 `logout`（退出）

**验收标准**:
- [x] admin 用户看到"用户管理"导航项
- [x] readonly 用户看不到"用户管理"
- [x] 用户信息区显示用户名和角色标签
- [x] 点击退出 → token 清除 → 跳转登录页
- [x] 其他导航功能正常（对话、连接管理、巡检）

---

#### 任务 22: 更新 App.vue（认证初始化）

- [x] **状态: 已完成**

**描述**: 应用启动时恢复登录态。

**涉及文件**: `frontend/src/App.vue`（修改）

**具体步骤**:
1. `onMounted` 中添加 `authStore.initAuth()`

**验收标准**:
- [x] 有有效 token 时刷新页面 → 保持登录态
- [x] 无 token 时刷新 → 未认证（路由守卫跳转登录）
- [x] 不影响现有的 `themeStore.initTheme()`

---

### Phase 5: 初始化 + 验证

---

#### 任务 23: 创建管理员账号脚本

- [x] **状态: 已完成**

**描述**: 创建 CLI 脚本，交互式创建第一个管理员账号。

**涉及文件**: `backend/scripts/create_admin.py`（新建）

**具体步骤**:
1. 创建 `backend/scripts/` 目录（如不存在）
2. 编写 `create_admin.py`:
   - 交互式输入 username（默认 "admin"）和 password
   - 密码 ≥ 6 字符校验
   - 调用 `hash_password()` 生成 bcrypt 哈希
   - 通过 `async_session_factory` 写入 `users` 表
   - 输出创建成功的用户 ID
3. 确保 `backend/scripts/__init__.py` 存在（可为空）

**验收标准**:
- [x] `python scripts/create_admin.py` 交互式创建管理员
- [x] 密码 < 6 字符时提示错误并退出
- [x] 创建成功后输出 user ID
- [x] 数据库中 `users` 表有对应记录，密码哈希正确

---

## 五、完整验证方案

### 自动化验证

- [x] `cd backend && alembic upgrade head` — users 表创建成功
- [x] `cd backend && pytest tests/ -v` — 所有测试通过
- [x] `cd frontend && npx vue-tsc --noEmit` — TypeScript 类型检查通过
- [x] `cd frontend && npm run build` — 生产构建成功

### 手动功能验证

#### 认证流程
- [x] 访问 `http://localhost:5173` → 跳转 `/login`
- [x] 输入 admin 凭据登录 → 跳转首页，Sidebar 显示用户信息
- [x] 管理员显示"用户管理"导航项
- [x] 点击退出 → token 清除 → 跳转 `/login`
- [x] 刷新页面 → 保持登录态

#### 用户管理流程
- [x] 管理员访问 `/users` → 显示用户列表
- [x] 创建新用户（readonly 角色）→ 创建成功
- [x] 重复用户名创建 → 显示错误
- [x] 编辑用户角色为 admin → 保存成功
- [x] 禁用用户 → 该用户无法登录
- [x] 删除自己 → 提示"不能删除自己"

#### 权限控制流程
- [x] readonly 用户登录 → 执行 `INSERT INTO ...` → SQL 审计拦截
- [x] readonly 用户登录 → 执行 `SELECT ...` → 正常执行
- [x] admin 用户执行 `INSERT INTO ...` → 弹出写操作确认对话框 → 确认后执行
- [x] admin 用户执行 `DROP TABLE ...` → SQL 审计拦截（DDL 始终拦截）

#### API 认证覆盖
- [x] 无 token 调用任何 `/api/connections/*` → 401
- [x] 无 token 调用 `POST /api/chat/stream` → 401
- [x] 无 token 调用 `/api/auth/me` → 401
- [x] 过期 token → 401

---

## 六、风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| JWT_SECRET 泄露 | 攻击者可伪造任意用户 token | 使用 ≥32 字符随机字符串，不提交到 Git |
| 两个 AsyncSession 并发 | auth 和业务逻辑各用一个 session，理论上无冲突 | FastAPI Depends 独立解析，auth session 除 SELECT 外无写操作 |
| 前端 401 竞态 | 多个请求同时 401 导致多次跳转 | `window.location.href` 赋值幂等，浏览器自动去重 |
| SSE 连接中 token 过期 | 已建立的 SSE 连接不受影响 | user_role 在建立连接时提取，流式传输中不变 |
| 现有用户无感知 | 部署后所有请求需要 token | 部署同时创建 admin 账号，提供登录指引 |
