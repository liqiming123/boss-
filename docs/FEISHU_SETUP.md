# 飞书配置

1. 在飞书开放平台创建企业自建应用，配置 OAuth 重定向地址和事件订阅回调。
2. 按最小权限申请用户身份读取、单聊消息与互动卡片权限。
3. 设置 `FEISHU_MODE=real`、App ID、App Secret、Encrypt Key、Verification Token 和 Redirect URI。
4. 回调端点为 `/api/v1/integrations/feishu/events` 与 `/card-callback`。
5. 系统校验 Verification Token、请求时间戳、签名，并解密加密信封；Outbox Worker 获取并缓存 tenant token。
6. 先在测试企业发送一条卡片，验证按钮回调、幂等、失败重试和日志脱敏，再切换生产。

App Secret 和 tenant token 只存在服务端环境变量/内存中，禁止写入扩展。

