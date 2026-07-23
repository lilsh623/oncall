# 本地开发

本项目采用混合模式：Conda 运行开发命令和宿主机 Recovery MCP，旧 Podman Machine 运行 PostgreSQL、Redis、Milvus、demo、API 和前端容器。

1. 复制 `.env.example` 为 `.env`，替换 JWT、MCP 密钥与百炼 API Key。`RECOVERY_MCP_SECRET` 和 `RECOVERY_APPROVAL_SECRET` 必须不同。
2. 确认 `podman system connection list` 指向原有 Machine，执行 `make infra-up`。
3. 执行 `make migrate`，用 `oncall users create` 创建首个 admin，并用 `oncall knowledge ingest` 导入项目知识。
4. 运行 `make recovery-mcp`（它只监听 `127.0.0.1:8080`），随后执行 `make app-up`。
5. 浏览器打开 `http://127.0.0.1:5173`。开发模式可改用 `cd frontend && npm run dev`。

Recovery MCP 不加入 Compose，因为它需要控制宿主机 Podman；不要把 Podman socket 暴露给任何容器或 Agent。

常见问题：没有配置百炼 Key 时，真实调查会安全地转入 `NEED_HUMAN`；先核对 `.env`。Recovery MCP 未启动时，批准后的执行也会转人工，绝不会尝试任意 shell 命令。
