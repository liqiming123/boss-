# 部署

复制 `.env.example`，替换强随机 `SECRET_KEY`、数据库密码、严格 CORS 域名和飞书配置。生产使用：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api alembic -c apps/api/alembic.ini upgrade head
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

在外部负载均衡或 Nginx 配置正式域名、TLS、HSTS 和证书自动续期。API/Worker 可水平扩容；Worker 通过 PostgreSQL `SKIP LOCKED` 竞争任务。上线前执行备份与恢复演练。

