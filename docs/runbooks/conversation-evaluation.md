# Conversation Router 与 Agent Evaluation

## 对话入口

控制台 `/assistant` 提供持久化多轮对话。Router 只有两个一级意图：

- `KNOWLEDGE`：运维知识、SOP、根因与处置方法问答；回答必须来自已审核 RAG 引用。
- `INCIDENT`：具体 Incident 的查询、调查或修复规划，动作进一步分为 `QUERY`、`INVESTIGATE` 和 `PLAN`。

自然语言审批与执行被固定路由为 `APPROVAL_BLOCKED`。对话入口永远不会批准计划或调用 Recovery MCP；用户必须在 Incident 详情页核对计划哈希并完成审批。

PostgreSQL 是 Conversation/Message 的完整事实来源。Mem0 只保存知识问答中经过脱敏和提炼的长期记忆，不保存易过期的 Incident 状态或操作话术；它复用现有百炼模型与独立 Milvus collection。Mem0 初始化、检索或写入失败时，对话会降级为无长期记忆模式。

每轮请求会注入最近 12 条 PostgreSQL 消息作为短期上下文；Conversation 还会绑定最近操作的 Incident，使“继续排查它”可以继承事件实体。明确的知识、查询、调查、规划和审批话术使用确定性规则快速路由，歧义请求再交由模型结构化分类。

相关配置：

```env
CONVERSATION_ROUTER_LIVE=true
MEM0_ENABLED=true
MEM0_COLLECTION=oncall_conversation_memory
MEM0_TELEMETRY=false
```

主要 API：

- `POST /api/v1/conversations`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{id}`
- `POST /api/v1/conversations/{id}/messages`

## Agent 评测

控制台 `/evaluations` 支持两种模式：

- `offline`：回放版本化固定数据集，无模型费用，可重复执行。
- `online`：真实调用 Conversation Router、Milvus RAG、只读 MCP，并使用历史 Incident 检查端到端解决情况。

评测通过 Celery 异步执行，API 只创建 `PENDING` 运行记录，前端轮询直到 `COMPLETED` 或 `FAILED`。每个样本都会保存输入、期望、实际输出、子分数、耗时和安全错误类型。

指标定义：

- **任务成功率**：所有评测样本中通过断言的比例。
- **工具调用成功率**：MCP 工具样本成功返回且通过输出契约的比例。
- **RAG 命中率**：期望文档在 Top-K 检索结果中的平均覆盖率。
- **端到端解决率**：端到端样本最终达到已验证 `RESOLVED` 状态的比例。

内置数据集位于 `src/oncall/evaluation/datasets/oncall_v1.json`，修改样本时必须同步更新数据集版本。

主要 API：

- `POST /api/v1/evaluations/runs`，请求体为 `{"mode":"offline"}` 或 `{"mode":"online"}`。
- `GET /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs/{id}`

## 启用步骤

```bash
make install
make migrate
```

随后重启 API、Celery Worker 与前端。在线评测前需确保百炼、Milvus、Observability MCP 和 Release MCP 均可用。
