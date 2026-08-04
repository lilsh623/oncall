# 人工验收

## 启动顺序

1. 配置真实百炼 Key 后执行 `make infra-up`、`make migrate`。
2. 执行 `make app-up`（容器化 API + 前端）与 `make app-host`（宿主机 Worker + Observability/Release/Recovery MCP）。
3. 用 admin 登录前端并创建 viewer、approver；确认 viewer 无审批按钮。

## 验收流程

4. 执行 `make demo-fail`，等待 `HighErrorRate` 触发并经 Alertmanager webhook 创建 Incident。
5. 确认图收集只读证据、RAG 引用后收敛到诊断，生成 `rollback_release` 计划并停在 `WAITING_APPROVAL`；此时无任何回滚执行。
6. 使用 approver 批准当前计划哈希；确认同一个 LangGraph Run 从 interrupt 恢复，
   依次经过 `execute`、`verify`，只发生一次 Recovery MCP 执行，版本回到 `v1`、
   服务健康、5xx 低于 5%，Incident 变为 `RESOLVED`。
7. 断开并重连详情页事件流，确认 `Last-Event-ID` 后的审计事件会补发。
8. 打开“经验中心”，确认验证通过的 `RESOLVED` Incident 自动生成一个
   `PENDING_REVIEW` 候选，且候选包含 Evidence、Knowledge Citation、ActionPlan、
   Execution 和 Verification Check 的来源 ID。
9. 使用 admin 发布候选，确认候选变为 `PUBLISHED` 并出现在“已发布经验”列表；
   再次发布返回相同经验。当前版本的重复模式候选必须拒绝，不能直接发布。
10. 确认经验发布后自动生成一个 `PENDING_REVIEW` Skill 候选。检查 manifest
    只包含只读工具，并显式禁止 rollback、restart、scale 和 arbitrary_write。
11. 使用 admin 发布 Skill 候选，确认生成 `ACTIVE` SkillVersion；同名旧版本应
    自动变为 `RETIRED`。新的调查应优先选择匹配项目、环境和服务的 ACTIVE Learned Skill。

验收中不得把真实用户 JWT、任意命令、路径或 URL 传入 MCP 工具。

## 经验自动沉淀

- 只有 `RESOLVED`、执行成功、全部验证通过、存在有效证据、知识引用和根因假设的
  Incident 才能生成经验候选。
- 提取在 Incident 解决事务提交后运行；提取失败不会回滚已经成功的恢复结果。
- 候选会做确定性脱敏、内容哈希、模式指纹和精确去重。
- 经验发布仅允许 admin；发布会自动创建待审核 Skill 候选，但不会自动启用 Skill。
- 旧的已解决 Incident 可执行 `oncall experience backfill` 进行幂等回填。

## Experience-to-Skill 演化

- 发布 Experience 后会自动生成 Skill 候选，但候选没有运行权限。
- Skill 候选使用现有 `SkillManifest` 严格校验，工具只能来自只读 MCP 白名单。
- Skill 发布需要 admin 进行第二次独立审核。
- 发布后的 Skill 以 `SkillVersion` 保存于 PostgreSQL；新版本原子激活，同名旧版本退役。
- Investigation Agent 每次调查都查询 ACTIVE Learned Skill，并以 Git 管理的静态 Skill
  作为回退。Skill 只限制调查工具，不授权任何恢复操作。

## 已验证证据（2026-07-24，混合模式，真实百炼）

完整闭环在本机跑通，Incident 进入 `RESOLVED`：

- 回滚执行：`execution=SUCCEEDED`，宿主机 Worker 通过 `127.0.0.1:8080` 单次调用宿主机 Recovery MCP，demo 服务由 `v2` 回到 `v1`。
- 验证三项全部通过：`release_version=v1`、`service_health=healthy`、`http_error_rate=0.0`（阈值 0.05）。
- 审计时间线完整：`incident.created` → `alert_linked` → `graph_completed` → `approved` → `recovery_executed` → `resolved`。

### 验收中发现并修复的问题

- Knowledge Agent 按错误字段名读取 RAG 引用，导致调查阶段崩溃。
- Supervisor 在证据与 SOP 引用已充足时仍不提交诊断，耗尽调查轮数转入 `NEED_HUMAN`。
- 恢复验证的错误率 PromQL 未对回滚后停更的 5xx 序列兜底，读到陈旧的高错误率；已加 `or vector(0)`，并改用较短的 rate 窗口以更快反映恢复。
