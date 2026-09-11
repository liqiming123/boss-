# API

基址 `/api/v1`，OpenAPI 位于 `/docs`。BOSS 扩展使用飞书 OAuth 签发的可撤销设备凭据；管理后台使用飞书 OAuth 后的 `HttpOnly` 会话 Cookie，前端不管理 Bearer token。

核心流程：

1. `POST /plugin/context/check` 在切换候选人时使用四项身份、同名同岗位和页面可见的 `native_communications`（BOSS 原生同事沟通招聘者、岗位、时间）查重；不创建候选人来源、跟进、招聘事件、正式冲突或候选人飞书行。命中时创建最小化点击提醒状态和通知 Outbox，立即私聊双方，24 小时冷却且证据升级立即补发。
2. `POST /plugin/engagements/message-sent` 仅在招聘人员消息确认发送后幂等登记候选人、跟进关系、`MESSAGE_SENT` 事件和飞书同步 Outbox。
3. `POST /plugin/conversations/sync` 按候选人打开或自动补扫触发同步，不要求外发证据；它只创建/更新候选人来源和飞书同步 Outbox，不创建虚假的跟进关系或 `MESSAGE_SENT` 事件。`GET/PUT /plugin/conversations/checkpoint` 读取或推进账号级补扫水位；`POST /plugin/conversations/restart` 将指定账号写入历史重扫锚点 `1970-01-01T00:00:00Z`，只有完整遍历成功后才推进水位。
4. `POST /plugin/conversations/{source_id}/snapshot` 以 multipart 流式接收最多 20 个、每个不超过 15MB 的聊天长图分片，上传飞书后只保存附件令牌与摘要。
5. `POST /plugin/conversations/{source_id}/resume` 仅由员工点击预览后调用，接收 PDF、Word、JPG 或 PNG 附件（限 25MB）；服务端校验哈希并只保留飞书附件令牌、文件名和状态。拿不到原附件时，扩展上传简历预览截图到同一字段。
6. `PUT /plugin/conversations/snapshot-status` 登记聊天快照采集失败或被用户操作中断；成功附件不会被后续失败状态覆盖。
7. `GET /plugin/feishu-binding/status` 查询当前 BOSS 姓名是否已绑定飞书；`POST /plugin/feishu-binding/start` 发起绑定或解绑 OAuth。回调为 `GET /auth/feishu/callback`。

7. `POST /plugin/events` 记录后续主动动作；`idempotency_key` 必须唯一。`POST /plugin/interviews` 记录约面。
8. `/conflicts/*` 处理知悉、排除、继续、申请转交和关闭；`/admin/*` 提供后台资源、设置、通知重试与审计查询。

后台统计补充：`GET /admin/operations` 的 `metrics.message_sent_total` 是当前权限范围内成功发送的主动消息总数，`communication_summary` 按招聘者和 BOSS 账号返回消息次数、覆盖候选人数和最近发送时间；`GET /admin/candidate-sources` 及候选人详情同时返回 `message_sent_count`、`last_message_sent_at`。这些字段均由 PostgreSQL 的 `MESSAGE_SENT` 事件聚合，不依赖飞书表。

岗位响应中的 `job_mapping.status=RAW` 表示 BOSS 原始岗位已可直接使用，`normalization=OPTIONAL` 表示标准岗位归一化仅用于统一报表；它不是同步失败。

错误统一为 `{ "error": { "code", "message", "request_id", "details" } }`。插件所属公司由服务端 `PLUGIN_COMPANY_CODE` 确定（只有一家启用公司时可自动确定），客户端不能传入公司或角色。
# 管理后台与扩展设置

- `GET /api/v1/admin/settings`：管理员读取公司设置及只读连接信息。
- `PATCH /api/v1/admin/settings`：管理员更新 `catchup_enabled`、`notify_on_contact`。
- `GET /api/v1/plugin/settings`：已鉴权扩展读取公司级自动补扫开关；响应不包含密钥、Cookie 或页面内容。
