# ── 运行时（前端使用本地预构建产物 backend/static，无需 Node 构建阶段）──
# 说明：vite build 内存需求约 500MB+，小内存服务器（1G/2G）在容器内构建会 OOM。
# 因此前端在开发机预构建（npm run build → backend/static），Docker 镜像只打包 Python 运行时。
FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1

# 非 root 运行：setpriv 来自 util-linux（slim 自带），入口脚本负责数据卷属主修复后降权
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app

# 依赖锁定：requirements.txt 只记录直接依赖，Docker 用 .lock 保证重建结果可复现
COPY backend/requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
