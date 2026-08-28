# 招聘重复候选人识别与协同系统

面向多招聘账号的内部协同平台。浏览器插件在候选人页面做最小化字段提取，后端以保守规则查询历史；只有招聘人员主动跟进时才创建冲突，并通过事务 Outbox 通知双方。

## 快速启动

需要 Python 3.12、Node 20+、pnpm 10 和 Docker Compose。

```bash
cp .env.example .env
make bootstrap
docker compose up --build
make migrate
make seed
```

- 管理后台：http://localhost:5173
- API 文档：http://localhost:8000/docs
- Mock 招聘站：http://localhost:5174
- 开发登录：`admin@example.com` / `dev-admin-2026`
- 招聘人员账号：`xie@example.com`、`jiali@example.com`、`daning@example.com`，密码均为 `dev-recruiter-2026`

Mock 飞书默认启用，消息在后台“Mock 飞书”页可查看。扩展执行 `make build` 后，在 Chrome 扩展管理页加载 `apps/extension/dist`。

常用命令见 `make help` 等价目标列表：`make test`、`make lint`、`make typecheck`、`make build`、`make generate-api-client`。

真实飞书密钥、生产域名/证书和现场采集的 BOSS 脱敏 DOM 诊断不在仓库中，参见 `docs/KNOWN_LIMITATIONS.md`。
