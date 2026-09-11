# 测试报告

执行日期：2026-09-03（Asia/Shanghai）。

实际执行结果：

- `pytest apps/api/tests`：58 通过，0 失败。覆盖点击查重、可靠外发后同步、旧版误拒绝重验、快照失败状态、简历五种允许格式、双向提醒、状态只前进、失败载荷清理和生产配置 fail-closed。
- 最新完整测试由 `make test` 执行：API 74 项、Web 1 项、扩展 61 项、扩展端到端 1 项全部通过；另有 lint、Python/TypeScript 类型检查和 diff 检查通过。覆盖 BOSS 页面兼容、登录后沟通列表扫描、虚拟列表零尺寸兜底、点击后候选人与岗位身份校验、新候选人表格缺失检测、时间不一致检测、未读标记消失检测、点击即同步、真实外发事件拆分、查重提醒、按账号候选人锚点的增量列表补扫、跨午夜候选人补扫、更新时间幂等比较、消息观察、移动端拒绝状态识别、截图/附件队列和失败重试。
- `pnpm --filter @recruitment/extension test:e2e`：1 通过；使用真实扩展构建、Mock 招聘站和 Chromium，验证仅在选中候选人变化后触发检查。
- `ruff check apps/api`：通过。
- `mypy apps/api/src`：21 个源文件通过，0 问题。
- `pnpm -r typecheck`：共享客户端、Web、Extension、Mock Site 全部通过。
- `pnpm -r lint`：全部工作区通过。
- `pnpm -r build`：共享客户端、Web、Extension、Mock Site 全部构建成功；Web 主入口 JS 约 245 KB，无大 chunk 告警；Extension content script 约 28 KB。
- `alembic upgrade head`：从空 SQLite 数据库连续升级至唯一 head `0016_remove_chat_summary`，实际通过；并完成 `head → 0015_structured_chat_summary → head` 往返验证。

未运行：Docker Compose（本机无 Docker CLI）、PostgreSQL 容器集成、真实飞书企业应用、真实 BOSS 页面。它们不得视为已验收。
