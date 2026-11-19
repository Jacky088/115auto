"""TG Bot 共享运行态、常量与指令菜单配置。"""
from ... import database as db

_last_update_id = 0

# /sub 搜索结果缓存：chat_id -> (缓存时间, 候选列表)（subpick 按钮回调时取回对应条目）
_SUB_SEARCH_CACHE: dict[str, tuple[float, list]] = {}
_SUB_SEARCH_TTL = 1800  # 30 分钟：旧消息里的按钮过期后不再订阅到别的片
_SUB_NUMS = ["①", "②", "③", "④", "⑤", "⑥"]

# 备份文件恢复待确认区：chat_id -> (payload, 缓存时间)。
# 转发备份 JSON 给 Bot 后先暂存，回复 /restore confirm 才真正写库（防误触）。
_RESTORE_PENDING: dict[str, tuple[dict, float]] = {}
_RESTORE_TTL = 600  # 10 分钟内有效


def _tg_proxy() -> str | None:
    return str(db.get_setting("proxy", "") or "") or None


USAGE_ADD = (
    "用法：/add <频道名或链接> on|off\n"
    "· on  = 开启「仅订阅」（仅转存命中订阅关键词的内容）\n"
    "· off = 关闭（全盘转存）\n"
    "例如：/add @somechannel on"
)

USAGE_SUB = (
    "用法：\n"
    "· /sub — 查看订阅\n"
    "· /sub <片名> — 搜索 TMDB 并点选订阅\n"
    "· /sub del <关键词> — 删除订阅"
)

HELP = (
    "可用指令：\n"
    "/status — 查看运行状态（队列/限额/115 空间）\n"
    "/ls — 最近 5 条转存记录\n"
    "/menu — 快捷操作菜单（按钮面板）\n"
    "/add <频道名或链接> on|off — 添加监控频道（on=仅订阅，off=全盘转存；on/off 必填）\n"
    "/sub — 查看订阅；/sub <片名> 搜索 TMDB 点选订阅；/sub del <关键词> 删除\n"
    "/archive — 归档状态；/archive run 立即排队归档（按钮确认）\n"
    "/start — 启动系统（自动就绪校验）\n"
    "/stop — 停止系统（两步确认，归档联动关闭）\n"
    "/retry — 重试全部失败任务\n"
    "/backfill [剧名] — 向前补全早期分集（如从 E03 开始追，补全 E01/E02）\n"
    "/backup — 导出并发送当前完整数据备份文件（.json 或 .json.gz）\n"
    "/restore — 发送备份文件后回复 /restore confirm 恢复数据\n"
    "/help — 显示本帮助\n"
    "直发链接需带指令头（空格不影响，链接可紧贴）：\n"
    "转存 [分类] <115 分享链接> — 进入分享转存队列\n"
    "云下载 [分类] <磁力/电驴链接> — 进入云下载队列\n"
    "分类可省略，省略时按标题规则识别；如「云下载 动漫 magnet:?xt=…」"
)

# 注册到 Telegram 的指令菜单：输入框左侧「菜单」按钮 / 输入 / 时可见
BOT_MENU_COMMANDS = [
    {"command": "status", "description": "运行状态（队列/限额/空间）"},
    {"command": "ls", "description": "最近 5 条转存记录"},
    {"command": "menu", "description": "快捷操作菜单（按钮面板）"},
    {"command": "add", "description": "添加频道：/add @频道 on|off"},
    {"command": "sub", "description": "追剧订阅：查看/添加/删除"},
    {"command": "archive", "description": "归档状态；/archive run 立即归档"},
    {"command": "start", "description": "启动系统"},
    {"command": "stop", "description": "停止系统（两步确认）"},
    {"command": "retry", "description": "重试全部失败任务"},
    {"command": "backfill", "description": "向前补全剧集（查找早期分集）"},
    {"command": "backup", "description": "导出完整数据备份文件"},
    {"command": "restore", "description": "从备份文件恢复数据"},
    {"command": "help", "description": "显示帮助"},
]
