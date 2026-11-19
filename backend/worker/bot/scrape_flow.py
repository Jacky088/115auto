"""TG Bot 历史刮削交互回调与提示逻辑。"""


async def _answer_scrape_cq(client, token: str, cq_id: str, text: str = "", show_alert: bool = False):
    try:
        payload = {"callback_query_id": cq_id}
        if text:
            payload["text"] = text[:190]
        if show_alert:
            payload["show_alert"] = True
        await client.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json=payload)
    except Exception:
        pass


async def _handle_scrape_callback(client, token: str, chat_id: str, cq: dict) -> None:
    """兼容拦截历史 TG 刮削交互按钮：不再从 TG 选片/提交，引导用户前往网页端「整理刮削」操作。"""
    cq_id = str(cq.get("id") or "")
    await _answer_scrape_cq(
        client,
        token,
        cq_id,
        "刮削确认与提交队列已调整为统一在网页「整理刮削 → 待确认列表」操作",
        show_alert=True,
    )


def _handle_scid_command(text: str = "") -> str:
    """`/scid` 指令已下线：提示用户前往网页端「整理刮削 → 待确认列表」选片并提交队列。"""
    return "⚠️ TG Bot 已移除手动绑定与提交刮削功能，请前往网页端「整理刮削 → 待确认列表」点选或搜索 TMDB 后点击「提交队列」。"
