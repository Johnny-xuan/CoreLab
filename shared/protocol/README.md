# CoreLab Protocol

backend 与 agent 共用的 Pydantic 消息模型。这里定义协议版本、消息 envelope、心跳、GPU 遥测、账户扫描、策略同步、脚本事件和 RPC 请求/响应。

协议改动需要同时更新两端，并为兼容性和非法消息补测试。

```bash
uv sync --frozen --all-packages
cd shared/protocol
uv run mypy corelab_protocol
```
