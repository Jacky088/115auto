# 修复任务清单（执行结果）

对应审计报告：[spec.md](file:///c:/Users/littl/Desktop/115atuosave/spec.md)
原则：不改功能语义、不破坏前后端契约、修完重新 `npm run build` 并重打发行包。

## 第一批 · P1

- [x] **T1 队列状态机加守卫** — 领取/成功/失败/回退全部带 `AND status=...`；仅成功守卫命中才计额度；成功后清 `next_retry_at`。文件：[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py)
- [x] **T2 长任务心跳与看门狗阈值** — 新增每 60 秒刷新 `running_at` 的心跳任务；阈值改为 `running_timeout_min`（默认 60，范围 15–720）；修正误导性注释。文件：[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py)、[jobs.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/jobs.py)
- [x] **T3 海报鉴权改造** — 登录/改密下发 HttpOnly + SameSite=Strict、路径仅限 `/api/tmdb/poster` 的 Cookie；移除 URL 中的主 token；新增 `/api/auth/logout` 清理 Cookie。文件：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py)、[auth.py](file:///c:/Users/littl/Desktop/115atuosave/backend/auth.py)、[Subscriptions.vue](file:///c:/Users/littl/Desktop/115atuosave/web/src/views/Subscriptions.vue)、[Layout.vue](file:///c:/Users/littl/Desktop/115atuosave/web/src/components/Layout.vue)
- [x] **T4 可信代理与登录限速** — 新增 `TRUSTED_PROXIES`；仅可信直连来源解析 XFF 且取最右侧非可信地址；否则用 socket 地址。文件：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py)、[config.py](file:///c:/Users/littl/Desktop/115atuosave/backend/config.py)、[docker-compose.yml](file:///c:/Users/littl/Desktop/115atuosave/docker-compose.yml)

## 第二批 · P2

- [x] **T5 依赖锁定** — 新增 `backend/requirements.lock`（运行时依赖精确版本 + tzdata），Docker 改为安装锁定文件。文件：[requirements.lock](file:///c:/Users/littl/Desktop/115atuosave/backend/requirements.lock)、[Dockerfile](file:///c:/Users/littl/Desktop/115atuosave/Dockerfile)
- [x] **T6 请求体真实字节限制** — 新增 ASGI 中间件按实际读取字节截断，不依赖 Content-Length；超限稳定返回 413。文件：[main.py](file:///c:/Users/littl/Desktop/115atuosave/backend/main.py)
- [x] **T7 修改密码限速生效** — 旧密码错误与限速检查统一使用真实来源 IP 桶。文件：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py)
- [x] **T8 关闭公开 API 文档** — `docs_url/redoc_url/openapi_url` 全部置空。文件：[main.py](file:///c:/Users/littl/Desktop/115atuosave/backend/main.py)
- [x] **T9 CSP 与 token 存储加固** — 增加 `script-src 'self'`、`object-src 'none'`、`base-uri 'self'`、`frame-ancestors 'none'`；样式保留 unsafe-inline。文件：[main.py](file:///c:/Users/littl/Desktop/115atuosave/backend/main.py)
- [x] **T10 频道重名检查** — Bot `/add` 与网页添加都改为按频道名全局判重，避免同一频道双链路重复处理。文件：[bot_commands.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/bot_commands.py)、[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py)
- [x] **T11 通知移出事件循环** — 永久失败通知改用 `to_thread`；`notify_detail` 多渠道并发 + 20 秒总超时。文件：[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py)、[jobs.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/jobs.py)
- [x] **T12 对外 API 入队竞态兜底** — 新增 `db.insert()` 返回自增 id；捕获 `IntegrityError` 按 duplicate 返回，不再 500。文件：[ext.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/ext.py)、[database.py](file:///c:/Users/littl/Desktop/115atuosave/backend/database.py)
- [x] **T13 同名判重精确化** — 归一化后精确比对（去空白/大小写/扩展名），取消前缀匹配；目录读取上限提升到 5000。文件：[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py)

## 第三批 · P3

- [x] **T14 日志搜索 LIKE 转义** — 复用 `_like_escape` + `ESCAPE '\'`，避免 `%` 触发全表扫描。文件：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py)
- [x] **T15 规则/路由保存事务化** — 路由先全量校验，再在一个事务内 DELETE + executemany 写入。文件：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py)
- [x] **T16 transfers 保留策略** — 每日清理保留 duplicate/skipped 7 天，success/failed 台账永久保留。文件：[jobs.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/jobs.py)
- [x] **T17 TMDB 分类首页短缓存** — discover 结果 5 分钟缓存（最多 100 键），降低 N+1 与配额消耗。文件：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py)
- [x] **T18 外网用例稳定跳过** — t.me 返回不足 15 条（错误页）即按网络不可用跳过，离线可稳定全绿。文件：[test_worker_mock.py](file:///c:/Users/littl/Desktop/115atuosave/tests/test_worker_mock.py)
- [x] **T19 部署默认绑定与资源限制** — 新增日志轮转（10m×3）与可选 `mem_limit` 示例。⚠️ 勘误（2026-11-13）：「默认仅监听 127.0.0.1」与实际不符——docker-compose 默认 `${PAN_BIND_IP:-0.0.0.0}`（全网卡），公网部署请在 `.env` 设 `PAN_BIND_IP=127.0.0.1` 并加反向代理。文件：[docker-compose.yml](file:///c:/Users/littl/Desktop/115atuosave/docker-compose.yml)
- [x] **T20 备份恢复加固** — 保持整体事务 + 列白名单；备份中的凭据类键一律跳过，禁止被备份值覆盖。文件：[database.py](file:///c:/Users/littl/Desktop/115atuosave/backend/database.py)

## 验证结果

- 后端字节编译通过：`python -m compileall backend tests`
- 回归测试：[test_worker_mock.py](file:///c:/Users/littl/Desktop/115atuosave/tests/test_worker_mock.py) — **244 通过 / 0 失败 / 4 跳过（外网不可达）**
- 专项验证：`/docs`、`/openapi.json` 返回 404；无 Cookie 海报 401、有 Cookie 正常鉴权；chunked 2MB 请求返回 413；伪造 XFF 不生效、可信代理解析正确；同一任务二次领取 `changed=0`；相似片名不误判、精确同名命中
- 前端构建：`npm run build` 成功，产物已更新到 `backend/static`
- 发行包：已重新打包 `115atuosave.zip`

## 后续维护

1. 升级依赖后重新在干净环境 `pip freeze` 更新 `requirements.lock`，并跑回归。
2. 反向代理部署时设置 `TRUSTED_PROXIES`（逗号分隔 IP/CIDR），否则登录限速按直连 IP 计算。
3. 默认仅本机监听：远程访问请用反向代理或 SSH 隧道；局域网直连需在 `.env` 写 `PAN_BIND_IP=0.0.0.0`。
