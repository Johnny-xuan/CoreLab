# 开发说明

## 目录

```text
backend/          FastAPI、SQLAlchemy models、services、Alembic migrations
frontend/         Vue 3 单页应用
agent/            GPU 主机 agent
shared/protocol/  backend-agent Pydantic 消息协议
deploy/           Docker Compose、Caddy、安装脚本
```

## 安装依赖

```bash
uv sync --frozen --all-packages
cd frontend
pnpm install --frozen-lockfile
```

Python workspace 共用根目录的 `uv.lock`。前端使用自己的 `pnpm-lock.yaml`。修改依赖后应提交对应 lockfile。

## 运行测试

```bash
make check
make test-python
make test-frontend
```

数据库相关改动使用一次性服务运行完整后端测试：

```bash
make test-integration
```

如果测试被中断，使用 `make test-integration-down` 清理容器。

## 本地界面调试

先按部署指南启动 Compose 栈，再运行：

```bash
cd frontend
pnpm dev
```

访问 `http://localhost:5173`。Vite 会把 API 和 WebSocket 转发到本机 80 端口。直接修改 backend 时，也可以在 `backend/` 使用 `uv run uvicorn corelab_backend.main:app --reload`，但需要自行提供 `CORELAB_DATABASE_URL`、`CORELAB_MIGRATION_DATABASE_URL` 和 `CORELAB_REDIS_URL`。

## 迁移与协议

数据结构改动必须通过 Alembic migration 表达，不能只修改 SQLAlchemy model。migration 使用全权限数据库账户，应用请求使用受限账户；新增 SQL 行为要在两种权限下都验证。

backend 与 agent 之间的新消息先加入 `shared/protocol`，再实现发送端和接收端，并覆盖错误版本、未知消息和断线重连。不要让两端通过未建模的任意字典形成隐式协议。
