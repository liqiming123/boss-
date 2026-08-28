# 测试报告

执行日期：2026-08-29（Asia/Shanghai）。

实际执行结果：

- `pytest apps/api/tests -q`：9 通过，0 失败。覆盖标准化、匹配等级、自身排除、状态机、只查看不通知、双向 Outbox、事件幂等、继续原因、Mock Worker 和飞书回调签名时效。
- `pnpm -r test`：Web 1 通过，Extension 3 通过；共享客户端和 Mock Site 当前无独立单元测试，命令以 `--passWithNoTests` 通过。
- `pnpm --filter @recruitment/extension test:e2e`：1 通过，0 失败。使用真实扩展构建、真实 FastAPI、SQLite 开发库、Mock 招聘站和 Chromium，验证登录令牌、页面解析、Shadow 面板和 Claim。
- `ruff check apps/api`：通过。
- `mypy apps/api/src`：16 个源文件通过，0 问题。
- `pnpm -r typecheck`：共享客户端、Web、Extension、Mock Site 全部通过。
- `pnpm -r lint`：全部工作区通过。
- `pnpm -r build`：共享客户端、Web、Extension、Mock Site 全部构建成功。Web 有一个非阻断的大 chunk 警告。
- `alembic upgrade head` 和 Seed：在本机 SQLite 开发数据库实际通过。

未运行：Docker Compose（本机无 Docker CLI）、PostgreSQL 容器集成、真实飞书企业应用、真实 BOSS 页面。它们不得视为已验收。
