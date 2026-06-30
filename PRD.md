# DB-Pilot 产品需求文档 (PRD)

> 数据库运维智能助手 · AI-Powered Database Operations Assistant  
> 版本：v1.0 | 日期：2026-06-30 | 状态：规划中

---

## 目录

1. [产品概述](#1-产品概述)
2. [目标用户](#2-目标用户)
3. [核心功能](#3-核心功能)
4. [系统架构](#4-系统架构)
5. [功能详述](#5-功能详述)
6. [技术方案](#6-技术方案)
7. [项目结构](#7-项目结构)
8. [安全设计](#8-安全设计)
9. [实施路线图](#9-实施路线图)
10. [非功能需求](#10-非功能需求)

---

## 1. 产品概述

### 1.1 产品定位

DB-Pilot 是一个基于大语言模型（LLM）的**数据库运维智能助手**。它以 Web 应用形态提供自然语言交互界面，帮助 DBA、开发者和运维人员高效完成数据库查询、性能诊断、故障排查和健康巡检等日常工作。

### 1.2 核心价值

| 痛点 | DB-Pilot 解决方案 |
|------|------------------|
| SQL 编写耗时，业务人员不会写 | 自然语言输入，自动生成并执行 SQL |
| 性能问题定位困难 | AI 自动分析慢查询、执行计划，给出优化建议 |
| 故障排查依赖经验 | 内置故障诊断知识库，引导式排查 |
| 巡检报告编写繁琐 | 一键生成多维度健康报告 |
| 多种数据库切换心智负担大 | 统一自然语言界面，底层适配多种数据库 |

### 1.3 产品愿景

> 让每个开发者都拥有一个 7×24 小时在线的 DBA 助手。

---

## 2. 目标用户

### 2.1 用户画像

| 角色 | 特征 | 核心使用场景 |
|------|------|-------------|
| **初级 DBA** | 1-3 年经验，需要指导 | 故障排查、优化建议、学习 |
| **资深 DBA** | 5 年+ 经验，需要提效 | 批量巡检、报告生成、复杂诊断 |
| **后端开发者** | 日常需要写 SQL | 自然语言查询、SQL 优化 |
| **运维工程师** | 负责数据库可用性 | 健康巡检、告警排查 |
| **数据分析师** | 需要查数据但 SQL 不熟练 | 自然语言查询 |

### 2.2 用户场景

- **场景 A**：开发者想知道"最近注册的用户有多少"，但不想离开 IDE 去翻表结构写 SQL → 打开 DB-Pilot，自然语言提问，秒得结果
- **场景 B**：DBA 收到"数据库很慢"的告警 → 打开 DB-Pilot，一键诊断，自动定位到缺少索引的慢查询，给出 ALTER TABLE 建议
- **场景 C**：运维每天需要巡检 10 个数据库实例 → 配置连接后一键巡检，生成统一报告

---

## 3. 核心功能

### 3.1 功能全景

```
DB-Pilot
├── 🔍 自然语言数据查询 (NL2SQL)
│   ├── 自然语言 → SQL 转换
│   ├── Schema 上下文自动注入
│   ├── SQL 安全审计 & 执行
│   └── 结果可视化（表格/图表）
│
├── 🩺 SQL 智能诊断与优化
│   ├── 慢查询分析
│   ├── 执行计划解读
│   ├── 索引建议
│   ├── SQL 改写建议
│   └── 配置参数优化建议
│
├── 🛠️ 故障自动排查
│   ├── 死锁检测与分析
│   ├── 连接池耗尽诊断
│   ├── 主从复制延迟排查
│   ├── 表空间不足告警
│   └── 性能尖峰根因分析
│
├── 📊 数据库健康巡检
│   ├── 20+ 维度健康检查
│   ├── 健康评分
│   ├── 可视化报告
│   └── 报告导出（PDF/HTML）
│
└── 🔌 多数据库管理
    ├── 连接配置管理
    ├── MySQL / PostgreSQL / Oracle 适配
    └── 多实例切换
```

### 3.2 功能优先级

| 优先级 | 功能模块 | 说明 |
|--------|---------|------|
| P0 | 自然语言查询 | MVP 核心，最直观的价值体现 |
| P0 | 数据库连接管理 | 所有功能的前置依赖 |
| P1 | SQL 安全审计 | 对生产环境至关重要 |
| P1 | 慢查询诊断 | DBA 最高频工作场景 |
| P2 | 执行计划分析 | 性能优化的核心能力 |
| P2 | 健康巡检报告 | 批量运维的刚需 |
| P2 | 故障排查向导 | 降低故障处理门槛 |
| P3 | 索引自动建议 | 需要深厚的规则积累 |
| P3 | 报告导出 | 锦上添花 |

---

## 4. 系统架构

### 4.1 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                      Browser (Vue 3)                     │
│  ┌───────────┐ ┌───────────┐ ┌───────────────────────┐  │
│  │  Chat UI  │ │ Dashboard │ │  Health Report View   │  │
│  │ (对话交互) │ │ (仪表盘)   │ │  (健康报告)           │  │
│  └─────┬─────┘ └─────┬─────┘ └───────────┬───────────┘  │
│        └──────────────┼─────────────────┘               │
│                       │ SSE Stream                      │
└───────────────────────┼────────────────────────────────┘
                        │
┌───────────────────────┼────────────────────────────────┐
│                  FastAPI Backend                         │
│  ┌────────────────────┴──────────────────────────┐     │
│  │              LangGraph Agent Loop               │     │
│  │                                                 │     │
│  │  用户消息 → Intent Router → 选择 Engine         │     │
│  │       ↓                                         │     │
│  │  ┌────┐    ┌──────────┐    ┌──────────┐        │     │
│  │  │NL→ │    │Diagnosis │    │Health    │        │     │
│  │  │SQL │    │Engine    │    │Check     │        │     │
│  │  │Eng │    │          │    │Engine    │        │     │
│  │  └──┬─┘    └────┬─────┘    └────┬─────┘        │     │
│  │     └───────────┼──────────────┘               │     │
│  │          ┌──────┴──────┐                        │     │
│  │          │  Tool Layer │ ← @tool 装饰器注册      │     │
│  │          └──────┬──────┘                        │     │
│  └─────────────────┼───────────────────────────────┘     │
│           ┌────────┴────────┐                            │
│           │  DB Adapter     │                            │
│           │  ┌──────┬──────┬──────┐                      │
│           │  │MySQL │  PG  │Oracle│                      │
│           │  └──────┴──────┴──────┘                      │
│           └─────────────────────┘                        │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Agent 工作流程

```
Step 1: 用户输入 "最近一小时慢查询有哪些？"

Step 2: Intent Router 识别 → 意图: DIAGNOSIS, 实体: slow_query, time_range: 1h

Step 3: Router 路由到 DiagnosisEngine

Step 4: Agent 调用工具:
   → get_slow_queries(time_range="1h")  [查询 MySQL slow_query_log]
   → explain_sql(sql="...")              [获取执行计划]
   → analyze_indexes(table="...")        [检查索引情况]

Step 5: LLM 综合分析工具返回值，生成诊断结论和建议

Step 6: SSE 流式返回结果给前端
```

---

## 5. 功能详述

### 5.1 自然语言数据查询（NL2SQL）

**输入**：用户的自然语言问题  
**输出**：生成的 SQL、执行结果、数据可视化

**流程**：
1. 用户输入自然语言查询意图
2. 系统从目标数据库获取 Schema 上下文（表名、字段名、注释）
3. 将 Schema + 用户问题组装为 Prompt，发送给 LLM
4. LLM 生成 SQL
5. **SQL 安全审计**：语法解析、危险操作检测、写操作拦截
6. 执行 SQL（默认只读）
7. 结果渲染为表格，支持排序/筛选/导出

**示例**：
```
用户: "查一下上个月订单金额最高的10个用户"
Agent: 
  SELECT u.username, SUM(o.amount) as total
  FROM users u JOIN orders o ON u.id = o.user_id
  WHERE o.created_at >= '2026-05-01' AND o.created_at < '2026-06-01'
  GROUP BY u.id, u.username
  ORDER BY total DESC
  LIMIT 10;
```

### 5.2 SQL 智能诊断与优化

**功能列表**：

| 子功能 | 说明 | 输入 | 输出 |
|--------|------|------|------|
| 慢查询分析 | 获取并分析慢查询日志 | 时间范围/条数 | 慢查询列表 + 分析 |
| 执行计划解读 | 解析 EXPLAIN 输出 | SQL 语句 | 可读的优化建议 |
| 索引建议 | 分析缺失索引 | 表名/SQL | 建议的 CREATE INDEX |
| SQL 改写 | 优化 SQL 写法 | 原始 SQL | 优化后的 SQL + 说明 |
| 配置审查 | 检查关键参数 | 实例 | 参数调整建议 |

**示例交互**：
```
用户: "帮我看看 order_details 表有没有索引问题"
Agent:
  1. 查询表结构和现有索引
  2. 分析最近针对该表的慢查询
  3. 诊断发现：product_id 字段缺少索引，导致 JOIN 查询全表扫描
  4. 建议：CREATE INDEX idx_od_product_id ON order_details(product_id);
  5. 预估改善：查询时间从 2.3s 降至 <50ms
```

### 5.3 故障自动排查

**故障类型覆盖**：

| 故障类型 | 检测方式 | 排查步骤 |
|----------|---------|---------|
| **死锁** | `SHOW ENGINE INNODB STATUS` / `pg_locks` | 1. 识别死锁事务 2. 分析锁等待链 3. 建议终止策略 |
| **连接池耗尽** | `SHOW PROCESSLIST` / `pg_stat_activity` | 1. 统计活跃连接 2. 找出长时间运行查询 3. 分析连接泄漏 |
| **主从延迟** | `SHOW SLAVE STATUS` / `pg_stat_replication` | 1. 检查延迟秒数 2. 分析延迟原因 3. 建议处理方案 |
| **磁盘空间不足** | 查询表空间/数据文件大小 | 1. 定位大表/大日志 2. 建议清理策略 |
| **CPU 飙升** | `SHOW PROCESSLIST` + 慢查询分析 | 1. 定位高消耗 SQL 2. 分析执行计划 3. 优化建议 |

**示例交互**：
```
用户: "数据库突然很慢"
Agent:
  🔍 开始排查...
  
  1️⃣ 检查连接数 → 正常 (45/200)
  2️⃣ 检查锁等待 → ⚠️ 发现 3 个锁等待
  3️⃣ 分析锁等待链 → 事务 #82491 持有排他锁，阻塞了 2 个 UPDATE
  4️⃣ 分析阻塞事务 → 该事务已运行 8 分钟未提交
  
  📋 结论：事务 #82491 长时间未提交导致锁等待
  💡 建议：检查该事务状态，必要时 KILL 82491;
```

### 5.4 数据库健康巡检

**检查维度（20+ 项）**：

| 类别 | 检查项 | 说明 |
|------|--------|------|
| **连接** | 连接数使用率 | 当前/最大连接数 |
| **连接** | 连接异常比例 | 失败连接/总连接 |
| **存储** | 磁盘使用率 | 数据目录所在磁盘 |
| **存储** | 表空间使用率 | 各表空间大小 |
| **性能** | 慢查询占比 | 慢查询数/总查询数 |
| **性能** | 缓冲池命中率 | InnoDB Buffer Pool / PG Buffer Cache |
| **性能** | QPS/TPS | 当前查询/事务吞吐 |
| **复制** | 主从延迟 | Seconds_Behind_Master |
| **复制** | IO/SQL 线程状态 | 是否正常运行 |
| **锁** | 锁等待数量 | 当前活跃锁等待 |
| **锁** | 死锁频率 | 单位时间死锁次数 |
| **备份** | 最近备份时间 | 超过阈值告警 |
| **安全** | 匿名用户检测 | 是否存在无密码用户 |
| **安全** | 弱密码检测 | 简单密码用户 |
| **表** | 无主键表 | 可能导致复制问题 |
| **表** | 碎片化率 | 需要 OPTIMIZE 的表 |
| **日志** | 错误日志异常 | 最近错误/警告数量 |
| **配置** | 关键参数审查 | buffer_pool_size, max_connections 等 |

**报告输出**：
- 综合健康评分（0-100）
- 按严重程度分级（🔴严重 🟡警告 🟢正常）
- 每个异常项的详细建议

---

## 6. 技术方案

### 6.1 技术栈选型

| 层 | 选择 | 版本 | 选型理由 |
|---|------|------|---------|
| **后端框架** | FastAPI | 0.115+ | 原生异步、自动 OpenAPI、SSE 支持好 |
| **Agent 框架** | LangGraph | 0.3+ | 状态图定义 Agent 流程，比 LangChain AgentExecutor 更可控 |
| **LLM** | Claude API / OpenAI | - | 默认 Claude Opus 4.5（复杂推理）+ Claude Haiku 4.5（简单分类） |
| **数据库 ORM** | SQLAlchemy | 2.0+ | Python 生态标准，异步支持，方言体系完善 |
| **MySQL 驱动** | PyMySQL / aiomysql | - | 纯 Python 异步 |
| **PG 驱动** | asyncpg | - | 最快的 Python PG 驱动 |
| **Oracle 驱动** | oracledb | 2.0+ | Oracle 官方 Python 驱动 |
| **SQL 解析** | sqlglot | 25+ | 多方言 SQL 解析器，用于安全审计 |
| **前端框架** | Vue 3 | 3.5+ | Composition API + TypeScript |
| **UI 组件库** | Naive UI | 2.40+ | Tree-shaking，数据密集型组件丰富 |
| **代码编辑器** | Monaco Editor | - | SQL 高亮和编辑 |
| **图表** | ECharts | 5.5+ | 仪表盘和指标趋势图 |
| **构建工具** | Vite | 6+ | 快速 HMR |
| **状态管理** | Pinia | 2+ | Vue 3 官方推荐 |

### 6.2 数据库适配器设计

```python
class BaseAdapter(ABC):
    """所有数据库适配器的抽象基类"""

    # --- 连接管理 ---
    @abstractmethod
    async def connect(self, config: ConnectionConfig) -> bool: ...
    @abstractmethod
    async def disconnect(self): ...
    @abstractmethod
    async def test_connection(self) -> bool: ...
    @abstractmethod
    async def execute(self, sql: str, params: dict = None) -> QueryResult: ...

    # --- 元数据 ---
    @abstractmethod
    async def get_databases(self) -> list[str]: ...
    @abstractmethod
    async def get_tables(self, database: str) -> list[TableInfo]: ...
    @abstractmethod
    async def get_columns(self, database: str, table: str) -> list[ColumnInfo]: ...
    @abstractmethod
    async def get_indexes(self, database: str, table: str) -> list[IndexInfo]: ...

    # --- 诊断 ---
    @abstractmethod
    async def get_slow_queries(self, limit: int, time_range: str) -> list[SlowQuery]: ...
    @abstractmethod
    async def explain(self, sql: str) -> ExplainResult: ...
    @abstractmethod
    async def get_connections_status(self) -> ConnectionStatus: ...
    @abstractmethod
    async def get_lock_info(self) -> list[LockInfo]: ...
    @abstractmethod
    async def get_replication_status(self) -> ReplicationStatus: ...

    # --- 指标 ---
    @abstractmethod
    async def get_metrics(self) -> dict[str, Any]: ...

    # --- 能力声明 ---
    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities: ...
```

```python
class AdapterCapabilities:
    """声明适配器支持哪些功能（不同数据库能力不同）"""
    supports_explain: bool = True
    supports_slow_query_log: bool = True
    supports_replication: bool = False
    supports_table_spaces: bool = False
    supports_json_type: bool = False
    # ...
```

### 6.3 Agent 设计

**工具注册表**：

| 工具名 | 描述 | 对应适配器方法 |
|--------|------|---------------|
| `list_tables` | 列出数据库中的表 | `get_tables()` |
| `describe_table` | 获取表结构和索引 | `get_columns()` + `get_indexes()` |
| `run_query` | 执行只读 SQL 查询 | `execute()` |
| `explain_query` | 获取 SQL 执行计划 | `explain()` |
| `get_slow_queries` | 获取慢查询列表 | `get_slow_queries()` |
| `check_connections` | 检查连接状态 | `get_connections_status()` |
| `check_locks` | 检查锁等待 | `get_lock_info()` |
| `check_replication` | 检查复制状态 | `get_replication_status()` |
| `get_metrics` | 获取性能指标 | `get_metrics()` |

**意图路由**：
```
用户输入
    │
    ▼
┌──────────────┐
│ 快速规则匹配  │ ← 关键词正则（慢查询、锁、死锁、连接数……）
└──────┬───────┘
       │ 低置信度
       ▼
┌──────────────┐
│  LLM 分类器  │ ← Haiku 轻量模型，快速分类
└──────┬───────┘
       │
       ▼
  ┌─────────┐
  │ Intent  │ = {QUERY, DIAGNOSIS, TROUBLESHOOT, HEALTH_CHECK, GENERAL}
  └────┬────┘
       │
       ▼
  对应 Engine 处理
```

---

## 7. 项目结构

```
DB-Pilot/
├── README.md
├── PRD.md                          # 本文档
├── docker-compose.yml              # 一键部署（Phase 5）
│
├── backend/
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── alembic.ini                 # 内部 SQLite 数据库迁移
│   ├── app/
│   │   ├── main.py                 # FastAPI 应用入口
│   │   ├── config.py               # 配置管理（环境变量 + .env）
│   │   │
│   │   ├── api/                    # REST API 路由
│   │   │   ├── __init__.py
│   │   │   ├── chat.py             # POST /api/chat/stream (SSE)
│   │   │   ├── connection.py       # CRUD /api/connections
│   │   │   └── report.py           # GET /api/reports
│   │   │
│   │   ├── agent/                  # LangGraph Agent
│   │   │   ├── __init__.py
│   │   │   ├── graph.py            # StateGraph 定义
│   │   │   ├── router.py           # 意图路由器
│   │   │   ├── state.py            # AgentState 定义
│   │   │   └── tools/
│   │   │       ├── __init__.py
│   │   │       ├── query.py        # list_tables, describe_table, run_query
│   │   │       ├── diagnosis.py    # explain_query, get_slow_queries
│   │   │       ├── health.py       # 健康巡检工具集
│   │   │       └── troubleshoot.py # check_locks, check_connections, check_replication
│   │   │
│   │   ├── db/                     # 数据库适配器
│   │   │   ├── __init__.py
│   │   │   ├── base.py             # BaseAdapter 抽象类
│   │   │   ├── mysql.py            # MySQL 适配器
│   │   │   ├── postgresql.py       # PostgreSQL 适配器
│   │   │   ├── oracle.py           # Oracle 适配器
│   │   │   └── factory.py          # 适配器工厂
│   │   │
│   │   ├── engine/                 # 核心引擎
│   │   │   ├── __init__.py
│   │   │   ├── nl2sql.py           # 自然语言 → SQL
│   │   │   ├── sql_auditor.py      # SQL 安全审计
│   │   │   ├── diagnosis.py        # 诊断引擎
│   │   │   └── health_check.py     # 健康巡检引擎
│   │   │
│   │   ├── models/                 # 数据模型
│   │   │   ├── __init__.py
│   │   │   ├── schemas.py          # Pydantic 请求/响应模型
│   │   │   ├── session.py          # 会话模型（ORM）
│   │   │   └── connection.py       # 连接配置模型（ORM）
│   │   │
│   │   └── prompts/                # Prompt 模板
│   │       ├── __init__.py
│   │       ├── nl2sql.py
│   │       ├── diagnosis.py
│   │       ├── troubleshoot.py
│   │       └── health.py
│   │
│   └── tests/
│       ├── test_nl2sql.py
│       ├── test_diagnosis.py
│       ├── test_adapter_mysql.py
│       └── test_sql_auditor.py
│
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    ├── src/
    │   ├── App.vue
    │   ├── main.ts
    │   ├── router.ts
    │   │
    │   ├── views/
    │   │   ├── ChatView.vue           # 对话主界面（默认页面）
    │   │   ├── ConnectionView.vue     # 连接管理页
    │   │   └── ReportView.vue         # 健康报告页
    │   │
    │   ├── components/
    │   │   ├── chat/
    │   │   │   ├── ChatPanel.vue      # 对话面板容器
    │   │   │   ├── MessageList.vue    # 消息列表
    │   │   │   ├── MessageBubble.vue  # 单条消息气泡
    │   │   │   ├── InputArea.vue      # 输入区域
    │   │   │   └── ThinkingIndicator.vue # Agent 思考中动画
    │   │   │
    │   │   ├── sql/
    │   │   │   ├── SqlBlock.vue       # SQL 代码块（高亮 + 复制 + 执行按钮）
    │   │   │   └── ResultTable.vue    # 查询结果表格
    │   │   │
    │   │   ├── connection/
    │   │   │   ├── ConnectionForm.vue # 连接配置表单
    │   │   │   └── ConnectionList.vue # 连接列表
    │   │   │
    │   │   ├── report/
    │   │   │   ├── HealthScore.vue    # 健康评分环形图
    │   │   │   ├── CheckItemList.vue  # 检查项列表
    │   │   │   └── MetricChart.vue    # 指标折线图
    │   │   │
    │   │   └── common/
    │   │       ├── AppLayout.vue      # 全局布局
    │   │       └── Sidebar.vue        # 侧边导航
    │   │
    │   ├── composables/               # 组合式函数
    │   │   ├── useChat.ts             # 对话逻辑
    │   │   ├── useSSE.ts              # SSE 流式连接
    │   │   └── useConnection.ts       # 连接管理
    │   │
    │   ├── stores/
    │   │   ├── chat.ts                # 对话状态
    │   │   ├── connection.ts          # 连接状态
    │   │   └── settings.ts            # 设置状态
    │   │
    │   ├── api/
    │   │   ├── client.ts              # Axios/Fetch 封装
    │   │   ├── chat.ts                # 对话 API
    │   │   └── connection.ts          # 连接 API
    │   │
    │   └── types/                     # TypeScript 类型定义
    │       ├── chat.ts
    │       ├── connection.ts
    │       └── report.ts
    │
    └── public/
        └── favicon.svg
```

---

## 8. 安全设计

### 8.1 SQL 安全五层模型

```
Layer 1: 默认只读
   ↓   自动添加 /* READONLY */ 标记，由适配器层强制执行
Layer 2: 语法解析
   ↓   sqlglot 解析 SQL AST，检测 DROP/DELETE/UPDATE/ALTER/TRUNCATE
Layer 3: 用户确认
   ↓   写操作弹出二次确认对话框，说明影响范围
Layer 4: 执行保护
   ↓   statement_timeout / max_execution_time 上限（默认 30s）
Layer 5: 事务回滚
        写操作自动包裹在事务中，异常即 ROLLBACK
```

### 8.2 连接凭据安全

- 连接凭据仅存储在浏览器 `localStorage` + 服务端内存会话中
- 支持 SSL/TLS 加密连接
- 连接密码不在日志中输出
- 会话超时自动清理（默认 30 分钟无操作）

### 8.3 权限模型

| 角色 | 权限 |
|------|------|
| **只读用户** | 仅可执行 SELECT，查看诊断结果 |
| **标准用户** | 可执行 SELECT + 诊断 + 巡检 |
| **管理员** | 可执行写操作（需二次确认），管理连接配置 |

---

## 9. 实施路线图

### Phase 1：项目脚手架 + 基础对话（预计 2 周）

**目标**：搭建前后端框架，实现最基础的自然语言查询能力

**后端**：
- FastAPI 项目初始化，配置管理
- LangGraph Agent 骨架（单节点测试回路）
- MySQL 适配器（基础方法：connect, execute, get_tables, get_columns）
- 基础 `query_database` 工具
- SSE 流式对话 API

**前端**：
- Vue 3 + Vite 项目初始化
- Naive UI 集成
- ChatPanel 骨架（输入框 + 消息列表 + SSE 流式接收）
- 连接管理表单

**验证标准**：连接本地 MySQL，输入"显示所有表"，Agent 返回表列表

---

### Phase 2：NL→SQL 引擎 + 安全审计（预计 1 周）

**目标**：完善自然语言→SQL 转换，建立安全防线

**后端**：
- NL2SQL 引擎（Schema 注入 + Prompt 工程）
- sqlglot SQL 解析集成
- SQL 安全审计五层模型
- PostgreSQL 适配器
- 适配器工厂模式

**前端**：
- SqlBlock 组件（Monaco Editor 高亮）
- ResultTable 组件（虚拟滚动表格）
- 写操作二次确认弹窗
- 连接切换功能

**验证标准**：输入"查询最近30天注册的用户"，安全生成并执行 SQL

---

### Phase 3：诊断引擎（预计 1.5 周）

**目标**：实现慢查询分析和执行计划诊断

**后端**：
- 慢查询获取 + 分析工具
- 执行计划解析（EXPLAIN → 可读建议）
- 索引分析 + 建议生成
- 诊断报告 Prompt 模板

**前端**：
- 诊断结果卡片组件
- 执行计划可视化（树形结构）
- 索引建议展示

**验证标准**：针对有性能问题的测试库，Agent 正确诊断慢查询并给出索引建议

---

### Phase 4：健康巡检 + 故障排查（预计 1.5 周）

**目标**：批量巡检 + 常见故障自动排查

**后端**：
- 健康巡检引擎（20+ 检查项）
- 故障排查工作流（死锁/连接/复制）
- Oracle 适配器（基础能力）
- 巡检报告生成

**前端**：
- 健康评分环形图
- 检查项分级列表（🔴🟡🟢）
- 报告详情页

**验证标准**：一键巡检返回完整报告，涵盖连接/锁/复制/慢查询等维度

---

### Phase 5：打磨与部署（预计 1 周）

**目标**：生产可用性提升

- Docker Compose 一键部署
- 报告 PDF/HTML 导出
- 前端交互优化（键盘快捷键、暗色模式）
- 错误处理与用户提示完善
- 使用文档

---

## 10. 非功能需求

### 10.1 性能

| 指标 | 目标 |
|------|------|
| 首屏加载 | < 2s |
| SQL 查询响应（简单） | < 500ms |
| Agent 思考延迟（不含 LLM） | < 200ms |
| SSE 流式首 Token 延迟 | < 2s |
| 巡检报告生成（20 项） | < 30s |

### 10.2 可用性

- 支持中英文自然语言输入
- 错误信息友好可读（非原始异常栈）
- 连接测试提供明确的成功/失败反馈
- 所有操作可撤销或需二次确认

### 10.3 兼容性

- 浏览器：Chrome 90+, Edge 90+, Firefox 90+
- 数据库：MySQL 5.7/8.0+, PostgreSQL 12+, Oracle 19c+
- LLM：Claude API（默认），兼容 OpenAI 格式接口

### 10.4 可扩展性

- 新增数据库适配器 < 300 行代码
- 新增 Agent 工具：添加 `@tool` 装饰器即可
- Prompt 模板可热更新（不重启服务）

---

> 📌 **下一步**：按 Phase 1 启动开发，优先搭建前后端脚手架并跑通"自然语言查询数据库"的完整链路。
