# 智能 OnCall Agent V1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一套可在 macOS + Podman 上运行的智能 OnCall MVP，跑通“发布异常告警—多 Agent 调查—SOP 检索—人工审批—真实回滚—恢复验证”的完整链路。

**Architecture:** 外层 LangGraph 维护确定性 Incident 生命周期，调查子图由 Supervisor 协调 Investigation Agent 和 Knowledge Agent，确诊后独立调用 Remediation Planner。所有外部查询通过窄接口 MCP 完成，恢复动作由 Policy Engine、人工审批和宿主机 Recovery MCP 共同约束。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLAlchemy、Alembic、PostgreSQL、Celery、Redis、LangGraph、LangChain、阿里云百炼 OpenAI-compatible API、Milvus 2.5.27、MCP、React、TypeScript、Vite、Podman 5.8.3、Prometheus、Alertmanager。

**Design Reference:** 实施前先阅读 `docs/superpowers/specs/2026-07-20-intelligent-oncall-agent-design.md`。架构取舍、备选方案和选型理由以该文档为准；实施中出现新的技术选型时，先补充“可选方案、最终选择、选择理由和代价”，再编码。

## 全局约束

- V1 只支持 `demo-shop` 一个项目和“发布后错误率升高”一个主要故障场景。
- V1 不实现聊天、长期记忆、经验自动沉淀、Skill 自动修改、Kubernetes、Kafka和多租户。
- 运行环境只依赖 Podman，不编写 Docker CLI 专属脚本。
- Agent 不能直接调用 Recovery MCP；写操作只能由 Recovery Executor 调用。
- 所有 Agent 输出必须通过 Pydantic Schema 校验。
- Recovery MCP 禁止接收 Shell、SQL、Python、Podman 参数或任意文件路径。
- 回滚前必须验证 ActionPlan 哈希、审批用户角色、当前版本、目标版本和幂等键。
- V1 按用户要求功能优先，不建设完整自动化测试体系；每个任务使用导入检查、健康检查或人工冒烟验证。
- 不修改或提交仓库根目录现有的 `main.py`，除非用户之后明确授权。
- 每个任务只提交该任务列出的文件，不顺带重构无关模块。

---

## 文件结构总览

```text
oncall/
├── pyproject.toml
├── .env.example
├── Makefile
├── compose.yaml
├── containers/
│   ├── api.Containerfile
│   ├── frontend.Containerfile
│   └── demo.Containerfile
├── migrations/
├── src/oncall/
│   ├── api/
│   ├── auth/
│   ├── alerts/
│   ├── incidents/
│   ├── graph/
│   ├── agents/
│   ├── rag/
│   ├── skills/
│   ├── mcp_gateway/
│   ├── policy/
│   ├── execution/
│   ├── audit/
│   ├── cli/
│   ├── config.py
│   ├── database.py
│   └── logging.py
├── mcp_servers/
│   ├── observability/
│   ├── release/
│   └── recovery/
├── demo/
│   ├── app/
│   ├── traffic/
│   ├── prometheus/
│   └── alertmanager/
├── project-packs/demo-shop/
│   ├── project.yaml
│   ├── alert-mappings.yaml
│   ├── policies.yaml
│   ├── knowledge/
│   └── skills/
├── frontend/
└── docs/runbooks/
```

## Task 1：建立 Python 工程、配置系统和 API 骨架

**Files:**

- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `Makefile`
- Create: `src/oncall/__init__.py`
- Create: `src/oncall/config.py`
- Create: `src/oncall/logging.py`
- Create: `src/oncall/api/__init__.py`
- Create: `src/oncall/api/main.py`

**Interfaces:**

- Produces: `get_settings() -> Settings`
- Produces: `configure_logging() -> None`
- Produces: `create_app() -> FastAPI`
- Produces: `GET /health/live` and `GET /health/ready`

- [ ] **Step 1：定义项目依赖和命令入口**

`pyproject.toml` 使用 `src` 布局，声明 Python `>=3.11,<3.13`，并加入 FastAPI、Uvicorn、Pydantic Settings、SQLAlchemy、asyncpg、Alembic、Celery Redis、LangGraph、LangGraph PostgreSQL Checkpointer、LangChain OpenAI、PyMilvus、MCP、argon2-cffi、PyJWT、httpx、structlog、prometheus-client、Typer 和 PyYAML。

CLI 入口固定为：

