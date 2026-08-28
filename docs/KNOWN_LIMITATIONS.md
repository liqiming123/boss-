# 已知限制

- 真实飞书尚未使用企业 App ID/Secret 现场验证；需要用户提供测试应用配置。
- 真实 BOSS 页面 DOM、选择器和平台候选人 ID 作用域尚未现场验证；当前 BossAdapter 安全地返回“未配置”。
- 生产域名、HTTPS 证书和最终 CORS/扩展 host permissions 需要部署方提供。
- 当前管理后台以通用数据表格覆盖所有资源，复杂详情的跨实体聚合和大数据分页需在生产试点后继续优化。
- 当前本机没有可用 Docker CLI，因此 Compose 文件只能做静态审查，不能声明已在本机实际启动。
- E2E 依赖 Playwright Chromium；若浏览器二进制未安装，需要执行 `pnpm exec playwright install chromium`。

