# 插件安装

```bash
pnpm --filter @recruitment/extension build
```

Chrome 或 Microsoft Edge 打开扩展管理页（Chrome 为 `chrome://extensions`，Edge 为 `edge://extensions`），启用“开发者模式”，选择“加载已解压的扩展程序”，目录为 `apps/extension/dist`。Edge 使用 Chromium MV3 API，与 Chrome 共用同一构建产物；如浏览器提示重新加载，点击扩展卡片上的“重新加载”即可。插件无需内部账号登录，会自动读取 BOSS 右上角招聘人员姓名。打开扩展弹窗可绑定或解绑飞书个人身份；设置页可修改 API 地址，生产地址必须是 HTTPS。

### Edge 安装步骤（zip 包）

从管理后台下载 `recruitment-collab-extension.zip` 后：

1. 解压 zip 到一个**固定目录**（不要放在“下载”文件夹后随手删掉；扩展每次启动都从该目录读取文件）。
2. 打开 `edge://extensions`，左侧开启“开发人员模式”。
3. 点击“加载解压缩的扩展”，选择解压出来的文件夹（里面直接是 `manifest.json`）。
4. 安装后**刷新一次 BOSS 沟通页（F5）**，再点插件弹窗绑定。

Edge 特有注意项：

- 每次 Edge 启动可能弹出“关闭开发人员模式扩展”提示，点提示右上角的 × 忽略即可，扩展不会被停用。
- 如需在 InPrivate（无痕）窗口使用 BOSS，先在扩展详情页开启“允许在 InPrivate 中访问”。
- Edge 默认的“睡眠标签页”比 Chrome 更早冻结后台页面，会暂停沟通页的实时监控（回到标签页会自动恢复）。建议在 `edge://settings/system` 关闭“使用睡眠标签页节省资源”，或在同一页的“从不使这些网站进入睡眠状态”中加入 `zhipin.com`。

构建扩展：在仓库根目录执行 `pnpm --filter @recruitment/extension build`，然后将生成的 `apps/extension/dist` 加载到 Chrome 或 Edge。定时历史补扫**每天 23:30（北京时间）**跑一轮，范围是**昨天和今天有动静的会话**（锚点按日历日记：每趟都覆盖"刚结束的那一天 + 正在进行的今天"，所以 23:30 浏览器没开也不会漏掉当天）；如果那一刻浏览器没开，下一次打开沟通页会自动补一轮，范围完全相同，**但要等页面连续空闲 60 秒（没有按键/点击/滚动、焦点不在输入框）才开始**，所以日常打字和读会话不会被抢。消息发送（含面试邀约）只登记事件、状态和面试安排，不截图；聊天长图只由补扫采集，另外某一行还没有可用截图时会补一张（焦点在输入框里时不截）。采集会等待 BOSS 气泡渲染完成。补扫期间只要把 BOSS 沟通页留在浏览器里即可，标签页可以在后台。

当前无域名验收阶段，先运行 `ssh -N -L 127.0.0.1:8000:127.0.0.1:18082 databoard-server`，设置页填写 `http://localhost:8000/api/v1`。域名正式上线后改为 `https://ai.wuxistar.com/api/v1`，无需重新打包含固定服务器地址的扩展包。

权限覆盖 BOSS 域名及 API 直连（`<all_urls>` host 权限，供扩展后台跨域请求 API），不需要访问其他网站的页面内容。`unlimitedStorage` 只用于网络失败时临时保留最多 10 个待上传聊天快照，上传成功即清理。