```toml
[project.scripts]
oncall = "oncall.cli.app:app"
```

- [ ] **Step 2：建立强类型配置**

`src/oncall/config.py` 至少定义：

```python
class Settings(BaseSettings):
    app_env: Literal["local", "production"] = "local"
    database_url: str
    redis_url: str
    jwt_secret: SecretStr
    bailian_api_key: SecretStr
    bailian_base_url: str
    bailian_chat_model: str
    bailian_embedding_model: str
    milvus_uri: str
    recovery_mcp_url: str
    recovery_mcp_secret: SecretStr
```

使用 `@lru_cache` 暴露 `get_settings()`，禁止业务模块直接读取环境变量。

- [ ] **Step 3：建立日志和 FastAPI 工厂**

`configure_logging()` 输出 JSON，并自动附加 `request_id`。`create_app()` 注册生命周期事件、Prometheus 指标中间件和健康检查。

健康响应固定为：

```json
{"status":"ok","service":"oncall-api"}
```

- [ ] **Step 4：准备本地环境文件**

`.env.example` 列出所有配置但不包含真实密钥。`Makefile` 至少提供：

```make
venv:
	python3 -m venv .venv

install:
	.venv/bin/pip install -e .

api:
	.venv/bin/uvicorn oncall.api.main:create_app --factory --reload --port 8000
```

- [ ] **Step 5：执行冒烟验证**

Run:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -c "from oncall.api.main import create_app; assert create_app().title"
```

Expected: 命令退出码为0。

- [ ] **Step 6：提交**

```bash
git add pyproject.toml .env.example Makefile src/oncall
git commit -m "feat: bootstrap oncall backend"
```

## Task 2：建立 Podman Compose 基础设施和演示服务

**Files:**

- Create: `compose.yaml`
- Create: `containers/api.Containerfile`
- Create: `containers/demo.Containerfile`
- Create: `demo/app/main.py`
- Create: `demo/traffic/main.py`
- Create: `demo/prometheus/prometheus.yml`
- Create: `demo/prometheus/alerts.yml`
- Create: `demo/alertmanager/alertmanager.yml`

**Interfaces:**

- Produces: demo `GET /health`, `GET /version`, `GET /api/orders`
- Produces: Prometheus `demo_http_requests_total{status,version}`
- Produces: Alertmanager Webhook to `POST /api/v1/webhooks/alertmanager/demo-shop`

- [ ] **Step 1：实现可切换版本的演示服务**

`DEMO_VERSION=v1` 时 `/api/orders` 返回200；`DEMO_VERSION=v2` 时按 `DEMO_FAILURE_RATE` 返回500。每次请求增加 Prometheus Counter，并让 `/version` 返回：

```json
{"service":"order-api","version":"v1"}
```

- [ ] **Step 2：实现稳定流量生成器**

流量进程每秒请求一次 `/api/orders`，超时2秒，错误只记录日志，不终止进程。

- [ ] **Step 3：配置监控和告警**

Prometheus 每5秒抓取 demo 服务。`HighErrorRate` 在两分钟窗口错误率超过30%并持续30秒后触发，labels 必须包含：

```yaml
project_id: demo-shop
environment: staging
service: order-api
severity: critical
correlation_key: release-regression
```

- [ ] **Step 4：编写标准 Compose 文件**

Compose 至少包含 PostgreSQL、Redis、Milvus 2.5.27、etcd、MinIO、Prometheus、Alertmanager、demo-service 和 traffic-generator。固定容器名使用 `oncall-postgres`、`oncall-redis`、`oncall-milvus`、`oncall-etcd`、`oncall-minio`、`oncall-prometheus`、`oncall-alertmanager`、`oncall-demo-service` 和 `oncall-traffic`。所有宿主机端口绑定 `127.0.0.1`。

Milvus 使用官方2.5系列 Standalone 依赖关系，不使用3.0 beta。

- [ ] **Step 5：执行 Podman 冒烟验证**

先运行 `podman machine list`；仅当 Machine 为 stopped 时执行 `podman machine start`，已运行时跳过该命令。

Run:

```bash
podman compose up -d postgres redis etcd minio milvus demo-service prometheus alertmanager traffic-generator
podman compose ps
curl http://127.0.0.1:18080/version
```

Expected: 依赖服务处于 running/healthy，版本响应为 `v1`。

- [ ] **Step 6：提交**

```bash
git add compose.yaml containers demo
git commit -m "feat: add podman demo observability stack"
```

## Task 3：建立数据库模型、迁移和审计事件

**Files:**

- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial.py`
- Create: `src/oncall/database.py`
- Create: `src/oncall/models.py`
- Create: `src/oncall/audit/service.py`
- Create: `src/oncall/incidents/state.py`

