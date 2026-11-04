# 应用镜像：构建前端静态资源 + 运行 seekdb-js 后端
# 说明：模型文件与 ORT 运行时通过 scripts/prepare-assets.mjs、fetch-model.mjs 准备在 web/public 下，
# 镜像内直接打包，运行时完全离线（不访问任何模型 CDN）。
FROM node:22-bookworm AS build

WORKDIR /app

# 先装依赖，利用层缓存
COPY package.json package-lock.json* ./
COPY server/package.json server/package.json
COPY web/package.json web/package.json
RUN npm --prefix server install --legacy-peer-deps --omit=dev --no-audit --no-fund \
 && npm --prefix web install --legacy-peer-deps --no-audit --no-fund

COPY . .
RUN npm --prefix web run build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/server ./server
COPY --from=build /app/server/node_modules ./server/node_modules
COPY --from=build /app/web/dist ./web/dist
COPY --from=build /app/web/public ./web/public

EXPOSE 8787
CMD ["node", "server/src/index.mjs"]
