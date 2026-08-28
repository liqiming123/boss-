# 架构决策

- 候选人来源独立保存；姓名和岗位永不触发永久合并。
- 查看页面仅更新来源 `last_seen_at`，主动事件才创建 Engagement/Conflict/Outbox。
- PostgreSQL 是唯一核心存储；通知使用事务 Outbox，不引入消息队列。
- 插件仅提取最小字段并通过 API 工作，不读取 Cookie、不拦截请求、不上传 HTML。
- BOSS 选择器无现场证据时保持空配置，避免把猜测当成适配结果。