**Interfaces:**

- Produces: `async_session() -> AsyncIterator[AsyncSession]`
- Produces: `append_audit_event(session, incident_id, event_type, payload, actor) -> AuditEvent`
- Produces: `IncidentStatus` enum and transition validation

- [ ] **Step 1：定义数据库实体**

在 `models.py` 定义设计文档中的核心表：用户、Refresh Token、原始告警、标准化告警、Incident、告警关联、Evidence、Hypothesis、知识引用、ActionPlan、ActionStep、Approval、Execution、VerificationCheck、AuditEvent。

所有主键使用 UUID；所有时间使用 UTC；所有表包含 `created_at`；可变实体包含 `updated_at`。

- [ ] **Step 2：定义 Incident 状态机**

`IncidentStatus` 固定为：

```python
class IncidentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    TRIAGING = "TRIAGING"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSED = "DIAGNOSED"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    NEED_HUMAN = "NEED_HUMAN"
    FAILED = "FAILED"
```

非法状态转换抛出 `InvalidIncidentTransition`。

- [ ] **Step 3：建立 Alembic 迁移**

迁移包含唯一约束：

```text
alerts(source, fingerprint)
incident_alerts(incident_id, alert_id)
approvals(action_plan_id, action_plan_hash, user_id)
executions(idempotency_key)
```

- [ ] **Step 4：运行迁移和表检查**

Run:

```bash
.venv/bin/alembic upgrade head
podman exec oncall-postgres psql -U oncall -d oncall -c '\dt'
```

Expected: 所有核心表存在。

- [ ] **Step 5：提交**

```bash
git add alembic.ini migrations src/oncall/database.py src/oncall/models.py src/oncall/audit src/oncall/incidents
git commit -m "feat: add incident persistence model"
```

## Task 4：实现本地用户认证、JWT 和 RBAC

**Files:**

- Create: `src/oncall/auth/passwords.py`
- Create: `src/oncall/auth/tokens.py`
- Create: `src/oncall/auth/dependencies.py`
- Create: `src/oncall/auth/router.py`
- Create: `src/oncall/auth/admin_router.py`
- Create: `src/oncall/auth/schemas.py`
- Create: `src/oncall/cli/app.py`
- Create: `src/oncall/cli/users.py`
- Modify: `src/oncall/api/main.py`

**Interfaces:**

- Produces: `hash_password`, `verify_password`
- Produces: `create_access_token`, `rotate_refresh_token`
- Produces: `require_roles(*roles)` FastAPI dependency
- Produces: `/api/v1/auth/login|refresh|logout|me`
- Produces: `/api/v1/admin/users`
- Produces: `oncall users create-admin`

- [ ] **Step 1：实现密码和 Token 服务**

密码使用 Argon2id。Access JWT 包含 `sub`、`role`、`iat`、`exp` 和 `jti`，有效期15分钟。Refresh Token 为32字节随机值，有效期7天，数据库只保存 SHA-256 哈希。

- [ ] **Step 2：实现登录和刷新**

