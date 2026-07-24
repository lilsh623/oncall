# 人工验收

## 启动顺序

1. 配置真实百炼 Key 后执行 `make infra-up`、`make migrate`。
2. 执行 `make app-up`（容器化 API + 前端）与 `make app-host`（宿主机 Worker + Observability/Release/Recovery MCP）。
3. 用 admin 登录前端并创建 viewer、approver；确认 viewer 无审批按钮。

## 验收流程

4. 执行 `make demo-fail`，等待 `HighErrorRate` 触发并经 Alertmanager webhook 创建 Incident。
5. 确认图收集只读证据、RAG 引用后收敛到诊断，生成 `rollback_release` 计划并停在 `WAITING_APPROVAL`；此时无任何回滚执行。
6. 使用 approver 批准当前计划哈希；确认一次 Recovery MCP 执行、版本回到 `v1`、服务健康、5xx 低于 5%，Incident 变为 `RESOLVED`。
7. 断开并重连详情页事件流，确认 `Last-Event-ID` 后的审计事件会补发。

验收中不得把真实用户 JWT、任意命令、路径或 URL 传入 MCP 工具。

## 已验证证据（2026-07-24，混合模式，真实百炼）

完整闭环在本机跑通，Incident 进入 `RESOLVED`：

- 回滚执行：`execution=SUCCEEDED`，宿主机 Worker 通过 `127.0.0.1:8080` 单次调用宿主机 Recovery MCP，demo 服务由 `v2` 回到 `v1`。
- 验证三项全部通过：`release_version=v1`、`service_health=healthy`、`http_error_rate=0.0`（阈值 0.05）。
- 审计时间线完整：`incident.created` → `alert_linked` → `graph_completed` → `approved` → `recovery_executed` → `resolved`。

### 验收中发现并修复的问题

- Knowledge Agent 按错误字段名读取 RAG 引用，导致调查阶段崩溃。
- Supervisor 在证据与 SOP 引用已充足时仍不提交诊断，耗尽调查轮数转入 `NEED_HUMAN`。
- 恢复验证的错误率 PromQL 未对回滚后停更的 5xx 序列兜底，读到陈旧的高错误率；已加 `or vector(0)`，并改用较短的 rate 窗口以更快反映恢复。
