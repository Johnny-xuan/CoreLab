# CoreLab Frontend

Vue 3 + TypeScript 单页应用，包含预约工作区、脚本状态、账户关联、用户与服务器管理、策略、告警和审计界面。

```bash
pnpm install --frozen-lockfile
pnpm dev
pnpm lint
pnpm type-check
pnpm test
pnpm build
```

开发服务器默认监听 5173，并把 `/api` 和 `/ws` 转发到 `http://localhost:80`。生产构建由 backend 多阶段 Dockerfile 完成。
