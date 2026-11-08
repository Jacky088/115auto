#!/bin/sh
# 容器入口：以 root 启动时把数据卷属主修好，再用非 root 用户运行（Linux bind 挂载兼容）。
# 已通过 user:/user 指定非 root 运行时，直接透传。
set -e

# 统一 HOME 到数据卷内：p115client 在 import 时就会往 ~/.p115client.cache.d 写缓存目录
# （const.py 硬编码 ~ 展开）。降权到 app 用户时若继承 root 的 HOME=/root 会
# Permission denied 导致 115 Cookie 校验全挂；放进数据卷还能顺带持久化缓存。
export HOME="/app/data/home"
mkdir -p "$HOME" 2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
    chown -R app:app /app/data 2>/dev/null || true
    exec setpriv --reuid=app --regid=app --clear-groups \
        python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
fi

exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
