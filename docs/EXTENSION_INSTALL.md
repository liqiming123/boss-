# 插件安装

```bash
pnpm --filter @recruitment/extension build
```

Chrome 打开 `chrome://extensions`，启用开发者模式，选择“加载已解压的扩展程序”，目录为 `apps/extension/dist`。打开插件 Popup，使用开发账号绑定，或走设备绑定码流程；设置页可修改 API 地址，生产地址必须是 HTTPS。

默认权限只有本地 API 与 Mock 站点，不申请 `<all_urls>`。真实 BOSS 域名只有在现场验证并更新 manifest 后才能启用。

