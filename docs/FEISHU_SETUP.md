# 飞书配置

1. 在飞书开放平台创建企业自建应用，配置 OAuth 重定向地址和事件订阅回调。
2. 按最小权限申请用户身份读取、单聊消息与互动卡片权限。
3. 设置 `FEISHU_MODE=real`、App ID、App Secret、Encrypt Key、Verification Token 和 Redirect URI。
4. OAuth 重定向地址必须精确配置为 `FEISHU_REDIRECT_URI`，本地默认 `/api/v1/auth/feishu/callback`；事件回调为 `/api/v1/integrations/feishu/events` 与 `/card-callback`。
5. 用户在扩展弹窗发起绑定或解绑；服务端使用一次性、十分钟有效的 `state` 防止伪造和重放，交换用户身份后不保存个人访问令牌。
6. 系统校验 Verification Token、请求时间戳、签名，并解密加密信封；Outbox Worker 获取并缓存 tenant token。
7. 先在测试企业完成一次绑定、私聊卡片和解绑，验证幂等、失败重试和日志脱敏，再切换生产。

管理后台也使用同一飞书 OAuth：用户点击“使用飞书登录”后，回调仅允许已绑定且角色为 `ADMIN` 或 `HR_MANAGER` 的飞书身份通过。服务器写入 `HttpOnly` 会话 Cookie，前端不保存 access token、refresh token、邮箱或密码。

同一个飞书用户改绑另一个 BOSS 招聘账号时，系统会事务性地解除旧账号绑定、撤销旧账号全部扩展设备令牌、取消旧账号待发送通知，再将发起 OAuth 的当前设备迁移到新账号。旧账号的候选人历史、岗位记录和飞书表行不会删除。目标 BOSS 账号如果已绑定另一个飞书用户，则拒绝覆盖，必须先由原用户解绑。

App Secret 和 tenant token 只存在服务端环境变量/内存中，禁止写入扩展。

## OAuth 错误码 20029

出现“重定向 URL 有误”（错误码 `20029`）表示授权请求中的 `redirect_uri` 没有被飞书应用安全设置精确放行，并不表示 API、SSH 隧道或 App Secret 失效。

本地真实验收阶段，在飞书开放平台进入对应自建应用的“安全设置”，将以下地址加入“重定向 URL”并保存：

```text
http://localhost:8000/api/v1/auth/feishu/callback
```

协议、主机名、端口、路径及末尾斜杠都必须完全一致；`localhost` 和 `127.0.0.1` 不视为同一个地址。保存后不要继续使用已经过期的授权链接，应回到扩展重新点击“使用飞书登录”。同时确保本机 SSH 隧道仍在运行，否则飞书虽能通过白名单检查，浏览器回调时仍无法连接 API。

正式域名上线时，将服务器环境变量与飞书开放平台同时切换为：

```text
https://ai.wuxistar.com/api/v1/auth/feishu/callback
```

确认 HTTPS 登录成功后再移除本地回调地址，避免切换过程中所有扩展同时无法登录。
