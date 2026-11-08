"""频道与 Cookie 校验统一入口：项目里所有「新增」操作共用，不得绕过。

- 新增 TG 频道（网页弹窗 / 初始化向导 / Bot /add）→ check_channel_access()
- 新增 115 Cookie（手动导入及未来其他方式）→ pan115.client.validate_cookie()

校验规则：
- polling → 能打开 https://t.me/s/<频道> 网页（公开频道）
- userbot → 已登录的 Userbot 账号能解析到该频道实体（含私密频道）
返回 {ok, title, err}，err 为可直接展示给用户的原因。
"""
from . import polling, userbot


async def check_channel_access(name: str, channel_type: str) -> dict:
    """按接入方式校验频道可访问性（统一入口，所有新增频道的地方必须调用）。"""
    if channel_type == "userbot":
        return await userbot.check_channel(name)
    return await polling.check_channel(name)
