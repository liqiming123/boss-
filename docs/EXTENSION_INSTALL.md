# 插件安装

```bash
pnpm --filter @recruitment/extension build
```

Chrome 打开 `chrome://extensions`，启用开发者模式，选择“加载已解压的扩展程序”，目录为 `apps/extension/dist`。插件无需内部账号登录，会自动读取 BOSS 右上角招聘人员姓名。打开扩展弹窗可绑定或解绑飞书个人身份；设置页可修改 API 地址，生产地址必须是 HTTPS。

当前无域名验收阶段，先运行 `ssh -N -L 127.0.0.1:8000:127.0.0.1:18082 databoard-server`，设置页填写 `http://localhost:8000/api/v1`。域名正式上线后改为 `https://ai.wuxistar.com/api/v1`，无需重新打包含固定服务器地址的扩展包。

权限仅覆盖本地 API、Mock 站点和 BOSS 域名，不申请 `<all_urls>`。`unlimitedStorage` 只用于网络失败时临时保留最多 10 个待上传聊天快照，上传成功即清理。
