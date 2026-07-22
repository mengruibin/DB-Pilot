# 用户数据隔离方案

## 方案简述

当前系统已上线账号系统（JWT 认证 + `get_current_user` 依赖），但 `connections`、`sessions`、`reports` 三张数据表**缺少 `user_id` 字段**，所有 API 端点返回**全量跨用户数据**而非仅限于当前用户。

本方案在数据库层增加 `user_id` 字段，后端 API 按用户维度做数据隔离：

- **所有用户（含 admin）**：一视同仁，只能看到 `user_id == 自己` 的数据
- **历史遗留数据**：`user_id` 为 NULL，无人可见（需通过后台脚本批量归属）

### 改动范围

| 层级 | 改动 |
|------|------|
| 数据库 | 3 张表新增 `user_id` 列 + 外键（Alembic 迁移） |
| ORM 模型 | 3 个 Model 新增字段 |
| Pydantic Schema | 3 个 Response Schema 新增字段 |
| API 路由 | 全部 CRUD 端点增加 user 过滤（列表）或 ownership 校验（单条） |
| 前端类型 | 3 个 TypeScript 接口补充 `user_id` 字段 |
| 其他 | `SessionManager` 后台任务无需改动 |

---

## 需要改动的接口

### 全量端点清单

| # | 文件 | 端点 | 方法 | 当前问题 |
|---|------|------|------|----------|
| C1 | connection.py | `/api/connections` | GET | 返回全部连接，无 user 过滤 |
| C2 | connection.py | `/api/connections` | POST | 创建时未关联 user_id |
| C3 | connection.py | `/api/connections/{id}` | GET | 无 ownership 校验 |
| C4 | connection.py | `/api/connections/{id}` | PUT | 无 ownership 校验 |
| C5 | connection.py | `/api/connections/{id}` | DELETE | 无 ownership 校验 |
| C6 | connection.py | `/api/connections/{id}/test` | POST | 无 ownership 校验 |
| C7 | connection.py | `/api/connections/{id}/metadata` | GET | 无 ownership 校验 |
| S1 | chat.py | `_get_or_create_session()` | — | 创建会话未关联 user_id |
| S2 | chat.py | `_resolve_connection_config()` | — | 无 ownership 校验 |
| S3 | chat.py | `_stream_events()` | — | 未透传 current_user |
| S4 | chat.py | `/api/chat/stream` | POST | 未透传 current_user |
| S5 | chat.py | `/api/sessions` | GET | 返回全部会话，无 user 过滤 |
| S6 | chat.py | `/api/sessions/{id}/messages` | GET | 无 ownership 校验 |
| S7 | chat.py | `/api/sessions/{id}` | PATCH | 重命名，无 ownership 校验 |
| S8 | chat.py | `/api/sessions/{id}` | DELETE | 无 ownership 校验 |
| R1 | report.py | `/api/connections/{id}/health-check` | POST | **缺 `current_user` 依赖** |
| R2 | report.py | `/api/reports` | GET | **缺 `current_user` 依赖**，无 user 过滤 |
| R3 | report.py | `/api/reports/{id}` | GET | 无 ownership 校验 |
| R4 | report.py | `/api/reports/{id}/export` | GET | **缺 `current_user` 依赖** |
| Q1 | query.py | `_load_and_create_adapter()` | — | 无 ownership 校验 |
| T1 | troubleshoot.py | `_resolve_conn_config()` | — | 无 ownership 校验 |

### 无需改动的端点

| 端点 | 原因 |
|------|------|
| `POST /api/auth/login` | 认证接口，不涉及用户数据 |
| `GET /api/auth/me` | 已返回当前用户信息 |
| `/api/users/*` | 管理员专用，管理系统用户 |
| `POST /api/chat/cancel` | 操作运行中的流任务，非持久数据 |
| `query.py` 三个暴露端点 | 已持有 `current_user`，仅需透传到 `_load_and_create_adapter` |
| `SessionManager` 后台任务 | 扫描全量空闲会话是正确行为 |

---

## 任务执行计划

### 前置任务（1 个）

