# 招聘重复候选人识别与协同系统

这是一个给多招聘账号团队使用的内部协同平台。系统把 BOSS 直聘页面上的最小必要候选人信息、团队成员的飞书身份、飞书多维表和后台管理页串起来，用来减少重复沟通、重复跟进和候选人归属不清的问题。

核心闭环是：

1. 每个 BOSS 招聘账号绑定一个飞书成员。
2. 浏览器扩展在候选人沟通页做最小化字段提取。
3. 发消息前只读查重，发现四项身份完全一致、同名同岗位或 BOSS 原生同事沟通提示时，立即在页面提醒。
4. 用户实际发送消息后，后端才创建候选人业务记录、写入飞书多维表，并通过事务 Outbox 发送提醒。
5. 用户授权后，系统可以同步聊天区域截图和简历附件到受限飞书多维表；API 不落盘图片字节、Cookie、密码或完整 HTML。

扩展兼容 Chrome 和 Microsoft Edge，使用 Chromium MV3。发送消息后的聊天截图会在登记成功并等待气泡渲染后采集，详细流程见 [`docs/BOSS_FEISHU_FLOW.md`](docs/BOSS_FEISHU_FLOW.md)。

## 项目结构

```text
apps/api                 FastAPI 后端、领域逻辑、SQLAlchemy 模型、Alembic 迁移和后台 Worker
apps/extension           Chrome/Edge MV3 浏览器扩展，负责 BOSS 页面识别、提醒、同步和截图入口
apps/web                 Vue 管理后台，用于账号分配、候选人查看、配置和扩展下载
apps/mock-recruitment-site 本地模拟招聘站点，用于开发和端到端测试
packages/api-client      前后端共用的 TypeScript API Client 和 OpenAPI 产物
docs                     架构、部署、飞书配置、数据库、接口和运维文档
infra                    Nginx、Docker、systemd 等部署配置
scripts                  构建发布、OpenAPI 生成、飞书表配置和隔离采集脚本
artifacts                已构建的扩展发布包和发布元数据
```

## 架构说明

```text
BOSS/Mock 页面 -> MV3 Content Script + Shadow Panel -> Extension Service Worker -> FastAPI API
                                                                                  |-> PostgreSQL
Vue 管理后台 ---------------------------------------------------------------------|
                                                                                  |-> Outbox Worker -> Mock/Real Feishu
                                                                                  |-> Candidate Sync Worker -> Feishu Bitable
                                                                                  |-> Data Retention Worker
```

领域代码保持独立，不依赖 FastAPI、SQLAlchemy、HTTP 或飞书 SDK。API 层只负责协议、认证、RBAC 和错误映射；Application 层编排业务事务；Infrastructure 层适配数据库、飞书和 Worker。数据库结构变更通过 Alembic 迁移落地，不手工改生产库。

## 快速启动

需要 Python 3.12、Node 20+、pnpm 10 和 Docker Compose。

```bash
cp .env.example .env
make bootstrap
docker compose up --build
make seed
```

本地默认地址：

- 管理后台：http://localhost:5173
- API 文档：http://localhost:8000/docs
- Mock 招聘站：http://localhost:5174

Mock 飞书默认启用，消息可在后台的 Mock 飞书页面查看。Docker Compose 会同时运行 API、PostgreSQL、冲突通知 Worker、候选人飞书表同步 Worker 和数据保留 Worker。

纯本地开发也可以分开启动：

```bash
make install
make db-up
make migrate
make seed
make dev-api
make dev-web
make dev-mock-site
make dev-notification-worker
make dev-candidate-sync-worker
make dev-data-retention-worker
```

## 浏览器扩展

构建扩展：

```bash
make build
```

构建完成后，在 Chrome 或 Edge 的扩展管理页打开开发者模式，加载 `apps/extension/dist`。每位招聘者安装同一个扩展，首次使用时配置中央 API 地址并通过飞书 OAuth 登录。飞书身份是设备登录主体，BOSS 右上角姓名只用于核对招聘账号映射。

发布包位于 `artifacts/recruitment-collab-extension.zip`，发布信息位于 `artifacts/extension-release.json`。

## 常用命令

```bash
make test
make lint
make typecheck
make build
make generate-api-client
```

如需单独采集职位列表，可使用隔离的 CDP 工具。它不会写入候选人协同数据，只旁听页面自身职位 API 响应：

```bash
pip install -r scripts/requirements-boss-cdp.txt
make boss-cdp KEYWORD='AI Agent' CITY='101020100'
```

不要把 BOSS 密码、Cookie 或完整 HTML 交给脚本。

## 配置与安全

1. `.env` 只保存在本地或服务器环境中，不提交到仓库。
2. `.env.example` 只放占位值，生产环境需要替换 `SECRET_KEY`、数据库密码、飞书 App Secret、回调地址和多维表配置。
3. BOSS 密码、Cookie、完整 HTML、生产证书、本地数据库和清理备份不会上传到 GitHub。
4. 简历附件和聊天截图只在用户授权流程中流向配置好的受限飞书多维表，后端只保留最新逻辑附件 token、hash、名称和传输状态。
5. 姓名和岗位相似性只作为证据和提醒，不会自动永久合并候选人。

## 文档索引

- 架构总览：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- 本地开发：[`docs/LOCAL_DEVELOPMENT.md`](docs/LOCAL_DEVELOPMENT.md)
- 飞书配置：[`docs/FEISHU_SETUP.md`](docs/FEISHU_SETUP.md)
- API 说明：[`docs/API.md`](docs/API.md)
- 数据库说明：[`docs/DATABASE.md`](docs/DATABASE.md)
- 同步流程：[`docs/SYNC_FLOW.md`](docs/SYNC_FLOW.md)
- 部署说明：[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- 运维手册：[`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- 安全说明：[`docs/SECURITY.md`](docs/SECURITY.md)
- 测试报告：[`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)
- 当前状态：[`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)

真实飞书密钥、生产域名证书、现场采集的 BOSS 脱敏 DOM 诊断和本地备份不在仓库中。公开仓库部署前，请确认生产密钥只存在于服务器环境变量或密钥管理系统中。
