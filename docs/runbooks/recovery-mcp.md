# Recovery MCP

Recovery MCP 只有 `rollback_release` 一个写工具，范围固定为 `demo-shop/staging/order-api` 的 `v2 → v1`。

API 不向它发送用户 JWT。Worker 使用已批准计划哈希、Approval ID、时间戳和 nonce 生成 HMAC；服务校验五分钟时间窗、持久 nonce 防重放、记录版本和实际 `/version` 后，才以固定参数列表调用 Podman Compose。任何校验失败均不会调用 Podman。

启动：`make recovery-mcp`；就绪探针：`http://127.0.0.1:8080/health/ready`。不要将此端口暴露到非本机网络。
