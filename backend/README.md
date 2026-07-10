# CoreLab Backend

FastAPI 控制平面，提供 REST API、浏览器与 agent WebSocket、预约调度、审计和 MySQL persistence。

```bash
uv sync --frozen --all-packages
cd backend
uv run pytest
uv run mypy corelab_backend
```

生产镜像由根目录 `backend/Dockerfile` 构建，并在启动时自动执行 Alembic migration。整体运行方式见 [部署指南](../docs/deployment.md)。