登录成功返回：

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {"id":"...","username":"approver","role":"approver"}
}
```

Refresh Token 写入 HttpOnly、SameSite Cookie；刷新时旋转并撤销旧 Token。

- [ ] **Step 3：实现 RBAC 依赖**

`require_roles("approver", "admin")` 校验 JWT 和数据库用户状态。用户被禁用后，Access Token 即使未过期也不能继续审批。

Admin Router 使用 `require_roles("admin")`，实现用户列表、创建用户、禁用用户和修改角色；禁止管理员禁用当前唯一的启用状态 admin。

- [ ] **Step 4：实现管理员 CLI**

Run:

```bash
.venv/bin/oncall users create-admin --username admin
```

CLI 使用隐藏密码输入，不接受命令行明文密码参数。

- [ ] **Step 5：执行登录冒烟验证**

先把创建管理员时输入的密码读入临时、任务专用变量，再发起请求：

```bash
read -s ONCALL_LOGIN_PASSWORD
export ONCALL_LOGIN_PASSWORD
.venv/bin/python -c 'import os, httpx; r = httpx.post("http://127.0.0.1:8000/api/v1/auth/login", json={"username": "admin", "password": os.environ["ONCALL_LOGIN_PASSWORD"]}); r.raise_for_status(); print(r.json()["token_type"])'
unset ONCALL_LOGIN_PASSWORD
```

Expected: 返回 Access Token，数据库不存在明文密码。

- [ ] **Step 6：提交**

```bash
git add src/oncall/auth src/oncall/cli src/oncall/api/main.py
git commit -m "feat: add local jwt authentication"
```

## Task 5：实现 Alert Hub、Incident 聚合和异步启动

**Files:**

- Create: `src/oncall/alerts/schemas.py`
- Create: `src/oncall/alerts/adapters/alertmanager.py`
- Create: `src/oncall/alerts/fingerprint.py`
- Create: `src/oncall/alerts/correlation.py`
- Create: `src/oncall/alerts/service.py`
- Create: `src/oncall/alerts/router.py`
- Create: `src/oncall/incidents/service.py`
- Create: `src/oncall/jobs/celery_app.py`
- Create: `src/oncall/jobs/tasks.py`
- Modify: `src/oncall/api/main.py`

**Interfaces:**

- Produces: `AlertEnvelope`
- Produces: `normalize_alertmanager(payload, project_id) -> list[AlertEnvelope]`
- Produces: `ingest_alerts(session, raw_payload) -> IngestionResult`
- Produces: `start_incident.delay(incident_id, checkpoint_version)`

- [ ] **Step 1：实现统一 AlertEnvelope**

字段与设计文档一致，`labels` 和 `annotations` 只允许 JSON 对象。`starts_at`、`ends_at` 转为 UTC datetime。

- [ ] **Step 2：实现指纹和去重**

优先使用 Alertmanager fingerprint；缺失时按项目、环境、服务、告警名和稳定标签生成 SHA-256。数据库冲突时更新 `last_seen`、`status` 和 `occurrence_count`。

- [ ] **Step 3：实现五分钟 Incident 聚合**

同项目、环境、服务、`correlation_key` 且五分钟窗口内的告警关联到同一开放 Incident。所有关联和状态变化写入 `audit_events`。

- [ ] **Step 4：实现 Webhook 快速响应**

Webhook 先验证每个项目配置的 Bearer Secret；验证失败不保存业务告警。验证成功后，在原始事件、标准化告警和 Incident 全部落库后提交 Celery Job，并返回：

```json
{"accepted":true,"incident_ids":["..."],"deduplicated":0}
```

HTTP 状态为202。

Task 5 阶段的 `start_incident` 只负责幂等完成 `RECEIVED -> TRIAGING`、写审计事件并结束；Task 8 再把该任务改为加载 Checkpointer 并启动 Incident Graph。这样每个阶段均可独立运行，不引用尚未创建的 Graph 模块。

- [ ] **Step 5：执行重复告警冒烟验证**

将同一 Alertmanager JSON 连续 POST 两次。

Expected: `alerts` 只有一条，`occurrence_count=2`，只有一个开放 Incident。

- [ ] **Step 6：提交**

```bash
git add src/oncall/alerts src/oncall/incidents/service.py src/oncall/jobs src/oncall/api/main.py
git commit -m "feat: add unified alert ingestion"
```

## Task 6：实现 Milvus 混合 RAG 和知识导入 CLI

**Files:**

- Create: `src/oncall/rag/schemas.py`
- Create: `src/oncall/rag/chunking.py`
- Create: `src/oncall/rag/embeddings.py`
- Create: `src/oncall/rag/milvus_store.py`
- Create: `src/oncall/rag/service.py`
- Create: `src/oncall/cli/knowledge.py`
- Create: `project-packs/demo-shop/knowledge/post-deployment-regression.md`
- Create: `project-packs/demo-shop/project.yaml`

**Interfaces:**

- Produces: `ingest_project_knowledge(project_id, path) -> IngestionSummary`
- Produces: `search_knowledge(query: KnowledgeQuery) -> list[KnowledgeCitation]`
- Produces: `oncall knowledge ingest`

- [ ] **Step 1：定义知识 Schema 和 Collection**

Collection 使用 `chunk_id` 主键、Dense Vector、Sparse Vector及设计文档中的标量元数据。查询强制包含 `project_id` 和 `review_status == "approved"`。

- [ ] **Step 2：实现 Markdown 切分**

先按标题层级切分；超过1000 Token 的章节再次切分；相邻块保留约100 Token 重叠；每块保存 `section_path` 和 `source_path`。

- [ ] **Step 3：实现百炼 Embedding**

复用 OpenAI-compatible Client。启动时从 Milvus Collection 读取向量维度并与配置模型输出维度比对，不一致时拒绝导入。

- [ ] **Step 4：实现 Dense + BM25 + RRF**

查询返回最多5个结果，并映射为：

```python
class KnowledgeCitation(BaseModel):
    document_id: str
    title: str
    version: str
    section_path: str
    source_path: str
    content: str
    score: float
