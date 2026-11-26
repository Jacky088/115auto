"""TG Bot 备份与恢复全流程处理。"""
import asyncio
import gzip
import json
import time

import httpx

from ... import database as db
from ...core import logstream
from .state import _RESTORE_PENDING, _RESTORE_TTL


def _backup_summary(data: dict) -> str:
    """备份 payload 的分段摘要（用于确认提示与结果展示）。"""
    c = db.backup_counts(data)
    return (
        f"导出于 {c['exported_at']}\n"
        f"配置：设置 {c['settings']}｜频道 {c['channels']}｜规则 {c['rules']}｜路由 {c['routes']}｜黑名单 {c['blacklist']}\n"
        f"业务：订阅 {c['subscriptions']}｜转存 {c['transfers']}｜自动归档 {c['auto_move_rules']}｜"
        f"整理刮削 {c['media_rules']}｜账本 {c['organized_records']}｜待刮削 {c['media_tasks']}"
    )


async def restore_upload_flow(client: httpx.AsyncClient, token: str, chat_id: str,
                              doc: dict) -> str:
    """用户转发备份 JSON/GZ/ZIP 文件给 Bot：下载 → 校验 → 暂存待确认。"""
    size = int(doc.get("file_size") or 0)
    # Telegram Bot API getFile 硬上限为 20MB
    if size > 20 * 1024 * 1024:
        return f"文件过大（{size // 1024} KB），Telegram Bot 下载上限为 20 MB"
    # 下载文件：getFile 拿路径，再从 file 接口取内容（都走 TG 代理）
    r = await client.post(f"https://api.telegram.org/bot{token}/getFile",
                          json={"file_id": doc.get("file_id")},
                          timeout=30)
    fp = (r.json().get("result") or {}).get("file_path") if r.status_code == 200 else None
    if not fp:
        return "无法读取该文件（Telegram getFile 失败），请重新发送"
    f = await client.get(f"https://api.telegram.org/file/bot{token}/{fp}",
                         timeout=60)
    file_name = str(doc.get("file_name") or "").lower()
    try:
        # 大备份文件的 gunzip + json 解析是重 CPU 同步操作，放线程池避免卡事件循环
        data = await asyncio.to_thread(db.parse_backup_bytes, f.content, filename=file_name)
    except ValueError as e:
        msg = str(e)
        if "ZIP 压缩包内未找到" in msg:
            return msg
        if "缺少 _meta 或 settings 标记" in msg:
            return "不是本程序的备份文件（缺少备份标记），已忽略"
        return f"文件解析失败（{msg}），确认发送的是自动备份/网页导出的 .json、.json.gz 或 .zip 文件"
    except Exception as e:
        return f"文件解析失败（{e}），确认发送的是自动备份/网页导出的 .json、.json.gz 或 .zip 文件"
    _RESTORE_PENDING[chat_id] = (data, time.time())
    return ("⚠️ 检测到备份文件\n" + _backup_summary(data) +
            "\n\n恢复将【覆盖】当前全部配置与业务数据（登录密码与通知密钥除外）。\n"
            "确认恢复请回复：/restore confirm（10 分钟内有效，忽略即取消）")


def _restore_guide() -> str:
    """恢复操作三步引导（/menu 按钮、/restore 指令共用）。"""
    return ("♻️ 从备份恢复（三步）：\n"
            "① 发送备份文件（.json / .json.gz / .zip）给本 Bot\n"
            "② 确认我回显的备份摘要\n"
            "③ 回复 /restore confirm 确认恢复（10 分钟内有效）\n\n"
            "恢复将覆盖当前全部配置与业务数据（登录密码与通知密钥除外）。")


