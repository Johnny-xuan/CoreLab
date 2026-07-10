# Security Policy

## 报告问题

涉及 token、权限绕过、远程命令执行、SQL 注入或个人数据泄露的问题，请使用 GitHub 的私密 Security Advisory 报告，不要在公开 issue 中附带可利用细节、真实凭据或日志。

一般性的加固建议和不含敏感信息的缺陷可以直接提交 issue。

## 受支持版本

项目目前只维护 `main` 上的最新版本。尚未建立长期支持分支或安全补丁回移策略。

## 已有边界

- 密码使用 bcrypt 保存，登录态使用有时限的 JWT。
- enrollment token 和注册链接只保存 SHA-256 摘要，明文只在创建时返回一次。
- agent 首次使用 enrollment token 回连后仍为 pending；管理员批准前，后端只接受心跳，不接受遥测或操作帧。
- 危险 agent 能力默认关闭，启用时要求填写说明，并写入审计日志。
- FastAPI 使用不具备 DELETE 权限的 `corelab_app` 数据库账户；`audit_log` 的 UPDATE 和 DELETE 另有 MySQL trigger 拒绝。
- Caddy 默认关闭访问日志，避免 WebSocket 查询参数中的 agent token 被写入日志。

## 部署责任

默认 Compose 配置面向受信局域网，不是开箱即用的公网安全方案。正式使用至少需要：

- 为 Web、API 和 WebSocket 配置 HTTPS/WSS，并限制管理入口的网络范围。
- 使用 `deploy/.env.example` 生成全新的数据库密码和 JWT secret，不复用示例或开发值。
- 保护 `deploy/.env`、数据库备份、agent 配置和 systemd 日志。
- 如果上游代理开启访问日志，必须删除或脱敏 `/ws/agent` 的查询参数。
- 审核 agent 的 sudoers、systemd 运行用户和能力开关；未使用的危险能力保持关闭。
- 只把 MySQL 绑定到回环或管理网络，不直接暴露到公网。

agent 获得脚本执行、账号管理或进程终止能力后，影响范围等同于它在主机上的系统权限。请先在非关键 GPU 主机验收，再逐台接入生产资源。
