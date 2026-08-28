# 本地开发

```bash
cp .env.example .env
make install
make db-up
make migrate
make seed
make dev-api
make dev-web
make dev-mock-site
```

也可执行 `docker compose up --build`。本机没有 Docker 时，API 默认可用 SQLite 做开发与测试，但生产只支持 PostgreSQL。运行 `make test`、`make lint`、`make typecheck` 和 `make build` 做交付检查。