def _verify_restore(data: dict) -> str:
    """恢复写库后逐表核对：返回空串=校验通过，否则返回不一致说明。"""
    for t in db.BACKUP_TABLES:
        want = len(data.get(t) or [])
        got = db.query(f"SELECT count(*) c FROM {t}", one=True)["c"]
        if want != got:
            return f"{t} 数量不一致（备份 {want} 条，实际 {got} 条）"
    # settings 逐键核对（跳过恢复时保留本机值的凭据/状态项，以及恢复时清理/重置的运行态项）
    got_map = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM settings")}
    for k, v in (data.get("settings") or {}).items():
        if k in db.BACKUP_KEEP_SETTINGS or db.is_runtime_setting_key(k):
            continue
        if got_map.get(k) != str(v):
            return f"settings[{k}] 恢复后与备份不一致"
    return ""


def _do_restore(chat_id: str) -> str:
    """执行恢复：校验待确认区 → 写库 → 复核数量 → 返回结果。"""
    pending = _RESTORE_PENDING.pop(chat_id, None)
    if not pending:
        return _restore_guide()
    data, ts = pending
    if time.time() - ts > _RESTORE_TTL:
        return "待确认的备份已超时（10 分钟），请重新发送备份文件"
    try:
        db.restore_payload(data)
    except ValueError as e:
        logstream.warn("system", f"TG 恢复备份失败（校验未通过）: {e}")
        return f"❌ 恢复失败：{e}\n当前数据未做任何更改，可放心重试"
    # 写库成功后再逐表复核，确认真实落库才报成功
    mismatch = _verify_restore(data)
    if mismatch:
        logstream.error("system", f"TG 恢复备份后复核不一致: {mismatch}")
        return f"❌ 恢复结果异常（{mismatch}），请查看系统日志，必要时用网页入口重新恢复"
    logstream.info("system", f"已通过 TG 指令恢复备份（{_backup_summary(data)}），复核一致")
    return ("✅ 已从备份恢复（已与备份逐项核对一致）\n" + _backup_summary(data) +
            "\n\n系统已回到停止状态，确认无误后到网页顶栏点「开始运行」。")


def _restore_allowed(msg: dict) -> tuple[bool, str]:
    """备份恢复仅限与 Bot 的私聊：群聊里转发/伪造备份文件的风险面太大。"""
    if str(msg.get("chat", {}).get("type") or "") == "private":
        return True, ""
    return False, ("出于安全考虑，备份恢复仅支持在与 Bot 的私聊中进行：\n"
                   "请私聊本 Bot 发送备份 JSON 文件")


async def send_backup_flow(client: httpx.AsyncClient, token: str, chat_id: str):
    """通过 TG Bot 发送当前完整数据备份（JSON / GZ）。"""
    payload = await asyncio.to_thread(db.backup_payload)
    raw = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    summary = _backup_summary(payload)
    # 体积超过 8MB 时自动使用 gzip 压缩，彻底杜绝 Telegram 20MB 下载/50MB 发送限制
    if len(raw) > 8 * 1024 * 1024:
        content = gzip.compress(raw, compresslevel=9)
        fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json.gz"
        mime = "application/gzip"
        size_str = f"{len(content) // 1024} KB (Gzip 压缩，原始 {len(raw) // 1024} KB)"
    else:
        content = raw
        fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        mime = "application/json"
        size_str = f"{len(content) // 1024} KB"

    caption = (f"🗄 完整数据备份（{size_str}）\n{summary}\n\n"
               "如需恢复，直接把此文件转发回本 Bot，并回复 /restore confirm")
    try:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (fname, content, mime)},
            timeout=60,
        )
        if resp.status_code == 200:
            logstream.info("system", f"已通过 TG 指令发送数据备份（{fname}，{size_str}）")
        else:
            logstream.warn("system", f"TG 指令发送数据备份失败: HTTP {resp.status_code}")
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": f"❌ 备份文件发送失败: HTTP {resp.status_code}"},
                timeout=15,
            )
    except Exception as e:
        logstream.error("system", f"TG 发送备份异常: {logstream.exc(e)}")
        try:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": f"❌ 发送备份异常: {e}"},
                timeout=15,
            )
        except Exception:
            pass
