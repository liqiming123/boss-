# 插件安装

```bash
pnpm --filter @recruitment/extension build
```

Chrome 或 Microsoft Edge 打开扩展管理页（Chrome 为 `chrome://extensions`，Edge 为 `edge://extensions`），启用“开发者模式”，选择“加载已解压的扩展程序”，目录为 `apps/extension/dist`。Edge 使用 Chromium MV3 API，与 Chrome 共用同一构建产物；如浏览器提示重新加载，点击扩展卡片上的“重新加载”即可。插件无需内部账号登录，会自动读取 BOSS 右上角招聘人员姓名。打开扩展弹窗可绑定或解绑飞书个人身份；设置页可修改 API 地址，生产地址必须是 HTTPS。

构建扩展：在仓库根目录执行 `pnpm --filter @recruitment/extension build`，然后将生成的 `apps/extension/dist` 加载到 Chrome 或 Edge。定时历史补扫**每天 23:30（北京时间）**跑一轮，范围是**当前 23:30 周期（今天）内有动静的会话**（叠加"上一轮已补到哪"的同步锚点）；如果那一刻浏览器没开，下一次打开沟通页会自动补一轮，范围完全相同，**但要等页面连续空闲 60 秒（没有按键/点击/滚动、焦点不在输入框）才开始**，所以日常打字和读会话不会被抢。消息发送（含面试邀约）只登记事件、状态和面试安排，不截图；聊天长图只由补扫采集，另外某一行还没有可用截图时会补一张（焦点在输入框里时不截）。采集会等待 BOSS 气泡渲染完成。补扫期间只要把 BOSS 沟通页留在浏览器里即可，标签页可以在后台。

当前无域名验收阶段，先运行 `ssh -N -L 127.0.0.1:8000:127.0.0.1:18082 databoard-server`，设置页填写 `http://localhost:8000/api/v1`。域名正式上线后改为 `https://ai.wuxistar.com/api/v1`，无需重新打包含固定服务器地址的扩展包。

权限仅覆盖本地 API、Mock 站点和 BOSS 域名，不申请 `<all_urls>`。`unlimitedStorage` 只用于网络失败时临时保留最多 10 个待上传聊天快照，上传成功即清理。