```

- [ ] **Step 5：编写演示 SOP 并导入**

SOP 必须包含：发布变更核对、错误率对比、日志检查、回滚前置条件、回滚步骤和恢复验证指标。

Run:

```bash
.venv/bin/oncall knowledge ingest --project demo-shop --path project-packs/demo-shop/knowledge
```

Expected: 插入非零 Chunk，查询“发布后错误率升高如何处置”返回该 SOP。

- [ ] **Step 6：提交**

```bash
git add src/oncall/rag src/oncall/cli project-packs/demo-shop
git commit -m "feat: add milvus operational knowledge rag"
```

## Task 7：实现 Skill Registry 和只读 MCP 能力

**Files:**

- Create: `src/oncall/skills/schemas.py`
- Create: `src/oncall/skills/registry.py`
- Create: `project-packs/demo-shop/skills/post_deployment_regression/skill.yaml`
- Create: `project-packs/demo-shop/skills/post_deployment_regression/instructions.md`
- Create: `mcp_servers/observability/server.py`
- Create: `mcp_servers/release/server.py`
- Create: `src/oncall/mcp_gateway/client.py`
- Create: `src/oncall/mcp_gateway/schemas.py`

**Interfaces:**

- Produces: `find_candidate_skills(alert, service_type) -> list[SkillDefinition]`
- Produces MCP tools: `query_metrics`, `query_service_logs`, `get_service_health`, `get_active_alerts`
- Produces MCP tools: `get_current_release`, `get_recent_releases`, `get_release_diff`
- Produces: `McpGateway.call_read_tool(name, arguments) -> ToolResult`

- [ ] **Step 1：实现 Skill 加载和候选筛选**

启动时校验每个 `skill.yaml` 的名称、版本、触发条件、允许工具、禁止动作和输出 Schema。候选数量最多3个。

- [ ] **Step 2：编写发布回归 Skill**

Skill 允许的工具仅包括指标、日志、健康状态和发布查询；明确禁止回滚、重启、扩缩容和任意写操作。

- [ ] **Step 3：实现 Observability MCP**

每个工具使用固定参数对象，包括项目、环境、服务、时间窗口和返回上限。日志返回最多100条或20 KB。

- [ ] **Step 4：实现 Release MCP**

发布历史读取 `.runtime/demo-release.json`，返回当前版本、发布时间、发布人标签和版本差异摘要。该 MCP 无写能力。

- [ ] **Step 5：实现 MCP Gateway**

Gateway 维护工具到 Server 的显式映射，使用每个 Server 独立的服务密钥，并统一执行超时、Schema 校验、结果截断、审计字段注入和错误分类。

- [ ] **Step 6：启动并人工调用工具**

Run Observability 和 Release MCP 后，从 Python 调用 Gateway 查询 `order-api` 当前版本和最近指标。

Expected: 结构化返回 `v1` 和非空指标，无 Shell 字符串接口。

- [ ] **Step 7：提交**

```bash
git add src/oncall/skills src/oncall/mcp_gateway mcp_servers/observability mcp_servers/release project-packs/demo-shop/skills
git commit -m "feat: add skills and read only mcp tools"
```

## Task 8：实现 LangGraph、Supervisor 和专业 Agent

**Files:**

- Create: `src/oncall/agents/model_factory.py`
- Create: `src/oncall/agents/supervisor.py`
- Create: `src/oncall/agents/investigation.py`
- Create: `src/oncall/agents/knowledge.py`
- Create: `src/oncall/agents/remediation.py`
- Create: `src/oncall/graph/contracts.py`
- Create: `src/oncall/graph/investigation.py`
- Create: `src/oncall/graph/incident.py`
- Modify: `src/oncall/jobs/tasks.py`

**Interfaces:**

- Produces: `create_chat_model(agent_name) -> BaseChatModel`
- Produces: `build_investigation_graph() -> CompiledStateGraph`
- Produces: `build_incident_graph(checkpointer) -> CompiledStateGraph`
- Consumes: RAG、Skill Registry、MCP Gateway、Incident 数据服务

- [ ] **Step 1：实现 ModelFactory**

使用百炼 `base_url`、API Key和模型名创建 OpenAI-compatible Chat Model。不同 Agent 通过配置名称选择模型，但 V1 默认共用一个模型。

- [ ] **Step 2：定义 Graph State 和输出契约**

State 包含 Incident ID、状态、告警摘要、证据、假设、知识引用、选定 Skill、ActionPlan、审批、执行和验证结果，以及调查轮数和模型调用计数。

- [ ] **Step 3：实现 Investigation 和 Knowledge Agent**

Investigation Agent 只能调用只读 MCP；Knowledge Agent 只能调用 RAG。两者只返回结构化结果，不返回完整内部消息历史。

- [ ] **Step 4：实现受控 Supervisor**

Supervisor 只能选择：

```text
call_investigation
call_knowledge
submit_diagnosis
request_human
```

它不能看到 Recovery 工具。

- [ ] **Step 5：实现 Remediation Planner**

只有 Diagnosis Gate 接受根因后才调用。输出 ActionPlan，且 V1 唯一允许的动作类型为 `rollback_release`。

- [ ] **Step 6：实现外层 Incident Graph**

显式节点与设计文档一致，调查最多4轮、模型最多12次、验证失败最多重新调查一次。审批节点使用 LangGraph `interrupt()`。

- [ ] **Step 7：执行无写操作的 Graph 冒烟运行**

使用真实百炼、RAG 和只读 MCP，让一个已存在 Incident 运行到 `WAITING_APPROVAL`。

Expected: 产生证据、根因、SOP 引用和 ActionPlan；未执行任何回滚。

- [ ] **Step 8：提交**

```bash
git add src/oncall/agents src/oncall/graph src/oncall/jobs/tasks.py
git commit -m "feat: add supervised incident graph"
```

## Task 9：实现 Policy、审批 API、Recovery MCP 和恢复验证

**Files:**

- Create: `src/oncall/policy/schemas.py`
- Create: `src/oncall/policy/engine.py`
- Create: `src/oncall/execution/service.py`
- Create: `src/oncall/execution/verification.py`
- Create: `src/oncall/incidents/router.py`
- Create: `mcp_servers/recovery/server.py`
- Create: `mcp_servers/recovery/podman_runner.py`
- Create: `project-packs/demo-shop/policies.yaml`
- Modify: `src/oncall/graph/incident.py`
- Modify: `src/oncall/api/main.py`

**Interfaces:**

- Produces: `evaluate_action_plan(plan, incident) -> PolicyDecision`
- Produces: `POST /api/v1/incidents/{id}/approvals|rejections|retries`
- Produces: `execute_approved_plan(incident_id, plan_id) -> ExecutionResult`
- Produces Recovery MCP tool: `rollback_release`
- Produces: `verify_recovery(criteria) -> VerificationResult`

- [ ] **Step 1：实现 ActionPlan 哈希和策略引擎**

使用规范化 JSON 计算 SHA-256。策略只允许 `demo-shop/staging/order-api` 从 `v2` 回滚到 `v1`。计划改变后必须重新审批。

- [ ] **Step 2：实现审批与拒绝 API**

只有 `approver` 或 `admin` 可以批准。Approval 同时保存 `user_id`、计划 ID、计划哈希、决策、备注和时间。批准事务提交后投递幂等 `resume_incident` Celery Job；拒绝则把 Incident 转为 `NEED_HUMAN`，不恢复 Graph 执行。

- [ ] **Step 3：实现宿主机 Recovery MCP**

Recovery MCP 使用独立服务密钥，仅暴露 `rollback_release`。Recovery Executor 根据审批记录生成包含计划哈希、Approval ID、时间戳和随机数的 HMAC `approval_proof`；不向 Recovery MCP 转发用户 JWT。内部 `podman_runner` 只允许固定项目目录、固定 Compose 服务和 `v1|v2` 版本枚举。

Recovery MCP 验证 HMAC、五分钟时间窗和 nonce 防重放记录，并再次核对当前版本、目标版本及项目白名单；验证失败时不得调用 Podman。

不得出现：

```python
subprocess.run(command, shell=True)
```

必须使用固定参数列表和 `shell=False`。

- [ ] **Step 4：实现幂等执行**

执行前插入 `executions(idempotency_key)`；冲突时返回已有结果。超时后先查询执行记录和当前版本，禁止直接重试回滚。

- [ ] **Step 5：实现恢复验证**

回滚后等待一个 Prometheus 抓取周期，再检查：当前版本为 `v1`、服务健康、错误率低于5%。满足后进入 `RESOLVED`，否则最多重新调查一次。

- [ ] **Step 6：执行人工审批冒烟验证**

让 Graph 停在审批点，使用 approver Token 批准。

Expected: Recovery MCP 只执行一次，demo 版本恢复为 `v1`，Incident 进入 `RESOLVED`。

- [ ] **Step 7：提交**

```bash
git add src/oncall/policy src/oncall/execution src/oncall/incidents/router.py src/oncall/graph/incident.py src/oncall/api/main.py mcp_servers/recovery project-packs/demo-shop/policies.yaml
git commit -m "feat: add approved recovery execution"
```

## Task 10：实现 Incident 查询、SSE 和报告 API

**Files:**

- Create: `src/oncall/incidents/schemas.py`
- Create: `src/oncall/incidents/queries.py`
- Create: `src/oncall/incidents/report.py`
- Create: `src/oncall/incidents/sse.py`
- Modify: `src/oncall/incidents/router.py`

**Interfaces:**

- Produces: `GET /api/v1/incidents`
- Produces: `GET /api/v1/incidents/{id}`
- Produces: `GET /api/v1/incidents/{id}/events`
- Produces: `GET /api/v1/incidents/{id}/stream`
- Produces: `GET /api/v1/incidents/{id}/report`

- [ ] **Step 1：实现列表和详情聚合查询**

详情一次返回概览、告警、证据、假设、引用、计划、审批、执行和验证摘要。原始日志不进入响应。

- [ ] **Step 2：实现持久化事件重放**

SSE 客户端提供 `Last-Event-ID` 时，先从 PostgreSQL `audit_events` 补发缺失事件，再订阅 Redis 实时通知。

- [ ] **Step 3：实现 Markdown Incident 报告**

报告包含：摘要、时间线、告警、根因及证据、SOP 引用、修复计划、审批、执行和验证结果。V1 只生成报告，不写入 Milvus。

- [ ] **Step 4：执行 API 冒烟验证**

使用 viewer Token 请求列表、详情、事件和报告。

Expected: viewer 可读但无法审批；SSE 断开重连后不丢事件。

- [ ] **Step 5：提交**

```bash
git add src/oncall/incidents
git commit -m "feat: add incident query and event api"
```

## Task 11：实现 React Incident Web

**Files:**

- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/auth/AuthProvider.tsx`
- Create: `frontend/src/pages/LoginPage.tsx`
- Create: `frontend/src/pages/IncidentListPage.tsx`
- Create: `frontend/src/pages/IncidentDetailPage.tsx`
- Create: `frontend/src/pages/UserAdminPage.tsx`
- Create: `frontend/src/features/incidents/api.ts`
- Create: `frontend/src/features/incidents/types.ts`
- Create: `frontend/src/features/incidents/IncidentOverview.tsx`
- Create: `frontend/src/features/incidents/IncidentTimeline.tsx`
- Create: `frontend/src/features/incidents/EvidencePanel.tsx`
- Create: `frontend/src/features/incidents/KnowledgePanel.tsx`
- Create: `frontend/src/features/incidents/RemediationPanel.tsx`
- Create: `frontend/src/features/incidents/AuditPanel.tsx`
- Create: `frontend/nginx.conf`
- Generate: `frontend/package-lock.json`
- Create: `containers/frontend.Containerfile`
- Modify: `compose.yaml`

