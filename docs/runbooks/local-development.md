# 本地开发

本项目采用混合模式：容器运行基础设施与入站服务（PostgreSQL、Redis、Milvus、etcd、MinIO、Prometheus、Alertmanager、demo、traffic、API、前端）；宿主机运行 app 层（Celery Worker、Observability MCP、Release MCP、Recovery MCP）。

Worker 放在宿主机，是因为它要通过 `127.0.0.1:8080` 直连宿主机 Recovery MCP 执行真实回滚。容器化 Worker 无法通过 Podman Machine 网关回调 macOS 宿主机的 loopback 服务，而 SDD 13.2 禁止把 Podman socket 挂进任何容器。

1. 复制 `.env.example` 为 `.env`，替换 JWT、MCP 密钥与百炼 API Key。`RECOVERY_MCP_SECRET` 和 `RECOVERY_APPROVAL_SECRET` 必须不同。
2. 确认 `podman system connection list` 指向原有 Machine，执行 `make infra-up`。
3. 执行 `make migrate`，用 `oncall users create-admin` 创建首个 admin，并用 `oncall knowledge ingest` 导入项目知识。
4. 执行 `make app-up` 启动容器化 API 和前端。
5. 执行 `make app-host` 在宿主机启动 Worker 与三个 MCP（日志和 PID 写入 `.runtime/host-app/`）。用 `make app-host-down` 停止它们。
6. 浏览器打开 `http://127.0.0.1:5173`。开发模式可改用 `cd frontend && npm run dev`。

Alertmanager 通过 Compose 私有网络中的 `http://api:8000` 调用容器化 API，不依赖 `host.containers.internal`。Recovery MCP 不加入 Compose，因为它需要控制宿主机 Podman；不要把 Podman socket 暴露给任何容器或 Agent。

宿主机进程从 `.env` 文件读取配置，请勿在启动前 `source .env`——shell 会破坏 `ALERTMANAGER_WEBHOOK_SECRETS` 等 JSON 值的引号，导致 pydantic-settings 解析失败。

常见问题：没有配置百炼 Key 时，真实调查会安全地转入 `NEED_HUMAN`；先核对 `.env`。Recovery MCP 未启动时，批准后的执行也会转人工，绝不会尝试任意 shell 命令。
