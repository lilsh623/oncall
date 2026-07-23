# 人工验收

1. 配置真实百炼 Key 后执行 `make infra-up`、`make migrate`、`make recovery-mcp`、`make app-up`。
2. 用 admin 登录前端并创建 viewer、approver；确认 viewer 无审批按钮。
3. 执行 `make demo-fail`，等待 Alertmanager webhook 创建 Incident。
4. 确认图收集只读证据、RAG 引用和 `rollback_release` 计划后停在 `WAITING_APPROVAL`。
5. 使用 approver 批准当前计划哈希；确认一次 Recovery MCP 执行、版本为 `v1`、健康且 5xx 低于 5%，Incident 变为 `RESOLVED`。
6. 断开并重连详情页事件流，确认 `Last-Event-ID` 后的审计事件会补发。

验收中不得把真实用户 JWT、任意命令、路径或 URL 传入 MCP 工具。
