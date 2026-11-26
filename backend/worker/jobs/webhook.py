"""通用 Webhook 推送模块：支持企业微信、飞书、钉钉机器人及自定义 JSON 模板。"""
from __future__ import annotations

import json

from ... import database as db


def _jobs():
    from .. import jobs
    return jobs


def _build_payload(preset: str, template: str, message: str) -> dict:
    title = (message.splitlines()[0] if message else "115 自动转存通知")[:80]
    p = (preset or "custom").strip().lower()
    if p in ("wecom", "dingtalk"):
        return {"msgtype": "text", "text": {"content": message}}
    if p == "feishu":
        return {"msg_type": "text", "content": {"text": message}}

    raw_tpl = (template or "").strip()
    if not raw_tpl:
        return {"title": title, "message": message}

    # 以 JSON 字符串安全转义后替换占位符，避免换行/引号破坏 JSON 结构
    rendered = (
        raw_tpl
        .replace('"{{title}}"', json.dumps(title, ensure_ascii=False))
        .replace('"{{message}}"', json.dumps(message, ensure_ascii=False))
        .replace("{{title}}", json.dumps(title, ensure_ascii=False).strip('"'))
        .replace("{{message}}", json.dumps(message, ensure_ascii=False).strip('"'))
    )
    try:
        parsed = json.loads(rendered)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"title": title, "message": message}


def send_webhook(message: str) -> bool:
    """向配置的 Webhook 地址发送 JSON 通知，失败静默返回 False。"""
    url = str(db.get_setting("webhook_url", "") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return False
    preset = str(db.get_setting("webhook_preset", "wecom") or "wecom")
    template = str(db.get_setting("webhook_template", "") or "")
    payload = _build_payload(preset, template, message)
    try:
        resp = _jobs().httpx.post(url, json=payload, timeout=10)
        return 200 <= resp.status_code < 300
    except Exception:
        return False
