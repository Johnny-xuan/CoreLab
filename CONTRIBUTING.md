# Contributing to CoreLab

CoreLab 同时涉及 Web、数据库和 Linux 主机操作。提交改动前，先确认自己修改的是控制平面、agent，还是二者之间的协议；跨边界的行为需要同时补测试。

## 开发环境

- Python 3.11
- uv
- Node.js 20 或更高版本
- pnpm 9 或更高版本
- Docker Compose（数据库集成测试需要）

```bash
uv sync --frozen --all-packages
cd frontend && pnpm install --frozen-lockfile
```

前端本地调试可以先启动 Docker Compose 栈，再在 `frontend/` 运行 `pnpm dev`。Vite 会把 `/api` 和 `/ws` 转发到本机 80 端口。

## 提交前检查

```bash
make check
make test-python
make test-frontend
```

涉及模型、迁移、事务、权限或调度逻辑时，再运行：

```bash
make test-integration
```

新增 Alembic 迁移时必须同时考虑全权限迁移账户和受限运行账户。`audit_log` 的 append-only 约束、危险能力备注要求、预约冲突检查和服务器审批门禁不能只依赖前端。

## 提交内容

- 不要提交 `.env`、token、真实账号、SSH 私钥、访问日志或本地数据库。
- 不要提交 `frontend/dist`、`backend/static` 等构建产物。
- 测试数据使用明显的虚构身份和无效凭据。
- 修复用户可见行为时，测试应覆盖成功路径以及权限、冲突或过期等拒绝路径。
- 改动 backend-agent 协议时，先修改 `shared/protocol`，再同步两端实现。

建议一个提交只处理一个可说明、可验证的行为。Pull request 请写清改动原因、验证命令和仍未验证的环境条件。
