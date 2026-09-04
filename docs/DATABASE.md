# 数据库

迁移 `0001_initial` 建立基础业务表；`0003_candidate_conversation_times` 增加首次沟通和最近互动时间；`0004_candidate_sync_outbox` 增加可合并、可重试的候选人同步 Outbox；`0006_candidate_identity_status_snapshot` 增加四要素身份、岗位级行键、状态、飞书截图令牌与补扫水位；`0007_device_identity_retention` 增加飞书设备登录和候选人缓存最小化标记；`0009_resume_attachments` 是已停用的历史简历附件元数据迁移，其列由 `0013_remove_redundant_columns` 删除；`0010_duplicate_lookup_alerts` 增加点击查重的最小化冷却、证据升级和通知版本状态；`0011_remove_legacy_device_codes` 删除 OAuth 前遗留设备码表；`0012_remove_unused_settings` 删除没有执行路径的设置项；`0013` 删除冗余列；`0014_resume_attachments_reenabled` 恢复经员工明确点击预览后才使用的简历附件元数据；`0015_structured_chat_summary` 为兼容上一版本暂时增加聊天元数据；`0016_remove_chat_summary` 删除结构化聊天列，恢复仅保留聊天框截图附件。

`0014` 在 PostgreSQL 上会先将 Alembic 的 `version_num` 从 32 字符扩为 64 字符，以容纳现有长迁移标识；该调整只影响迁移元数据，不影响业务表。

关键唯一约束包括来源键、消息事件幂等键、冲突键、通知 Outbox 幂等键、点击提醒键、每个候选人来源唯一的同步 Outbox、招聘账号补扫水位，以及排序后的排除来源对。图片二进制不进入数据库，简历不进入采集或上传流程。

飞书是候选人业务数据的长期权威来源。数据库长期保留设备身份、哈希键、飞书记录 ID、幂等状态、水位和必要审计，不保存聊天正文或图片字节。`data_retention_worker` 每小时执行一次可配置清理；同步成功的候选人 Outbox 和通知 Outbox 会立即清空载荷。

升级：`make migrate`。新迁移：`make migration`。生产 Docker Compose 在 API 进程启动前显式执行 Alembic；应用导入本身不会修改数据库结构。
