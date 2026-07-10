# Deployment Files

- `docker-compose.yml`：MySQL、Redis、backend、Caddy 和可选 cloudflared
- `.env.example`：部署配置模板，不包含可用凭据
- `install.sh`：控制平面安装入口
- `install-agent.sh`：由 backend 提供给 GPU 主机的 agent 安装器
- `mysql-init/`：创建受限运行数据库账户

安装、网络、TLS、备份和升级步骤见 [部署指南](../docs/deployment.md)。
