# CoreLab Agent

运行在 Linux GPU 主机上的 WebSocket agent。它采集 NVIDIA 遥测和本机账户信息，并执行经过后端授权的脚本、SSH、公钥、账号和进程操作。

正式安装应使用 CoreLab 管理页生成的命令，以便绑定正确的 server id 和一次性 enrollment token。开发测试：

```bash
uv sync --frozen --all-packages
cd agent
uv run pytest
uv run mypy corelab_agent
```

agent 的系统权限决定了危险操作的实际影响范围。上线前请阅读 [安全策略](../SECURITY.md) 和 [部署指南](../docs/deployment.md)。
