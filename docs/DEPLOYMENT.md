# 中央服务器部署

## 架构

所有招聘者安装同一份 Chrome 扩展。扩展只连接一个 HTTPS API；API、候选人同步 Worker、通知 Worker、数据保留 Worker 共用 PostgreSQL。候选人业务数据和最新聊天快照保存在受限飞书多维表格，PostgreSQL 只承担设备身份、幂等、重试、水位、附件引用和必要审计。

## 生产环境变量

- `APP_ENV=production`
- `SECRET_KEY`：至少 32 字节随机值
- `POSTGRES_PASSWORD`：独立强密码
- `PLUGIN_COMPANY_CODE`：部署所属公司代码
- `PUBLIC_WEB_URL=https://招聘后台域名`
- `FEISHU_MODE=real`
- `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_ENCRYPT_KEY`、`FEISHU_VERIFICATION_TOKEN`：均使用飞书开放平台真实配置
- `FEISHU_REDIRECT_URI=https://API域名/api/v1/auth/feishu/callback`
- `FEISHU_BITABLE_APP_TOKEN`、`FEISHU_BITABLE_CANDIDATE_TABLE_ID`：受限多维表格及候选人表
- `CORS_ORIGINS=["https://招聘后台域名"]`：只列精确 HTTPS 来源，不使用通配符
- 数据期限：`CANDIDATE_CACHE_DAYS=30`、`DIAGNOSTIC_RETENTION_DAYS=30`、`EVENT_RETENTION_DAYS=90`、`AUDIT_RETENTION_DAYS=180`、`FAILED_TASK_RETENTION_DAYS=30`

`.env` 只存在服务器，不提交 Git。飞书 OAuth 回调域名必须与开放平台配置完全一致，飞书应用可见范围应限制为公司招聘成员。

### 本项目域名规划

本项目只使用现有域名 `ai.wuxistar.com`，后台和 API 同源部署：

- 管理后台：`https://ai.wuxistar.com`
- API：`https://ai.wuxistar.com/api/v1`
- 飞书 OAuth 回调：`https://ai.wuxistar.com/api/v1/auth/feishu/callback`

`ai.wuxistar.com` 已指向服务器 `101.35.15.22`，无需新增 DNS 记录。Nginx 在同一个 HTTPS 站点中提供前端并将 `/api/` 反代到内部 API。

## 启动

```bash
docker compose up -d --build postgres api worker candidate-sync-worker data-retention-worker
curl -f https://API域名/api/v1/health
curl -f https://API域名/api/v1/ready
```

API 容器会先执行 Alembic 迁移，健康后三个 Worker 才启动。服务配置为 `restart: unless-stopped`。公网入口应由 Nginx/Caddy/云负载均衡终止 TLS，只代理 API 所需路径，并设置请求体、超时和访问日志脱敏策略。仓库的 Nginx 模板对登录、普通插件请求和上传分别限流；上线时仍需按实际并发压测后调整，不能直接取消限制。

## 无 Docker 的 systemd 部署

当前 `databoard-server` 使用服务器已有的 PostgreSQL 14 和 Python 3.10，目录约定如下：

- 当前版本：`/home/ubuntu/apps/recruitment-collab/current`
- 历史版本：`/home/ubuntu/apps/recruitment-collab/releases/<时间戳>`
- 共享虚拟环境：`/home/ubuntu/apps/recruitment-collab/shared/venv`
- 生产配置：`/home/ubuntu/apps/recruitment-collab/shared/.env`，权限 `0600`
- API：仅监听 `127.0.0.1:18082`

`infra/systemd/` 中的四个服务模板对应 API、候选人同步、通知和数据清理进程。上传新版本后先安装依赖并迁移，再切换 `current` 软链接和重启服务：

```bash
python -m pip install --upgrade /home/ubuntu/apps/recruitment-collab/releases/<时间戳>/apps/api
cd /home/ubuntu/apps/recruitment-collab/releases/<时间戳>
# 迁移必须带上生产 DATABASE_URL，否则 alembic 会静默落在 cwd 的 sqlite 开发库上
DATABASE_URL=$(grep '^DATABASE_URL=' /home/ubuntu/apps/recruitment-collab/shared/.env | head -1 | cut -d= -f2- | tr -d '"') \
  python -m alembic -c apps/api/alembic.ini upgrade head
# 扩展下载只允许从这一处共享发布目录读取。每次部署都必须通过校验脚本
# 原子更新 ZIP 与元数据，不能把 dist、Downloads 或某个历史 release 当下载源。
python scripts/publish_extension_release.py \
  --package artifacts/recruitment-collab-extension.zip \
  --metadata artifacts/extension-release.json \
  --target-package /home/ubuntu/apps/recruitment-collab/shared/extension/latest.zip \
  --target-metadata /home/ubuntu/apps/recruitment-collab/shared/extension/extension-release.json
ln -sfn /home/ubuntu/apps/recruitment-collab/releases/<时间戳> /home/ubuntu/apps/recruitment-collab/current
sudo systemctl restart recruitment-collab-api recruitment-collab-candidate-worker recruitment-collab-notification-worker recruitment-collab-retention-worker
```

域名上线前，可从已配置 SSH 别名的本机建立临时隧道：

```bash
ssh -N -L 127.0.0.1:8000:127.0.0.1:18082 databoard-server
```