#### Task 0: 新增辅助函数 `verify_resource_ownership`

**文件**: `backend/app/auth/dependencies.py`

在文件末尾添加通用 ownership 校验函数：

```python
async def verify_resource_ownership(
    resource: Any | None,
    current_user: UserModel,
    resource_name: str = "资源",
) -> None:
    """非 admin 用户只能访问自己的资源；不存在或无权限均返回 404（防信息泄露）。"""
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "NOT_FOUND", "user_message": f"{resource_name} 不存在或已删除"},
        )
    if current_user.role != "admin":
        if getattr(resource, "user_id", None) != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "NOT_FOUND", "user_message": f"{resource_name} 不存在或已删除"},
            )
```

**验收标准**:
- [ ] 函数存在，导入不报错
- [ ] resource=None 时抛 404
- [ ] admin 用户任意 resource 都通过
- [ ] 非 admin 且 user_id 不匹配时抛 404
- [ ] 非 admin 且 user_id 匹配时通过

---

### 第 1 层：数据模型（3 个任务，可并行）

#### Task 1.1: Alembic 迁移

**文件**: 新建 `backend/alembic/versions/007_add_user_id_to_connections_sessions_reports.py`

为三张表各加一列 `user_id`：

| 表名 | 列定义 |
|------|--------|
| `connections` | `user_id VARCHAR(36)`, FK → `users.id` ON DELETE SET NULL, nullable, indexed |
| `sessions` | 同上 |
| `reports` | 同上 |

**执行**:
```bash
cd backend && alembic upgrade head
```

**验收标准**:
- [ ] 迁移文件生成，`alembic upgrade head` 成功
- [ ] `connections` 表有 `user_id` 列（nullable）
- [ ] `sessions` 表有 `user_id` 列（nullable）
- [ ] `reports` 表有 `user_id` 列（nullable）
- [ ] 现有数据不受影响，`user_id` 为 NULL
- [ ] `alembic downgrade -1` 可回滚

---

#### Task 1.2: ORM 模型加字段（3 个文件，可并行）

| 文件 | 加的内容 |
|------|----------|
| `backend/app/models/connection.py` | `ConnectionConfigModel.user_id` |
| `backend/app/models/session.py` | `SessionModel.user_id` |
| `backend/app/models/report.py` | `ReportModel.user_id` |

每个模型加：

```python
user_id: Mapped[str | None] = mapped_column(
    String(36), nullable=True, default=None, index=True,
    comment="创建此连接的用户 ID（NULL = 历史遗留数据）",
)
```

**验收标准**:
- [ ] 三个 Model 均有 `user_id` 字段声明
- [ ] `from_attributes` 序列化正常

---

#### Task 1.3: Pydantic Schema 加字段

**文件**: `backend/app/models/schemas.py`

三个 Response Schema 加字段：

| Schema | 加字段 |
|--------|--------|
| `ConnectionResponse` | `user_id: str \| None = None` |
| `SessionResponse` | `user_id: str \| None = None` |
| `HealthReportResponse` | `user_id: str \| None = None` |

**验收标准**:
- [ ] 三个 Response 的 JSON 响应中出现 `user_id` 字段
- [ ] 历史数据返回 `user_id: null`

---

### 第 2 层：API 逻辑（5 个文件，Task 0 完成后可并行）

#### Task 2.1: connection.py — 7 个端点

**文件**: `backend/app/api/connection.py`

| 端点 | 具体执行 |
|------|----------|
| `GET /api/connections`（列表） | `count_q` 和 `stmt` 在非 admin 时加 `.where(ConnectionConfigModel.user_id == current_user.id)` |
| `POST /api/connections`（创建） | `ConnectionConfigModel(... user_id=current_user.id)` |
| `GET /api/connections/{id}` | 查库后调 `verify_resource_ownership(db_conn, current_user, "连接")` |
| `PUT /api/connections/{id}` | 同上 |
| `DELETE /api/connections/{id}` | 同上 |
| `POST /api/connections/{id}/test` | 同上 |
| `GET /api/connections/{id}/metadata` | 同上 |

