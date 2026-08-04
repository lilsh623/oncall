# 本地开发

本项目采用混合模式：容器运行基础设施与入站服务（PostgreSQL、Redis、Milvus、etcd、MinIO、Prometheus、Alertmanager、demo、traffic、API、前端）；宿主机运行 app 层（Celery Worker、Observability MCP、Release MCP、Recovery MCP）。

Worker 放在宿主机，是因为它要通过 `127.0.0.1:8080` 直连宿主机 Recovery MCP 执行真实回滚。容器化 Worker 无法通过 Podman Machine 网关回调 macOS 宿主机的 loopback 服务，而 SDD 13.2 禁止把 Podman socket 挂进任何容器。

1. 复制 `.env.example` 为 `.env`，替换 JWT、MCP 密钥与百炼 API Key。`RECOVERY_MCP_SECRET` 和 `RECOVERY_APPROVAL_SECRET` 必须不同。
2. 确认 `podman system connection list` 指向原有 Machine，执行 `make infra-up`。
3. 执行 `make migrate`，用 `oncall users create-admin` 创建首个 admin，并用 `oncall knowledge ingest` 导入项目知识。
4. 执行 `make app-up` 启动容器化 API 和前端。
5. 执行 `make app-host` 在宿主机启动 Worker 与三个 MCP（日志和 PID 写入 `.runtime/host-app/`）。用 `make app-host-down` 停止它们。
6. 浏览器打开 `http://127.0.0.1:5173`。开发模式可改用 `cd frontend && npm run dev`。

数据库升级到 `0006_learned_skills` 后，新的已验证 Incident 会自动创建经验候选，
并支持 Experience-to-Skill 演化。
如需为历史 Incident 生成候选，执行：

```bash
oncall experience backfill
```

候选可在前端“经验中心”查看；所有登录用户可读，只有 admin 可以发布或拒绝。

经验发布后会自动生成一个只读调查 Skill 候选。admin 在“经验中心”再次审核并发布后，
系统会创建 ACTIVE `SkillVersion`；Learned Skill 存在 PostgreSQL 中，不会覆盖
Git 管理的 `project-packs`。调查时优先使用匹配的 ACTIVE Learned Skill，找不到时
回退到静态 Skill。

Alertmanager 通过 Compose 私有网络中的 `http://api:8000` 调用容器化 API，不依赖 `host.containers.internal`。Recovery MCP 不加入 Compose，因为它需要控制宿主机 Podman；不要把 Podman socket 暴露给任何容器或 Agent。

宿主机进程从 `.env` 文件读取配置，请勿在启动前 `source .env`——shell 会破坏 `ALERTMANAGER_WEBHOOK_SECRETS` 等 JSON 值的引号，导致 pydantic-settings 解析失败。

常见问题：没有配置百炼 Key 时，真实调查会安全地转入 `NEED_HUMAN`；先核对 `.env`。Recovery MCP 未启动时，批准后的执行也会转人工，绝不会尝试任意 shell 命令。

## OpenAI Agents SDK 智能循环

实时 Incident 调查使用 OpenAI Agents SDK，仍通过 `BAILIAN_BASE_URL` 和
`BAILIAN_CHAT_MODEL` 连接当前 OpenAI-compatible 模型。一个 `Incident Supervisor`
将以下三个隔离模型上下文的专业 Agent 暴露为 agent tools：

- `Evidence Investigator`：仅调用经过 `McpGateway` 校验的只读观测和发布工具；
- `Incident Diagnostician`：读取已收集证据并检索已审核 SOP；
- `Remediation Planner`：只生成结构化 `rollback_release` 计划，不拥有恢复工具。

循环从调查持续到生成计划。计划返回后，外层 LangGraph 对作用域和结构做确定性
校验、计算稳定 Plan Hash、持久化并进入 `WAITING_APPROVAL`。审批通过后，同一个
持久化 Graph Run 从 checkpoint 恢复，依次进入 `execute`、`verify`，再路由到
`RESOLVED` 或 `NEED_HUMAN`。Celery 只负责启动或恢复 Graph Run。

审批、Recovery MCP 执行和恢复验证仍不属于模型控制的 Agent Loop：`execute` 节点
只调用固定、幂等且经过策略校验的执行服务；`verify` 节点只调用只读观测工具。模型
没有审批权，也不会获得 Recovery MCP、shell 或任意写工具。

本地可调整的循环边界：

```dotenv
AGENT_LOOP_MAX_TURNS=8
AGENT_SPECIALIST_MAX_TURNS=4
AGENT_MAX_TOOL_CALLS=16
OPENAI_AGENTS_TRACING_ENABLED=false
```

使用非 OpenAI API Key 时保持 SDK tracing 关闭；如后续启用 tracing，仍应保持敏感
输入/输出不上报，并为 trace exporter 单独配置凭据。