此时扩展 API 地址使用 `http://localhost:8000/api/v1`。隧道只适合单机测试；多人正式使用必须改为 HTTPS 域名，并同步更新服务器 `FEISHU_REDIRECT_URI`、扩展连接设置和飞书开放平台回调地址。不要将 PostgreSQL 5432 暴露到公网。

## 当前阶段：先本地真实验收

在域名尚未确定时保持以下配置，不提前开放公网入口：

- 服务器 API 继续只监听 `127.0.0.1:18082`。
- 本机建立 SSH 隧道后，扩展连接 `http://localhost:8000/api/v1`。
- 飞书开放平台 OAuth 回调暂用 `http://localhost:8000/api/v1/auth/feishu/callback`。
- 只使用测试招聘账号和明确同意用于验收的候选人会话；截图仍只允许聊天区域。

本地真实验收必须依次验证：扩展飞书登录、BOSS 招聘者识别、四项身份查重静默/命中、点击候选人即建行且不伪造外发事件、状态与时间、同人多岗位、飞书字段同步、最新聊天快照、断网重试、浏览器重启后的账号候选人锚点增量补扫。上述项目通过前，不切换域名、不为其他招聘者分发扩展。

## 待域名确定后的上线清单

收到正式 API 域名后，按以下顺序完成上线，不能只修改扩展地址：

1. 确认 `ai.wuxistar.com` 仍解析到 `101.35.15.22`，确认云防火墙仅开放 80、443 和受限来源的 22；PostgreSQL 5432、内部 API 18082 保持不开放。
2. 为域名申请并安装有效 TLS 证书，Nginx 仅将所需 `/api/v1/` 请求代理到 `127.0.0.1:18082`，设置上传大小、超时、安全响应头和脱敏访问日志。
3. 将服务器设置为 `PUBLIC_WEB_URL=https://ai.wuxistar.com`、`FEISHU_REDIRECT_URI=https://ai.wuxistar.com/api/v1/auth/feishu/callback`、`CORS_ORIGINS=["https://ai.wuxistar.com"]`；前端构建时使用 `VITE_API_BASE_URL=https://ai.wuxistar.com/api/v1`，并将 `apps/web/dist` 发布到服务器 `/var/www/recruitment-collab`。
4. 在飞书开放平台添加完全一致的 HTTPS OAuth 回调地址；需要事件订阅时，再配置 `/api/v1/integrations/feishu/events` 和 `/card-callback`，不能复用 OAuth 地址。
5. 重启四个 systemd 服务，验证 `/api/v1/health`、`/api/v1/ready`、生产设备鉴权和飞书目标表读写。
6. 在扩展“连接设置”中改为 `https://ai.wuxistar.com/api/v1`，确认扩展只新增该精确 HTTPS Origin 权限；移除对本地隧道的依赖。
7. 用一名招聘者完成端到端冒烟测试，再逐步分发给其他招聘者；检查设备与 BOSS 招聘账号归属、幂等行键、截图替换及数据库清理任务。
8. 上线后建立 PostgreSQL 备份、恢复演练、证书续期、服务存活和飞书同步失败监控；保留至少一个可回滚的历史发布目录。

域名切换完成的判定标准：关闭本机 SSH 隧道后，扩展仍能登录、查重、同步、上传快照和续签设备令牌；服务器数据库及内部端口仍不可从公网直接访问。

## 招聘者首次使用

1. 安装相同的 `apps/extension/dist`。
2. 在“连接设置”填写中央服务器 `https://.../api/v1`；扩展只申请该服务器 Origin 的权限。
3. 招聘人员登录自己负责的 BOSS 账号并打开沟通页，在扩展中点击“使用飞书登录”。未被占用的 BOSS 账号会在首次登录时自动与该飞书成员绑定；一个飞书成员同一时间只能负责一个 BOSS 账号。
4. 飞书 OAuth 验证成功后重新打开扩展，扩展轮询一次性授权并保存设备访问令牌；每次同步都会核对当前页面 BOSS 姓名和有效绑定。
5. 如果 BOSS 账号已经属于其他成员，只有管理员能在“BOSS 账号”页从应用可见通讯录选择新负责人。

管理员替换负责人后，旧负责人的全部扩展设备立即撤销；新负责人重新登录扩展即可继承该 BOSS 账号已有的候选人和同步记录。应用可见范围内的普通飞书成员可以登录后台并下载扩展；没有绑定时只能看到安装与绑定引导，不能读取任何候选人数据。成员在未被占用的 BOSS 沟通页使用当前飞书账号登录扩展后会自动完成首次绑定并开始同步；已经属于其他成员的账号不会被扩展抢占，必须由管理员替换。已经授权的扩展暂时离线不会隐藏历史数据，退出或撤销所有扩展授权后会回到待连接状态。管理员可直接查看公司数据。飞书应用需要开通读取通讯录的权限，后台只读取应用可见范围，并且只持久化实际登录或被选择成员的 open_id、user_id 和姓名。

员工离职或设备遗失时，将对应 `PluginDevice` 撤销即可，不需要重新发布扩展。刷新令牌只以哈希形式存入服务器；飞书个人 OAuth Token 仅在回调请求内使用，获取身份后立即丢弃。

## 数据容量

飞书同步成功后，数据库立即清空同步载荷。候选人缓存超过 30 天且飞书记录存在时会自动去除姓名、年龄、年限、学历等可识别字段。数据清理 Worker 默认每 30 天执行一次（`RETENTION_RUN_INTERVAL_DAYS=30`），调整清理间隔或保留期限只需修改环境变量并重启 `data-retention-worker`；不要手工修改生产表。
