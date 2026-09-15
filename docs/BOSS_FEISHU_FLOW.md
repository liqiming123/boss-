# BOSS 沟通协同闭环

候选人协同链路只保留沟通相关的 BOSS 能力；职位列表采集是独立的可选工具，不会进入查重或飞书候选人表。

## 使用流程

1. 在 BOSS 沟通页打开招聘协同助手扩展，确认右上角招聘人账号已显示。
2. 从扩展弹窗发起“绑定飞书”。扩展通过当前 BOSS 页的 `GET_PAGE_ACCOUNT` 读取脱敏的账号显示名，再把该账号与飞书身份、浏览器设备绑定；不会读取或保存 BOSS 密码、Cookie 或完整页面 HTML。
3. 打开候选人沟通页时，扩展只提取候选人身份字段、岗位、沟通时间和 BOSS 原生同事沟通记录，调用 `/plugin/context/check` 做只读查重。
4. 如果其他招聘人已经沟通过同一候选人，页面只显示重复提醒和证据，不自动合并候选人，也不会因为“仅查看”创建业务记录。
5. 当 BOSS 确认消息发送成功后，扩展调用 `/plugin/engagements/message-sent` 创建/更新当前招聘人的沟通记录，并通过候选人同步 Outbox 异步写入飞书多维表；通知通过 Notification Outbox 异步发送，避免阻塞 BOSS 页面。
6. 定时历史补扫逐个打开“沟通中”的已读会话，同步候选人后截取当前沟通区域的分块 JPEG；未读行一律不点击。消息发送（包括面试邀约）只登记事件、状态和面试安排并刷新飞书行，**任何一次发送都不截图**；聊天长图只由补扫采集，另外某一行还没有可用截图时会补一张。截图计算哈希并上传到受限飞书多维表附件列。用户明确点击 BOSS 简历预览后，才下载简历或上传预览截图到同一附件列。
7. 上传失败会进入扩展队列或后端重试队列；数据库只保留最新附件令牌、哈希、文件名和状态。数据保留 Worker 会清理已同步记录的临时业务载荷。

## 与参考项目的边界

参考的 `boss-zhipin-scraper` 使用 Chrome CDP 被动捕获职位搜索 API，适合职位市场分析。仓库现在提供了一个精简、隔离的对应工具 [`scripts/boss_cdp_capture.py`](../scripts/boss_cdp_capture.py)：它只监听真实页面发出的职位列表响应，优先读取 `salaryDesc`，输出 JSON 和 CSV；不会把职位数据写入候选人协同表，也不主动注入 XHR。城市码表、详情 JD、薪资分析和大规模调度仍不在本项目范围内。

启动专用 Chrome（macOS 示例）：

```bash
mkdir -p "$PWD/.boss-cdp-profile"
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$PWD/.boss-cdp-profile"
pip install -r scripts/requirements-boss-cdp.txt
make boss-cdp KEYWORD='AI Agent' CITY='101020100'
```

首次启动后在这个专用窗口手动登录 BOSS；不要把主 Chrome 的 profile、Cookie 或密码目录传给脚本。若端口已被占用，可将 `CDP_PORT=9223` 传给 `make`。

## 必须保持的安全约束

- 生产扩展只能通过 API 访问后端，不能连接 PostgreSQL 或飞书。
- 账号绑定、查重和附件上传都由后端校验当前设备/招聘人身份；页面显示名不是永久合并依据。
- 截图范围限定为当前聊天区域；简历只有在用户明确打开预览后才上传。
- 任何姓名/岗位相似只能生成证据和提醒，不能自动永久合并。
