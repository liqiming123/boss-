# 架构

```text
Mock/BOSS 页面 -> MV3 Content + Shadow Panel -> Service Worker -> FastAPI
                                                           |-> PostgreSQL
管理后台 --------------------------------------------------|
                                                           |-> Outbox Worker -> Mock/Real Feishu
```

Domain 负责标准化、匹配、冲突键和状态机，不依赖框架。Application 编排上下文解析和跟进事务。Infrastructure 提供 SQLAlchemy、JWT、飞书和 Worker。API 只做协议、认证、RBAC 与错误映射。Web 和 Extension 复用 `packages/api-client`。

