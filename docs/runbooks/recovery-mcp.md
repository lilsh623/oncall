# Recovery MCP

Recovery MCP 只暴露 `rollback_release` 一个写工具。平台仅在恢复计划已通过人工审批、且服务策略允许该动作时调用它；不向 MCP 转发用户 JWT。Worker 会签名计划范围、计划哈希、Approval ID、时间戳和 nonce，Recovery MCP 校验 HMAC 与五分钟有效期，并通过 Redis 原子消费 nonce 防止重放。平台数据库中的幂等执行记录提供第二层重复执行保护。

## 腾讯云 TAT

在服务的 `recovery_config` 中登记固定的 TAT 命令和目标实例。Agent 只能给预先登记的命令传递当前/目标版本；不能修改地域、命令 ID、实例列表或附加命令内容。

```json
{
  "provider": "tencent_tat",
  "tool_name": "rollback_release",
  "action_policy": {"rollback_release": "approval"},
  "tencent_tat": {
    "region": "ap-guangzhou",
    "command_id": "cmd-rollback-release",
    "instance_ids": ["ins-xxxxxxxx"],
    "parameters": {"namespace": "payments", "service": "checkout"},
    "target_version_parameter": "target_version",
    "current_version_parameter": "current_version",
    "poll_timeout_seconds": 55
  }
}
```

Recovery MCP 从自身环境读取 `TENCENTCLOUD_SECRET_ID`、`TENCENTCLOUD_SECRET_KEY` 和可选的 `TENCENTCLOUD_API_BASE_HOST`，通过腾讯云官方 SDK 调用 TAT `InvokeCommand`，并轮询 `DescribeInvocations` 至终态。调用成功后才进入平台只读健康检查；失败、超时或非成功终态会将 Incident 转交人工处理。

TAT 命令应预先创建为可参数化的回滚脚本，并为 Recovery MCP 运行身份授予最小的 TAT 命令调用权限。启动：`make recovery-mcp`；就绪探针：`http://127.0.0.1:8080/health/ready`。不要将此端口暴露到非受信网络。
