# ── 构建阶段：安装并深度清理 Python 依赖包（多阶段构建，剥离构建残留）──
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY backend/requirements.lock .
RUN pip install --no-cache-dir --no-compile --prefix=/install -r requirements.lock \
    && find /install -type d \( -name "__pycache__" -o -name "tests" -o -name "test" \) -exec rm -rf {} + 2>/dev/null || true \
    && find /install -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.pyi" -o -name "*.c" -o -name "*.cpp" -o -name "*.h" \) -delete \
    && rm -rf /install/share /root/.cache

# ── 运行阶段（最小化镜像层，零构建缓存，运行时零额外字节码）──
FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 非 root 运行：util-linux 的 setpriv 负责入口数据卷修复并降权；清理运行期无用组件与系统缓存
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app \
    && rm -rf /usr/local/lib/python3.12/ensurepip \
              /usr/local/lib/python3.12/idlelib \
              /usr/local/lib/python3.12/turtledemo \
              /usr/local/lib/python3.12/site-packages/pip* \
              /usr/local/lib/python3.12/__pycache__ \
              /var/lib/apt/lists/* /var/cache/apt/* /tmp/* /var/tmp/* /usr/share/doc /usr/share/man

# 从构建阶段原样复制已精简的 Python 运行时依赖
COPY --from=builder /install /usr/local

COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
