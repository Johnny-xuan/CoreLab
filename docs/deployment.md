# 部署指南

## 控制平面要求

- Linux 主机或能够稳定运行 Docker Desktop 的开发机
- Docker Engine 与 Compose v2
- OpenSSL、curl、Git
- 至少 2 GB 可用内存；MySQL 默认 buffer pool 为 512 MB
- 受信 LAN 地址，或已经配置 TLS 的域名/反向代理

GPU 不需要和控制平面在同一台机器上。控制平面通过 HTTPS/WSS 与各台 agent 通信。

## 安装

推荐先自行安装 Docker，再运行：

```bash
git clone https://github.com/Johnny-xuan/CoreLab.git
cd CoreLab
./deploy/install.sh --no-auto-install
```

脚本会创建 `deploy/.env`、构建镜像、执行 Alembic migration，并等待健康检查。已有 `.env` 会被保留；`--force` 会重新生成凭据，只适用于没有旧数据库的全新安装，否则持久化数据库中的账号密码会与新环境文件不一致。

手动安装时，复制 `deploy/.env.example` 并替换四个 `__GENERATE_ME__` 占位值：

```bash
cd deploy
cp .env.example .env
docker compose up -d --build
docker compose ps
```

后端容器每次启动前都会运行 `alembic upgrade head`。`/healthz` 表示进程存活，`/readyz` 还会检查依赖服务。

## 网络与 TLS

默认 Caddy 监听 HTTP 80，适合隔离的实验室局域网。跨公网部署时，请让 Caddy 或上游反向代理终止 TLS，并确保以下路径都使用同一外部地址：

- `/`：前端
- `/api/*`：REST API
- `/ws/user`：浏览器实时推送
- `/ws/agent`：agent WebSocket

在 `deploy/.env` 设置：

```dotenv
BACKEND_PUBLIC_URL=https://corelab.example.org
CORS_ORIGINS=
```

agent token 位于 WebSocket 查询参数中。默认 Caddy 配置不写访问日志；如果上游代理开启日志，应删除或脱敏查询参数。

Compose 把 MySQL 映射到 `127.0.0.1:3307` 供本机维护使用。正式环境若不需要宿主机直连，可以删除 `mysql.ports`。Redis 没有宿主机端口，只在 Compose 内部网络可见。

Cloudflare Quick Tunnel 只适合临时联调：

```bash
./deploy/install.sh --no-auto-install --tunnel cloudflare
```

随机 tunnel URL 不应当被当作稳定生产入口。

## 初始化与邀请

第一次访问会进入 setup wizard。创建实验室和首位管理员后，setup endpoint 会永久关闭，不能再次初始化。

用户管理页创建的是注册链接，而不是预建账号。管理员选择角色和过期时间，把链接通过受信渠道发送给用户；用户自行提交身份、密码和可选 SSH 公钥。项目本身不提供邮件发送服务。

## 接入 GPU 主机

正式 agent 主机建议满足：

- Linux、Python 3.11 或更高版本、systemd
- 已安装 NVIDIA 驱动且 `nvidia-smi` 可用
- 能访问 `BACKEND_PUBLIC_URL`
- root 或受控 sudo 权限，用于 systemd 安装以及明确启用的主机操作

在管理页创建服务器后，将页面生成的 inspect-first 命令复制到目标主机。agent 首次回连只证明它持有一次性 token，仍需管理员在服务器详情页批准。批准前不要把该主机视为已接入。

无 root 环境可以使用 `--user-mode`，非 GPU 联调可以使用 `--mock`；这两种模式不能代替真实主机验收。

## 数据与备份

默认持久化目录为 `deploy/data`，可以通过 `CORELAB_DATA_DIR` 改到独立磁盘。需要备份的内容至少包括 MySQL 和 `deploy/.env`，两者应分开加密保存。

一致性较好的逻辑备份可以在服务运行时执行：

```bash
cd deploy
docker compose exec -T mysql sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction corelab' \
  > corelab.sql
```

恢复演练应在独立环境完成。Redis、backend 日志和 Caddy 状态都不是业务数据库的替代品。

## 升级与回退

```bash
git pull --ff-only
cd deploy
docker compose up -d --build
docker compose ps
```

升级前备份 MySQL。Alembic migration 会自动前进，但应用回退不保证数据库也能无损降级；需要回退时优先恢复与旧版本匹配的数据库备份。

## 上线前检查

- 使用非关键 GPU 主机完成注册、回连、审批和离线恢复。
- 核对 GPU 数量、显存、Linux 账户和 authorized keys 发现结果。
- 用普通用户跑通邀请注册、账户关联、预约、脚本输出和取消。
- 确认危险 capability 默认关闭，逐项评估后再启用。
- 检查 HTTPS/WSS、备份恢复、日志脱敏和数据库网络边界。
- 模拟 backend 与 agent 重启，确认预约和脚本状态能够收敛。
