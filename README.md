# 招聘重复候选人识别与协同系统

面向多招聘账号的内部协同平台。浏览器插件在候选人页面做最小化字段提取，后端以保守规则查询历史：四项身份完全一致、同名同岗位或页面显示 BOSS 原生同事沟通时，页面立即提醒并通过事务 Outbox 私聊双方；点击阶段只保存提醒去重信息，员工实际沟通后才创建候选人业务行与正式冲突。

核心闭环是“每个 BOSS 招聘账号绑定自己的飞书身份 → 发消息前只读查重 → 消息确认发送后同步飞书多维表并发送提醒 → 按用户动作同步聊天区域截图和简历附件”。详细流程见 [`docs/BOSS_FEISHU_FLOW.md`](docs/BOSS_FEISHU_FLOW.md)。

如需单独采集职位列表，可使用隔离的 CDP 工具（不会写入候选人协同数据）：先在一个已登录的 Chrome 中开启远程调试端口，再运行 `pip install -r scripts/requirements-boss-cdp.txt` 和 `make boss-cdp KEYWORD='AI Agent' CITY='101020100'`。工具只旁听页面自身的职位 API 响应，使用 `salaryDesc` 输出 `data/boss/jobs.json` 与同名 CSV；不要把 BOSS 密码、Cookie 或完整 HTML 交给脚本。

## 快速启动

需要 Python 3.12、Node 20+、pnpm 10 和 Docker Compose。

```bash
cp .env.example .env
make bootstrap
docker compose up --build
make seed
```

- 管理后台：http://localhost:5173
- API 文档：http://localhost:8000/docs
- Mock 招聘站：http://localhost:5174
- 每位招聘者安装同一个扩展。首次使用时配置中央 API 地址并通过飞书 OAuth 登录；飞书身份是设备登录主体，BOSS 右上角姓名只用于核对招聘账号映射。

Mock 飞书默认启用，消息在后台“Mock 飞书”页可查看。Docker Compose 同时运行 API、PostgreSQL、冲突通知 Worker、候选人飞书表同步 Worker 和数据保留 Worker；纯本地开发时分别执行 `make dev-notification-worker`、`make dev-candidate-sync-worker` 和 `make dev-data-retention-worker`。扩展执行 `make build` 后，在 Chrome 扩展管理页加载 `apps/extension/dist`。

中央服务器部署见 `docs/DEPLOYMENT.md`，开发人员监控与故障处理见 `docs/OPERATIONS.md`。

常用命令见 `make help` 等价目标列表：`make test`、`make lint`、`make typecheck`、`make build`、`make generate-api-client`。

真实飞书密钥、生产域名/证书和现场采集的 BOSS 脱敏 DOM 诊断不在仓库中，参见 `docs/KNOWN_LIMITATIONS.md`。
