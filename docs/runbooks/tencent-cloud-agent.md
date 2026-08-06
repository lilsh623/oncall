# 腾讯云通用运维 Agent 接入

## 1. 登记工程和服务

运行迁移后，以管理员身份打开前端的“工程接入”页面：

```bash
make migrate
make api
```

先创建工程，再登记服务。服务的 `runtime_type` 只接受腾讯云 `tke` 或 `cvm`。创建告警入口后，页面只展示一次高熵密钥；后端只保存 SHA-256 摘要，之后无法从数据库恢复该密钥。

告警入口接受：

| 来源 | URL 路径 | 适合的回调 |
| --- | --- | --- |
| `tencent_monitor` | `/api/v1/webhooks/tencent-monitor/{integration_key}` | 腾讯云监控告警回调 |
| `tencent_cls` | `/api/v1/webhooks/tencent-cls/{integration_key}` | CLS 告警/自定义回调 |

所有入口都支持 `Authorization: Bearer <secret>`；腾讯云监控也支持 `Authorization: Basic <username>:<secret>` 的 Base64 形式。回调先落原始事件，再去重、关联 Incident，并异步启动 Agent。

腾讯云监控的 `alarmStatus=1/0` 会转换成 `firing/resolved`，`alarmLevel` 会转换成 `critical/warning/info`。如果腾讯回调没有携带环境和服务，入口创建时登记的默认值会作为可信范围，避免模型从请求体改变处置目标。

## 2. 接入腾讯 CLS MCP 日志查询

本项目不会把腾讯云 SecretId/SecretKey 存在工程表中。按腾讯官方 CLS MCP 文档启动独立的 Streamable HTTP MCP 服务，将凭证限制在只读 CLS 子用户：

```bash
TRANSPORT=http \
TENCENTCLOUD_SECRET_ID=... \
TENCENTCLOUD_SECRET_KEY=... \
TENCENTCLOUD_API_BASE_HOST=internal.tencentcloudapi.com \
PORT=3000 \
npx -y cls-mcp-server@latest
```

在 API 和 Worker 的 `.env` 中设置：

```dotenv
CLS_MCP_URL=http://127.0.0.1:3000/mcp
# 如果 MCP 前面还有网关鉴权，再设置；官方服务本身通常不需要这个入站 token。
CLS_MCP_AUTH_TOKEN=
```

在前端服务配置中填写 CLS `TopicId`。配置完成后，Agent 仍调用原有 `query_service_logs` 逻辑工具，Gateway 会按工程/环境/服务查目录，构造受限查询并调用官方 `SearchLog`：

```json
{
  "Region": "ap-guangzhou",
  "TopicId": "topic-id",
  "From": 1754388000000,
  "To": 1754389800000,
  "Query": "service=\"order-api\" AND level IN (\"ERROR\",\"WARNING\")",
  "Sort": "desc",
  "Limit": 50,
  "Offset": 0
}
```

CLS MCP 返回的 `LogJson` 和毫秒时间戳会被转换为 Agent 内部的 `LogsResult`，并继续执行结果大小、字段和服务范围校验。没有 CLS 配置时调用会明确失败为 `CLS_NOT_CONFIGURED`，不会回退到任何本地数据源。

## 3. 恢复 MCP 和安全边界

恢复是写操作，不能让模型直接执行 TAT 的任意命令。当前执行链路是：

1. Agent 只能生成结构化 `rollback_release` 计划；
2. 服务目录的 `recovery_config.action_policy` 决定该动作是否允许；
3. 高风险恢复动作必须经过人工审批；
4. Worker 用独立密钥签名计划哈希、Approval ID、时间戳和一次性 nonce；
5. Recovery MCP 校验 HMAC、五分钟有效期，并用 Redis 原子消费 nonce 防止重放；
6. 校验通过后，Worker 才调用固定的 `rollback_release`；
7. Recovery MCP 调用 TAT `InvokeCommand` 并轮询云侧执行状态；
8. 超时状态记为 `UNKNOWN`，不会自动重试；
9. 通过 CLS 只读验证后，Incident 才闭环。

服务可以配置：

```json
{
  "provider": "tencent_tat",
  "tool_name": "rollback_release",
  "action_policy": {"rollback_release": "approval"},
  "tencent_tat": {
    "region": "ap-guangzhou",
    "command_id": "cmd-rollback-release",
    "instance_ids": ["ins-xxxxxxxx"]
  }
}
```

`tencent_tat` 调用预先登记的 TAT 命令和实例列表。不要把 TAT 任意命令直接加入 Agent 工具白名单；由独立 Recovery MCP 封装为固定参数、可审计的业务动作。

参考：

- [腾讯云 CLS MCP 官方文档](https://cloud.tencent.com/document/product/614/118699)
- [Tencent 官方 cls-mcp-server](https://github.com/Tencent/cls-mcp-server)
- [腾讯云监控告警回调](https://cloud.tencent.com/document/product/248/50409)
- [腾讯云 TAT MCP](https://cloud.tencent.com/developer/mcp/server/11729)
