FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /workspace
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/web apps/web
COPY packages/api-client packages/api-client
RUN pnpm install --no-frozen-lockfile && pnpm --filter @recruitment/web build
FROM nginx:1.27-alpine
COPY --from=build /workspace/apps/web/dist /usr/share/nginx/html
COPY infra/nginx/default.conf /etc/nginx/conf.d/default.conf

