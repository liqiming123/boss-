# 测试报告

执行日期：2026-09-03（Asia/Shanghai）。

实际执行结果：

- `pytest apps/api/tests`：58 通过，0 失败。覆盖点击查重、可靠外发后同步、旧版误拒绝重验、快照失败状态、简历五种允许格式、双向提醒、状态只前进、失败载荷清理和生产配置 fail-closed。
- `pnpm --filter @recruitment/web test`：1 通过；`pnpm --filter @recruitment/extension test`：41 通过，覆盖当前聊天区域识别、未发送快捷语排除、可靠外发、结构化摘要、预览附件范围、自动补扫开关、页面控制器、消息观察和发送队列；共享 API 客户端 3 通过。
- `pnpm --filter @recruitment/extension test:e2e`：1 通过；使用真实扩展构建、Mock 招聘站和 Chromium，验证仅在选中候选人变化后触发检查。
- `ruff check apps/api`：通过。
- `mypy apps/api/src`：21 个源文件通过，0 问题。
- `pnpm -r typecheck`：共享客户端、Web、Extension、Mock Site 全部通过。
- `pnpm -r lint`：全部工作区通过。
- `pnpm -r build`：共享客户端、Web、Extension、Mock Site 全部构建成功；Web 主入口 JS 约 245 KB，无大 chunk 告警；Extension content script 约 28 KB。
- `alembic upgrade head`：从空 SQLite 数据库连续升级至唯一 head `0016_remove_chat_summary`，实际通过；并完成 `head → 0015_structured_chat_summary → head` 往返验证。

未运行：Docker Compose（本机无 Docker CLI）、PostgreSQL 容器集成、真实飞书企业应用、真实 BOSS 页面。它们不得视为已验收。