**Interfaces:**

- Consumes: Auth、Incident、Approval、SSE API
- Produces routes: `/login`, `/incidents`, `/incidents/:incidentId`, `/admin/users`

- [ ] **Step 1：初始化 React + TypeScript**

使用 Vite、React Router、TanStack Query和 Zod。Access Token 只保存在 AuthProvider 内存；刷新 Token 由浏览器 Cookie 管理。SSE 使用带 `Authorization` Header 的 `fetch()` 流式读取，不使用无法附加 Bearer Header 的原生 `EventSource`。Vite 开发服务器和 frontend 容器中的 Nginx 都把 `/api` 代理到 API 服务，浏览器始终使用同源路径，避免额外放宽 CORS。

- [ ] **Step 2：实现登录与路由保护**

未认证用户跳转 `/login`。401 时只尝试刷新一次；失败后清空内存状态并回到登录页。

- [ ] **Step 3：实现 Incident 列表**

支持状态、严重度、服务和时间筛选；展示 Incident ID、标题、服务、状态、严重度、开始时间和最后更新时间。

- [ ] **Step 4：实现详情标签页**

按设计实现：概览、时间线、证据、知识、恢复计划、审计。使用 SSE 更新 TanStack Query Cache，不整页刷新。

- [ ] **Step 5：实现审批和用户管理**

