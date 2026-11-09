# 115 自动转存助手 · 全面审计报告

- 审计日期：2026-11-09
- 审计范围：后端 FastAPI（api / core / worker / tg / pan115 / database / auth / config / main）、部署（Dockerfile / docker-compose / entrypoint / requirements）、前端接口契约（router / api / 关键视图）、测试基线
- 审计方式：静态代码审阅 + 既有测试结果（`tests/test_worker_mock.py`：246 通过 / 2 失败的离线外网用例，属已知基线）
- 本轮结论：**只读审计，未修改任何代码**

## 一、总体结论

系统整体可运行、模块职责清晰，既有安全设计（PBKDF2 密码、JWT 轮换、凭据文件 600、备份排除密码哈希、WAL、请求体上限）已具备。主要风险集中在**队列状态机竞态**、**看门狗误杀长任务**、**令牌出现在 URL**、**登录限速可被伪造头绕过**。

| 等级 | 数量 | 说明 |
| --- | --- | --- |
| P0 | 0 | 无阻断性、可导致数据不可恢复的问题 |
| P1 | 4 | 可导致重复转存/额度双计、凭据泄露、风控失效 |
| P2 | 9 | 可复现性、安全加固、性能与数据一致性 |
| P3 | 7 | 可维护性、可观测性、长期膨胀 |

> 上一轮已完成的订阅追剧修复（删除按钮、别名、多订阅、结构化集数、TMDB 分类排序等）不在本次缺陷清单内重复记录。

## 二、P1 · 高优先级

### P1-1 队列领取与成功回写缺少状态守卫（可重复转存 + 额度双计）

