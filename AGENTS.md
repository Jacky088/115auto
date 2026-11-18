# 115AutoSave 工程开发与模块化规范

所有新增功能、重构与缺陷修复必须遵守以下规则，防止再次出现难以维护的单体大文件。

---

## 1. 单文件行数红线（防单体膨胀）

由 [scripts/check_file_sizes.py](scripts/check_file_sizes.py) 与 CI 自动校验：

- **默认硬上限**：`backend/**/*.py` 与 `web/src/**/*.{vue,js}` 任意新建或常规文件 **不得超过 800 行**（超过 **600 行** 时应主动规划拆分，不再继续叠加新业务块）。
- **历史基线只减不增（Ratchet）**：现有 801～1,450 行的历史文件（如 `Settings.vue`、`Files.vue`、`bot_commands.py`、`database.py`、`queue_worker.py` 等）已锁定各自上限，**禁止往其中堆砌新功能**；如需扩展能力，必须拆出独立子模块或子组件。

---

## 2. 后端模块化规范（Python / FastAPI）

1. **路由层（`backend/api/`）**
   - 独立大业务新建 `backend/api/<domain>.py` 并在 `backend/main.py` 注册；
   - 主管理接口按子域放入 `backend/api/manage/<sub_domain>.py`，在 `backend/api/manage/__init__.py` 聚合路由并保留门面重导出。
2. **115 客户端层（`backend/pan115/`）**
   - `client.py` 仅维护核心 SDK 单例、风控限速、登录态/容量缓存与基础网盘读写；
   - 认证与扫码逻辑放 `auth.py`，定时整理匹配引擎放 `auto_move.py`；新增重量级网盘能力单独建模块，并通过延迟 `_pan()` 读取 `client` 门面（避免循环依赖且确保单测 `patch` 生效）。
3. **后台任务层（`backend/worker/`）**
   - 媒体整理/刮削相关放 `backend/worker/media_worker/` 对应子模块（`common` / `scanner` / `reports` / `organize` / `scrape` / `scheduler`）；
   - 定时巡检/通知/离线/备份/自动整理相关放 `backend/worker/jobs/` 对应子模块（`notify` / `health` / `offline` / `digest_backup` / `auto_move`）；
   - 子包 `__init__.py` 只做**薄门面**：统一持有跨模块共享的运行态变量（如锁、队列、任务引用）并完整重导出既有符号，禁止把多个业务域混写进同一文件。
4. **共享状态与测试兼容**
   - 拆分子包时，跨模块共享的可变状态与外部/测试会 `patch` 的钩子必须经门面访问（如 `_mw().xxx` / `_jobs().xxx` / `_pan().xxx`），严禁在子模块中复制一份独立状态副本。

---

## 3. 前端组件化规范（Vue 3 / Element Plus）

1. **页面与子组件职责分离**
   - `web/src/views/*.vue` 只做**薄容器**（布局、顶栏状态、路由/Tab 切换与轻量协调）；
   - 独立的业务卡片、Tab 页签、复杂弹窗、独立表格统一拆分到 `web/src/components/<feature>/*.vue`（参考 `components/media/`、`components/dirs/`）。
2. **样式作用域与 Teleport 规则**
   - 同一页面多个子组件共享样式时，提取为 `web/src/components/<feature>/<feature>.css`，统一以页面根类（如 `.media-page`、`.dirs-page`）做作用域前缀；
   - **严禁**在纯 `.css` 文件中残留 Vue 专有语法（`:deep(...)`、`:global(...)`、`:slotted(...)`）；
   - 挂载到 `<body>` 的弹出层（如 `el-tooltip`、`el-popover`）不能加页面根类前缀，应使用独立 `popper-class` 精确匹配。
3. **交互、校验与文案一致性**
   - **禁止面向用户的开发英文术语**：按钮、标题、提示中不得出现 `Dry-Run`、内部变量名（如 `pace_factor`）等开发术语；保留常见行业缩写（如 `TMDB`、`NFO`、`SHA1`、`Cookie`、`MB/GB/TB`）。
   - **数字输入**：统一使用主题默认的左右 `-` / `+` 胶囊步进器（`el-input-number` **禁止**加 `controls-position="right"`），单位紧贴步进器右侧不换行，同时做前端整数/上下界校验与后端 Pydantic 严格校验。
   - **二次确认与危险操作视觉规范**：全站所有二次确认统一使用 `confirmAction`（[confirmDialog.js](web/src/composables/confirmDialog.js)）弹出居中 Aurora Glass 确认框（禁止再使用零散的 `el-popconfirm` 气泡）；所有删除、清空、覆盖、停止类确认按钮统一使用红色 `danger` 样式。

---

## 4. 改动后必跑验证清单

每次提交前必须执行并通过：

```powershell
# 1. 文件行数红线检查
.venv\Scripts\python.exe scripts/check_file_sizes.py

# 2. 后端静态检查与回归测试
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tests/test_worker_mock.py

# 3. 前端严检与生产构建（改动 web/ 时必跑，确保 backend/static 同步）
cd web
npm run lint
npm run build
```

- 拆分重构类改动必须坚持**原样抽取**（不顺手改写业务逻辑），并用 AST / 符号核对与冷启动导入测试验证零回归。