只有 approver/admin 展示批准与拒绝按钮；提交时携带当前 `action_plan_hash`。admin 页面支持创建、禁用和修改用户角色。

- [ ] **Step 6：构建并运行**

Run:

```bash
cd frontend
npm install
npm run build
```

Expected: TypeScript 构建成功。随后通过 Podman 打开 `http://127.0.0.1:5173`，能登录并查看真实 Incident。

- [ ] **Step 7：提交**

```bash
git add frontend containers/frontend.Containerfile compose.yaml
git commit -m "feat: add incident operations console"
```

## Task 12：接通完整链路、可观测性和运行文档

**Files:**

- Modify: `compose.yaml`
- Modify: `Makefile`
- Create: `docs/runbooks/local-development.md`
- Create: `docs/runbooks/manual-acceptance.md`
- Create: `docs/runbooks/recovery-mcp.md`
- Create: `src/oncall/metrics.py`
- Modify: `src/oncall/logging.py`
- Modify: `.env.example`

**Interfaces:**

- Produces: `make infra-up`, `make app-up`, `make recovery-mcp`, `make demo-fail`, `make demo-reset`, `make down`
- Produces Prometheus application metrics and correlated JSON logs
- Produces complete manual acceptance guide

- [ ] **Step 1：统一启动顺序和健康检查**

