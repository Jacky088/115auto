# ── 运行时（前端使用本地预构建产物 backend/static，无需 Node 构建阶段）──
# 说明：vite build 内存需求约 500MB+，小内存服务器（1G/2G）在容器内构建会 OOM。
# 因此前端在开发机预构建（npm run build → backend/static），Docker 镜像只打包 Python 运行时。
FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
