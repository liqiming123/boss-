# API

基址 `/api/v1`，OpenAPI 位于 `/docs`。所有受保护接口使用 `Authorization: Bearer <token>`。

核心流程：

1. `POST /dev/login` 获取开发令牌。
2. `POST /plugin/context/resolve` 解析账号、岗位、候选人来源并只读查询历史。
3. `POST /plugin/events` 记录主动动作；`idempotency_key` 必须唯一。
4. `POST /plugin/interviews` 记录约面。
5. `/conflicts/*` 处理知悉、排除、继续、申请转交和关闭。
6. `/admin/*` 提供后台资源、设置、通知重试与审计查询。

错误统一为 `{ "error": { "code", "message", "request_id", "details" } }`。服务端忽略客户端传入的公司、招聘人员和角色，以 Token 为准。

