> ⚠️ **初期版本（v1.0.1），功能虽已较完整但可能存在各种各样的问题，欢迎提 Issue 反馈！使用前请仔细阅读下方风险提示。**

# 115 自动转存助手

监控 Telegram 频道中的 115 分享链接和电驴/磁力下载链接，自动分类转存到 115 网盘对应目录。网页操作全部功能，Docker 一键部署，支持手机访问。

## 功能

- **双通道转存**：115 分享链接转存 + 云下载（支持电驴/磁力链接），可分别开关、分别指定目录
- **自动分类**：内置电影/电视剧/动漫/综艺/纪录片/少儿 6 类规则，开箱即用；正则规则可视化编辑 + 实时测试器；分类占比饼图一目了然
- **黑名单过滤**：关键词 / 频道 / 文件名正则三层拦截（预告、花絮、枪版、广告等 22 条预置），命中记录可复核、可放行
- **TG 监听双模式**：公开频道网页轮询（免 TG 账号、零风控）+ Userbot 实时监听（支持私有频道）；**停机漏消息自动按 ID 回溯补齐**（按每日限额节奏慢慢补）
- **115 扫码登录**：网页展示二维码，手机 App 扫码即登录；Cookie 失效自动巡检告警
- **可靠性**：链接合法性校验、双重确认（转存后反查目录/云下载跟踪 115 真实结果）、失败退避重试、每日限额、随机间隔防风控
- **TG 通知**：转存成功/失败、云下载添加/完成/失败，均含具体原因；支持 Bot 远程指令（/status /pause /resume /retry）
- **定时计划**：可设置只在指定时段（如夜间）转存
- **仪表盘**：统计卡片、115 空间用量（蓝/橙/红预警）、实时日志、分类占比
- **数据管理**：历史日志保留 7 天按天查询/导出、数据备份导出 JSON
- **移动端适配**：手机浏览器完整可用

## 快速部署

```bash
# 1. 上传项目到服务器并解压
unzip 115atuosave.zip -d 115atuosave && cd 115atuosave

# 2. 编辑 docker-compose.yml，修改 WEB_PASSWORD 为强密码

# 3. 构建并启动（前端已预构建进包内，构建镜像只需 Python 层，1G 内存服务器可用）
docker compose up -d --build
```

> 若自行修改前端代码（web/ 目录），需在开发机先 `cd web && npm install && npm run build` 重新生成 backend/static，再构建镜像。

浏览器打开 `http://服务器IP:8000`，账号 `admin`，密码为你设置的 `WEB_PASSWORD`。

公网访问建议加一层 Caddy 自动 HTTPS：

```
pan.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

## 首次使用

登录后进入初始化向导，按提示完成 4 步：

1. **115 扫码登录** — 手机 115 App 扫网页上的二维码
2. **选择 TG 接入方式** — 推荐「网页轮询」（频道是公开的即可，不需要 TG 账号）；要监控私有频道再选 Userbot（建议小号）
3. **添加频道** — 粘贴频道链接（如 `https://t.me/s/regeng115`），每行一个
4. 完成 → 系统开始自动监控转存

## 日常使用

| 页面 | 用途 |
|---|---|
| 仪表盘 | 今日统计、115 空间、分类占比、实时日志 |
| 频道管理 | 添加/启停频道，预览每条消息的处理结果 |
| 分类规则 | 分类→目录映射（分享/云下载可分目录）、正则规则、测试器 |
| 黑名单 | 拦截规则管理、命中记录复核放行 |
| 转存记录 | 全部任务状态、失败重试、批量重试 |
| 日志查询 | 历史日志按天查阅/导出（保留 7 天） |
| 账号管理 | 115 扫码登录、Userbot 登录 |
| 系统设置 | 功能开关、转存限额、定时计划、TG 通知与远程指令 |

**TG 通知**：在「系统设置 → 通知」填入 Bot Token 和 Chat ID，点「发送测试消息」验证。配好后转存结果会实时推送，还可直接给 Bot 发 `/status` 查看状态、`/pause` 暂停。

## 数据与维护

- 所有数据（数据库、Cookie、TG session、日志）都在 `data/` 目录
- **备份**：网页「系统设置 → 导出数据备份」下载 JSON，或直接复制 `data/` 目录
- **升级**：替换项目文件后 `docker compose up -d --build`，数据无损

## 风险提示

- 115 转存使用 Cookie + 官方自用接口（非开放平台 API），程序已内置随机间隔/每日限额/失败退避缓解风控，建议先用不重要的账号试跑几天
- Userbot 模式使用真实 TG 账号，有封号风险，务必用小号
- 仅供个人学习研究使用

## 技术栈

FastAPI + SQLite + Telethon + [p115client](https://github.com/ChenyangGao/web-mount-packs) ｜ Vue 3 + Element Plus + ECharts ｜ Docker

## 本地开发

```bash
# 后端
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt   # Windows
.venv/bin/pip install -r backend/requirements.txt       # Linux/Mac
.venv/Scripts/python -m uvicorn backend.main:app --port 8000 --reload

# 前端（另开终端，/api 自动代理到 8000）
cd web && npm install && npm run dev    # http://localhost:5173

# 测试
.venv/Scripts/python tests/test_worker_mock.py
```