Compose healthcheck 必须覆盖 PostgreSQL、Redis、Milvus、Prometheus、Alertmanager、API、Worker、Observability MCP、Release MCP、demo 和 frontend。依赖只能在上游 ready 后启动。宿主机 Recovery MCP 不加入 Compose，由 `make recovery-mcp` 单独启动并提供 `/health/ready`；API/Worker 在执行恢复前必须检查该端点。

- [ ] **Step 2：加入关联日志和指标**

每个关键日志包含：`incident_id`、`graph_run_id`、`agent_name`、`tool_call_id`、`job_id`。加入设计文档列出的核心 Prometheus 指标。

- [ ] **Step 3：编写本地开发 Runbook**

文档写清：Podman Machine 资源建议、Compose Provider 检查、环境变量、百炼配置、数据库迁移、管理员创建、知识导入、Recovery MCP 启动和常见故障。

- [ ] **Step 4：执行完整人工验收**

严格按设计文档第20节执行17步流程，并在 `docs/runbooks/manual-acceptance.md` 记录实际命令和预期界面状态。

- [ ] **Step 5：检查危险接口**

Run:

```bash
rg -n "run_shell|execute_sql|shell=True|kubectl|docker.sock|/var/run/docker.sock" src mcp_servers
```

Expected: 不存在 Agent 可调用的宽接口；Recovery 内部也没有 `shell=True`。

- [ ] **Step 6：检查仓库状态并提交**

```bash
git status --short
git add compose.yaml Makefile .env.example docs/runbooks src/oncall/metrics.py src/oncall/logging.py
git commit -m "docs: complete local oncall acceptance flow"
```

## 实施后的 V1.1 计划边界

V1 功能闭环人工验收通过后，再单独编写 V1.1 测试计划，覆盖：

- Alert Hub 单元测试和重放样本。
- Auth/RBAC 测试。
- Policy、Approval、幂等和失败路径测试。
- MCP Schema 与契约测试。
- LangGraph Fake Model 测试。
- 百炼 Live Agent 评测。
- Milvus RAG Hit@5 与引用正确性评测。
- React 组件测试和 Playwright E2E。
- 三次连续真实回滚回归。