**验收标准**:
- [ ] 列表接口：普通用户只看到 `user_id` 匹配自己的记录
- [ ] 列表接口：admin 看到全部记录（含 NULL）
- [ ] 创建接口：新连接自动绑定当前用户的 `user_id`
- [ ] 单条接口：普通用户访问他人连接返回 404（与"不存在"报文一致）
- [ ] 单条接口：admin 可访问任何连接

---

#### Task 2.2: chat.py — 8 个位置

**文件**: `backend/app/api/chat.py`

| 位置 | 具体执行 |
|------|----------|
| `_get_or_create_session()` | 新增 `current_user` 参数；续会话时做 ownership 校验；创建时设 `user_id=current_user.id` |
| `_resolve_connection_config()` | 新增 `current_user` 参数；查连接后做 ownership 校验 |
| `_stream_events()` | 接收 `current_user` 参数，透传给上面两个函数 |
| `chat_stream()` | 将 `current_user` 传给 `_stream_events` |
| `GET /api/sessions`（列表） | 非 admin 时加 `.where(SessionModel.user_id == current_user.id)` |
| `GET /api/sessions/{id}/messages` | 查会话后调 `verify_resource_ownership(session, current_user, "会话")` |
| `PATCH /api/sessions/{id}` | 同上 |
| `DELETE /api/sessions/{id}` | 同上 |

**验收标准**:
- [ ] 新会话自动绑定当前用户 `user_id`
- [ ] 续会话时校验 ownership（他人会话不可续）
- [ ] 会话列表：普通用户只看到自己的会话
- [ ] 会话列表：admin 看到全部会话
- [ ] 消息查看/重命名/删除：校验 ownership
- [ ] SSE 流中连接校验通过后才执行

---

#### Task 2.3: report.py — 4 个端点

**文件**: `backend/app/api/report.py`

| 端点 | 具体执行 |
|------|----------|
| `POST /api/connections/{id}/health-check` | 加 `current_user = Depends(get_current_user)`；透传到 `_health_stream` 和 `_persist_report`；`_load_and_create_adapter` 加 ownership 校验 |
| `GET /api/reports`（列表） | 加 `current_user = Depends(get_current_user)`；非 admin 时加 `.where(ReportModel.user_id == current_user.id)` |
| `GET /api/reports/{id}` | 查报告后调 `verify_resource_ownership(report, current_user, "报告")` |
| `GET /api/reports/{id}/export` | 加 `current_user = Depends(get_current_user)` + ownership 校验 |

**验收标准**:
- [ ] `list_reports` 和 `export_report` 补齐 `current_user` 依赖
- [ ] 健康巡检生成的报告自动绑定 `user_id`
- [ ] 报告列表：普通用户只看到自己的报告
- [ ] 报告列表：admin 看到全部报告
- [ ] 报告详情/导出：校验 ownership

---

#### Task 2.4: query.py — `_load_and_create_adapter`

**文件**: `backend/app/api/query.py`

`_load_and_create_adapter()` 新增 `current_user` 参数，查连接后做 ownership 校验；三个调用端点（`execute_query`、`execute_explain`、`list_slow_queries`）已持有 `current_user`，调用时传入。

**验收标准**:
- [ ] 普通用户无法查询/分析他人连接的数据库
- [ ] admin 可查询任何连接

---

#### Task 2.5: troubleshoot.py — `_resolve_conn_config`

**文件**: `backend/app/api/troubleshoot.py`

`_resolve_conn_config()` 新增 `current_user` 参数，查连接后做 ownership 校验；`troubleshoot_stream` 已持有 `current_user`，调用时传入。

**验收标准**:
- [ ] 普通用户无法对他人连接做故障排查
- [ ] admin 可排查任何连接

---

### 第 3 层：前端（1 个任务）

#### Task 3.1: 前端类型补充

| 文件 | 接口 | 加字段 |
|------|------|--------|
| `frontend/src/types/connection.ts` | `ConnectionConfig` | `user_id?: string` |
| `frontend/src/types/chat.ts` | `Session` | `user_id?: string` |
| `frontend/src/types/report.ts` | `HealthReport` | `user_id?: string` |

