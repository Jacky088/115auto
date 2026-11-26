"""TG Bot 刮削待确认快捷审批回调与提示逻辑。"""
import asyncio


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


async def _edit_card(client, token: str, chat_id: str, message_id: int, text: str):
    if message_id <= 0:
        return
    try:
        await client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": str(chat_id),
                "message_id": int(message_id),
                "text": text,
                "reply_markup": {"inline_keyboard": []},
            },
        )
    except Exception:
        pass


async def _handle_scrape_callback(client, token: str, chat_id: str, cq: dict) -> None:
    """处理 TG Bot 刮削待确认快捷审批按钮（m:pick:<tid>:<idx> / m:skip:<tid>）。"""
    from .. import media_worker

    cq_id = str(cq.get("id") or "")
    data = str(cq.get("data") or "")
    msg = cq.get("message") or {}
    msg_id = int(msg.get("message_id") or 0)

    parts = data.split(":")
    if len(parts) == 4 and parts[1] == "pick":
        try:
            tid, idx = int(parts[2]), int(parts[3])
        except ValueError:
            await _answer_scrape_cq(client, token, cq_id, "无效的任务参数", show_alert=True)
            return
        res = await asyncio.to_thread(media_worker.stage_task_selection, tid, idx, None, "tg")
        if not res.get("ok"):
            await _answer_scrape_cq(client, token, cq_id, res.get("msg") or "操作失败", show_alert=True)
            return
        c_res = await asyncio.to_thread(media_worker.commit_staged_tasks, [tid], "", "tg")
        media_worker.schedule_commit_runner_if_idle()
        cand = res.get("selected") or {}
        label = f"《{cand.get('title') or ''}》" + (f" ({cand.get('year')})" if cand.get("year") else "")
        note = "✅ 已确认并提交刮削队列" if c_res.get("ok") else "✅ 已暂存选择（请稍后在网页提交）"
        await _answer_scrape_cq(client, token, cq_id, f"{note}：{label}")
        await _edit_card(client, token, chat_id, msg_id, f"{note}：#{tid} {label}")
        return

    if len(parts) == 3 and parts[1] == "skip":
        try:
            tid = int(parts[2])
        except ValueError:
            await _answer_scrape_cq(client, token, cq_id, "无效的任务参数", show_alert=True)
            return
        res = await asyncio.to_thread(media_worker.ignore_task, tid, "tg")
        if not res.get("ok"):
            await _answer_scrape_cq(client, token, cq_id, res.get("msg") or "忽略失败", show_alert=True)
            return
        await _answer_scrape_cq(client, token, cq_id, f"⏭ 已跳过忽略 #{tid}")
        await _edit_card(client, token, chat_id, msg_id, f"⏭ 已跳过忽略：#{tid}")
        return

    await _answer_scrape_cq(
        client,
        token,
        cq_id,
        "刮削确认与提交队列已调整为统一在网页「整理刮削 → 待确认列表」操作",
        show_alert=True,
    )


def _handle_scid_command(text: str = "") -> str:
    """`/scid` 指令已下线：提示用户前往网页端「整理刮削 → 待确认列表」选片并提交队列，或点击推送卡片按钮操作。"""
    return "ℹ️ 请直接点击刮削待确认推送卡片上的候选按钮完成快捷确认，或前往网页端「整理刮削 → 待确认列表」选片并点击「提交队列」。"
