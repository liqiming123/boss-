FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /workspace
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/mock-recruitment-site apps/mock-recruitment-site
RUN pnpm install --no-frozen-lockfile && pnpm --filter @recruitment/mock-site build
FROM nginx:1.27-alpine
COPY --from=build /workspace/apps/mock-recruitment-site/dist /usr/share/nginx/html