仅类型补充，不影响现有消费代码。

**验收标准**:
- [ ] `npm run type-check` 无报错
- [ ] API 返回的 `user_id` 字段能被类型系统正确识别

---

### 第 4 层：测试（1 个任务）

#### Task 4.1: 单元测试

**文件**: 新建 `backend/tests/test_user_data_isolation.py`

测试场景：
1. admin 看到全部数据（含 NULL user_id）
2. 普通用户列表只返回自己的
3. 普通用户无法访问他人数据 → 404
4. 普通用户创建数据自动绑 `user_id`
5. admin 可访问任何数据
6. Session/Report 同上模式

**验收标准**:
- [ ] `pytest backend/tests/test_user_data_isolation.py -v` 全部通过
- [ ] `pytest backend/tests/ -v` 现有测试不受影响

---

## 任务总表

| 任务 ID | 描述 | 文件 | 优先级 | 状态 |
|---------|------|------|--------|------|
| Task 0 | 新增 `verify_resource_ownership` 辅助函数 | `dependencies.py` | P0 | ✅ 已完成 |
| Task 1.1 | Alembic 迁移加 `user_id` 列 | `007_add_user_id_to_connections_sessions_reports.py` | P0 | ✅ 已完成 |
| Task 1.2 | ORM 模型加字段 | `connection.py`, `session.py`, `report.py` | P0 | ✅ 已完成 |
| Task 1.3 | Pydantic Schema 加字段 | `schemas.py` | P0 | ✅ 已完成 |
| Task 2.1 | connection.py 7 个端点 | `connection.py` | P0 | ✅ 已完成 |
| Task 2.2 | chat.py 8 个位置 | `chat.py` | P0 | ✅ 已完成 |
| Task 2.3 | report.py 4 个端点 | `report.py` | P0 | ✅ 已完成 |
| Task 2.4 | query.py 校验 | `query.py` | P0 | ✅ 已完成 |
| Task 2.5 | troubleshoot.py 校验 | `troubleshoot.py` | P0 | ✅ 已完成 |
| Task 3.1 | 前端类型补充 | `connection.ts`, `chat.ts`, `report.ts` | P1 | ✅ 已完成 |
| Task 4.1 | 单元测试 | `test_user_data_isolation.py` | P1 | ✅ 已完成 |

**优先级说明**:
- P0: 核心功能，必须完成
- P1: 增强/保障，建议完成

**状态说明**:
- ⏳ 待开始
- 🔄 进行中
- ✅ 已完成
- ❌ 阻塞

---

## 依赖关系图

```
Task 0（辅助函数）
  ├─ Task 2.1（connection.py）
  ├─ Task 2.2（chat.py）
  ├─ Task 2.3（report.py）
  ├─ Task 2.4（query.py）
  └─ Task 2.5（troubleshoot.py）

Task 1.1（迁移）→ Task 1.2（ORM）→ Task 1.3（Schema）
  └─ 所有 Task 2.x 需要此链路完成

Task 2.x（全部完成）→ Task 4.1（测试）

Task 3.1（前端类型）独立，可随时执行
```

**建议执行顺序**：
1. Task 0 + Task 1.1 + Task 1.2 + Task 1.3（并行）
2. Task 2.1 ~ 2.5（可并行，依赖 Task 0 + 1.x）
3. Task 3.1（可独立执⾏）
4. Task 4.1（依赖 2.x 全部完成）

---

## 回滚方案

若需回滚全部改动：

```bash
# 数据库回滚
cd backend && alembic downgrade -1

# 代码回滚
git revert HEAD --no-edit  # 假设单次提交
```

## 注意事项

1. **两个 `_load_and_create_adapter` 副本**：`report.py` 和 `query.py` 各自有一份独立实现，都需要修改
2. **NULL vs ""**：`user_id` 为空用 `None`（NULL），不用空字符串
3. **404 报文一致性**：所有权校验与"不存在"用相同错误报文，防信息泄露（`verify_resource_ownership` 已保障）