- 现象：领取任务时执行 `UPDATE ... SET status='running' ... WHERE id=?`，没有 `AND status='queued'`；成功回写 `WHERE id=?`，也没有 `AND status='running'`。
- 证据：[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py#L589-L596)、[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py#L346-L349)
- 影响：多进程/多副本部署、或看门狗重排后原线程稍后返回，都会让同一任务被两个执行体同时接管；成功回写不看当前状态，会把已重排任务再次标记 `success` 并二次 `quota_incr()`，造成**重复转存与额度多扣**。
- 修复建议：
  1. 领取改为 `WHERE id=? AND status='queued'`，`changed==0` 即放弃；
  2. 成功/失败回写统一加 `AND status='running'`，`changed==0` 视为已作废，不写额度；
  3. `quota_incr()` 仅在成功回写 `changed==1` 时执行；
  4. 同时清理 `next_retry_at`，避免已成功任务被重试扫描捡回。
- 验收：模拟两个 worker 同时领取同一 id，只有一个能进入 `running`；延迟返回的旧线程无法改写最终状态；额度计数与成功记录一一对应。

### P1-2 看门狗 30 分钟阈值 + 长任务无心跳（正常大任务被误重排）

- 现象：健康检查每 2 分钟把 `running_at < now-1800` 的任务直接重排；而 `_process_one` 在 `asyncio.to_thread` 中运行，无法被取消，大分享（数千文件、反查/找回/校验）很容易超过 30 分钟。
- 证据：[jobs.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/jobs.py#L334-L341)、[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py#L598-L612)
- 影响：与 P1-1 联动会产生**双执行体、重复文件、双额度、双通知**；注释中“会被后续完成任务的状态守卫自然作废”与实现不符，属于误导性注释。
- 修复建议：在转存关键阶段（快照、拉取、转存、校验、整理）周期性刷新 `running_at` 心跳；把阈值做成可配置（默认 30→60 分钟）；重排前记录 `watchdog_requeued_at`，旧线程回写被守卫拦下。
- 验收：构造耗时 >30 分钟的 mock 任务，确认不会被重排；真正卡死的任务仍能在阈值后回收。

### P1-3 TMDB 海报接口把 JWT 放进 URL 查询参数

- 现象：`/api/tmdb/poster` 同时接受 `Authorization` 和 `?token=`，前端 `<img>` 使用 query 方式。
- 证据：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py#L834-L850)、[ws.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/ws.py)（WS 侧刻意用 Sec-WebSocket-Protocol 避免 URL 带 token，说明团队已有此约定）
- 影响：长期 JWT 会进入 Nginx/FastAPI 访问日志、浏览器历史、Referer、代理缓存；一旦日志被读取即可接管后台。
- 修复建议：签发**独立 scope、60 秒有效的一次性海报签名**（`scope=poster&path=...`），或在登录时下发 HttpOnly + SameSite=Strict 的会话 Cookie，由图片请求自动携带；移除 query 中的主 token；响应加 `Cache-Control: private, max-age=...`（已有私有缓存逻辑可复用）。
- 验收：图片正常加载；访问日志不含主 token；主 token 过期/密钥轮换后旧海报 URL 自动失效。

### P1-4 登录限速信任客户端可伪造的 X-Forwarded-For

- 现象：`_client_ip()` 无条件取 `X-Forwarded-For` 首段。
- 证据：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py#L17-L22)、[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py#L41-L49)
- 影响：攻击者每次请求换一个伪造 XFF 即可**完全绕过登录失败限速**，也可污染日志来源；若前置代理未覆盖该头，风险直接可利用。
- 修复建议：新增“可信代理”配置（环境变量/设置项）；仅当直连来源属于可信代理时才解析 XFF，并取最右侧可信段；否则一律用 `request.client.host`。uvicorn 启动参数保持默认，不启用 `--proxy-headers` 的宽松模式。
- 验收：伪造 XFF 连续失败 5 次仍触发 429；真实反代场景可正确取到客户端 IP。

## 三、P2 · 中优先级

### P2-1 依赖未锁定，重建不可复现

- 证据：[requirements.txt](file:///c:/Users/littl/Desktop/115atuosave/backend/requirements.txt#L1-L10)
- 影响：全部为 `>=` 下限，`p115client` 仍处 0.0.x、Telethon/FastAPI 跨大版本升级可能改变行为；Docker 重建结果与已验证版本不一致，故障难回溯。
- 建议：生成 `requirements.lock`（`pip freeze` 或 `uv lock`），镜像安装锁定版本；CI 定期跑兼容性升级。

### P2-2 请求体上限仅依赖 Content-Length，可被 chunked 绕过

- 证据：[main.py](file:///c:/Users/littl/Desktop/115atuosave/backend/main.py#L86-L93)
- 影响：未认证的 `/api/auth/login` 可用无 `Content-Length` 的分块请求持续发送，FastAPI 仍会整体读入内存，小内存 NAS 可被 OOM。
- 建议：在中间件/ASGI 层按实际读取字节计数截断；或使用反向代理 `client_max_body_size`；恢复备份端点单独限流与大小校验。

### P2-3 修改密码的限速检查用了空 IP 桶，实际不生效

- 证据：[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py#L66-L73)、[auth.py](file:///c:/Users/littl/Desktop/115atuosave/backend/auth.py#L31)
- 影响：已登录会话可无限次尝试旧密码；`record_login_fail` 记在真实 IP 桶，而检查读的是 `""` 桶，二者不是同一个计数器。
- 建议：`login_throttled(_client_ip(request))`，并把“旧密码错误”计入同一 IP 桶。

### P2-4 `/docs` 与 `/openapi.json` 无鉴权公开

- 证据：[main.py](file:///c:/Users/littl/Desktop/115atuosave/backend/main.py#L81)
- 影响：公网暴露时接口结构、参数、内部错误模型全部可枚举（含对外 API 形态），增加攻击面。
- 建议：`FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`，或仅登录后可访问（开发环境开关）。

### P2-5 前端 JWT 存 localStorage，且未设 CSP

- 证据：[api.js](file:///c:/Users/littl/Desktop/115atuosave/web/src/api.js#L7)、[Login.vue](file:///c:/Users/littl/Desktop/115atuosave/web/src/views/Login.vue#L38)、[main.py](file:///c:/Users/littl/Desktop/115atuosave/backend/main.py#L84-L99)（明确不加 CSP，因 Element Plus 内联样式）
- 影响：任何 XSS（第三方脚本、富文本、依赖投毒）可直接读取长期 token；无 CSP 时缺少最后一道缓解。
- 建议：短期加严格 `script-src 'self'`、`object-src 'none'`、`base-uri 'self'`（Element Plus 的内联**样式**不受 `style-src` 影响，可用 `style-src 'self' 'unsafe-inline'`）；中期改为 HttpOnly Cookie + CSRF Token。

### P2-6 Bot `/add` 重名只检查 polling 类型

- 证据：[bot_commands.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/bot_commands.py#L381-L385)
- 影响：同一频道可同时存在 userbot 与 polling 两条记录，消息被两条链路重复处理，可能产生重复任务与重复通知。
- 建议：重名检查去掉 `type` 条件；如确需双链路，明确去重策略（`messages` 主键已按频道+消息去重，但两条记录的频道名字段可能不同）。

### P2-7 失败通知在事件循环内同步执行

- 证据：[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py#L613-L623)
- 影响：永久失败分支直接调用同步 `jobs.notify_failure`，4 个渠道 × 超时（最长 15s/渠道）会阻塞整个队列循环，期间重试扫描、调度窗口判断全部延迟。
- 建议：统一 `await asyncio.to_thread(jobs.notify_failure, ...)`；通知内部改为并发发送并设总超时。

### P2-8 对外 API 入队缺少并发冲突兜底

- 证据：[ext.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/ext.py#L121-L132)、[pipeline.py](file:///c:/Users/littl/Desktop/115atuosave/backend/core/pipeline.py#L144-L180)（pipeline 有 `IntegrityError` 转判重，ext 没有）
- 影响：两个并发请求同时通过“查重”后插入，会触发部分唯一索引冲突，直接 500；调用方重试又会造成语义混淆。
- 建议：捕获 `sqlite3.IntegrityError` 并按 duplicate 返回；插入后按 `last_insert_rowid()` 取 id，而不是再查 `status='queued'`（当前查重可能取到别人的任务）。

### P2-9 同名判重使用前缀匹配且只取前 500 个文件

- 证据：[queue_worker.py](file:///c:/Users/littl/Desktop/115atuosave/backend/worker/queue_worker.py#L103-L118)
- 影响：`startswith(title)` 会把“进击的巨人 最终季”误判为已存在“进击的巨人”；目录超过 500 个文件时漏判导致重复转存。
- 建议：比较归一化后的文件名（去扩展名、去季集标记）做精确匹配；分页拉全量或至少与 `share_snap` 上限一致。

## 四、P3 · 低优先级

1. **日志搜索未转义 LIKE 通配符**：`message LIKE '%{search}%'`，用户输入 `%` 会全表扫描。[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py#L1480-L1482)。建议复用 `_like_escape` + `ESCAPE '\'`。
2. **规则/路由保存非事务**：逐条 `db.execute`，中途异常会部分生效。[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py#L892)。建议包 `db.transaction()`。
3. **transfers 无保留/归档策略**：重复链接每次命中都会写一条 `duplicate`，长期表膨胀且仪表盘/列表变慢。[pipeline.py](file:///c:/Users/littl/Desktop/115atuosave/backend/core/pipeline.py#L144-L180)。建议按月归档或仅保留每链接最近 N 条。
4. **TMDB discover 无列表缓存**：每次分类浏览最多 40 次详情请求（仅季信息有 24h 缓存），弱网/限流时首屏慢。[manage.py](file:///c:/Users/littl/Desktop/115atuosave/backend/api/manage.py#L679-L831)。建议对“分类第一页”做 5–10 分钟短缓存。
5. **测试依赖外网**：2 个用例访问 `t.me/s`，离线必失败，难以作为 CI 阻断项。[tests/test_worker_mock.py](file:///c:/Users/littl/Desktop/115atuosave/tests/test_worker_mock.py)。建议标记 `@pytest.mark.network` 或 mock。
6. **默认对外监听 0.0.0.0**：compose 默认全部网卡，且无资源限制/日志轮转。[docker-compose.yml](file:///c:/Users/littl/Desktop/115atuosave/docker-compose.yml#L7-L11)。建议默认 127.0.0.1，文档强调反代，并加 `logging` 轮转与内存上限。
7. **备份恢复直接操作连接**：`restore_payload` 在持锁期间直接用 `_conn`，未走统一封装；异常时可能留下半恢复状态。[database.py](file:///c:/Users/littl/Desktop/115atuosave/backend/database.py#L266)。建议整体包事务并校验备份版本/字段白名单。

## 五、已确认的良好实践（不列为缺陷）

- 密码 PBKDF2 校验、失败限速、JWT 密钥轮换、初始随机密码文件改密即删。
- 启动收紧 cookie / secret / session 文件权限为 600。
- 备份排除密码哈希与通知密钥，恢复后强制 `running=False`。
- SQLite WAL + 统一锁 + 事务封装；日志 7 天、消息 30 天清理。
- 请求体上限与安全响应头（nosniff / DENY / Referrer-Policy）。
- 前端 hash 路由，静态托管无深链 404 风险；WebSocket token 走子协议。

## 六、建议修复顺序

1. P1-1 + P1-2（同一状态机改造，必须一次完成并补测试）
2. P1-3 + P1-4 + P2-3（鉴权与限速）
3. P2-1 + P2-2（构建可复现与 OOM 防护）
4. P2-7 + P2-8 + P2-9（队列稳定性与准确性）
5. P2-4/5/6 与 P3 批量清理

## 七、约束

- 不改变现有功能行为与前后端接口契约；修复后需重新构建前端并重打发行包。
- 敏感配置继续使用密码框；每日摘要按 Asia/Shanghai；警示文字全局加粗。
- 修复完成需回归 `tests/test_worker_mock.py`，并以“除 2 个已知外网用例外全部通过”为验收基线。
