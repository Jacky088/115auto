"""每日运行摘要推送与定时自动备份（JSON / Gzip 发送至 TG Bot）。"""
import asyncio
import datetime
import time

from ... import database as db
from ...core import logstream
from ...pan115 import client as pan

def _jobs():
    """延迟获取 jobs 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import jobs
    return jobs


def _bj_now():
    """摘要调度统一用北京时间：不依赖容器/宿主机时区配置（配置错也不影响 21 点推送）。"""
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # 系统缺 zoneinfo 数据时按 UTC+8 手动换算
        return datetime.now(timezone(timedelta(hours=8)))


def digest_text() -> str:
    """汇总当天的运行情况（成功/失败/队列/空间/追剧进度）。"""
    day_start = _bj_now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = db.query("SELECT status, count(*) c FROM transfers WHERE created_at>=? GROUP BY status",
                    (day_start,))
    by = {r["status"]: r["c"] for r in rows}
    queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
    lines = ["📊 每日摘要", "",
             f"✅ 今日成功：{by.get('success', 0)}",
             f"❌ 失败/拦截：{by.get('failed', 0) + by.get('blocked', 0)}",
             f"⏭ 跳过/重复：{by.get('skipped', 0) + by.get('duplicate', 0)}",
             f"📥 队列剩余：{queued}"]
    if db.get_setting("cookie_ok", None):
        try:
            sp = pan.space_info()
            tb = lambda b: f"{b / (1 << 40):.1f}TB"
            lines.append(f"💾 115 空间：已用 {tb(sp['use'])}/{tb(sp['total'])}（{sp['ratio']}%）")
        except Exception:
            pass
    subs = db.query("SELECT keyword, last_ep, last_title FROM subscriptions"
                    " WHERE enabled=1 AND last_match_at IS NOT NULL ORDER BY last_match_at DESC LIMIT 5")
    if subs:
        lines.append("")
        lines.append("🎬 追剧进度：")
        for s in subs:
            ep = f"（{s['last_ep']}）" if s["last_ep"] else ""
            lines.append(f"  · {s['keyword']}{ep}")
    return "\n".join(lines)


async def digest_loop():
    """每日摘要：每天在设定时间推送一次运行汇总（默认 21:00，可关）。

    只有至少一个渠道发送成功才标记 digest_last_sent，失败时下轮（10 分钟后）
    自动重试，避免一次网络抖动导致当天摘要永久丢失。
    时间判定固定按北京时间（Asia/Shanghai），与容器/宿主机时区无关。
    """
    while True:
        try:
            if await asyncio.to_thread(db.get_setting, "digest_enabled", True):
                now = _bj_now()
                today = now.strftime("%Y-%m-%d")
                target = str(await asyncio.to_thread(db.get_setting, "digest_time", "21:00") or "21:00")
                now_hm = now.strftime("%H:%M")
                last = str(await asyncio.to_thread(db.get_setting, "digest_last_sent", "") or "")
                if last != today and now_hm >= target:
                    # digest_text 内部包含同步 pan.space_info() 网络调用：放到专用线程池防止阻塞事件循环或挤占默认池
                    text = await pan.run_pan_thread(_jobs().digest_text)
                    detail = await pan.run_pan_thread(_jobs().notify_detail, text)
                    if any(detail.values()):
                        await asyncio.to_thread(db.set_setting, "digest_last_sent", today)
                        sent = "、".join(k for k, v in detail.items() if v)
                        logstream.info("system", f"已推送每日摘要通知（渠道：{sent}）")
                    else:
                        # 全部渠道失败：记 warn 便于排查（未配置 Token/网络不通等），
                        # 不标记已发送，10 分钟后自动重试
                        stat = "、".join(f"{k}:{'成功' if v else '失败/未配置'}"
                                         for k, v in detail.items())
                        logstream.warn("system",
                                       f"每日摘要推送失败（{stat}）。"
                                       "请检查「通知」区渠道配置，或在设置页点「立即推送一次」验证"
                                       "（10 分钟后自动重试）")
        except Exception as e:
            logstream.error("system", f"每日摘要推送失败: {logstream.exc(e)}")
        await asyncio.sleep(600)


# ---------- 定时自动备份 ----------
def _backup_json() -> bytes:
    import json
    return json.dumps(db.backup_payload(), ensure_ascii=False, indent=1).encode()


async def auto_backup_loop():
    """定时自动备份：按用户设定的间隔天数与具体时间点，通过多途径（TG Bot、WebDAV、本地）分发执行。"""
    while True:
        try:
            def _load_backup_meta():
                channels_raw = db.get_setting("auto_backup_channels", [])
                if isinstance(channels_raw, list):
                    channels = [str(x) for x in channels_raw]
                elif isinstance(channels_raw, str):
                    channels = [x.strip() for x in channels_raw.split(",") if x.strip()]
                else:
                    channels = []
                return (
                    bool(db.get_setting("auto_backup_enabled", False)),
                    int(db.get_setting("auto_backup_days", 7) or 7),
                    str(db.get_setting("notify_token", "") or "").strip(),
                    str(db.get_setting("notify_chat_id", "") or "").strip(),
                    str(db.get_setting("auto_backup_time", "04:00") or "04:00").strip(),
                    str(db.get_setting("backup_last_sent_date", "") or "").strip(),
                    float(db.get_setting("backup_last_sent", 0) or 0),
                    channels,
                    int(db.get_setting("webdav_keep_count", 10) or 10),
                    bool(db.get_setting("webdav_last_test_ok", False)),
                )

            (
                enabled, days, token, chat_id, target_time, last_sent_date, last_ts,
                channels, dav_keep, dav_test_ok,
            ) = await asyncio.to_thread(_load_backup_meta)

            if enabled and days > 0 and channels:
                now = _bj_now()
                today_str = now.strftime("%Y-%m-%d")
                now_hm = now.strftime("%H:%M")

                if not last_sent_date and last_ts > 0:
                    last_sent_date = datetime.datetime.fromtimestamp(
                        last_ts, tz=_bj_now().tzinfo).strftime("%Y-%m-%d")

                should_send = False
                if not last_sent_date:
                    if now_hm >= target_time:
                        should_send = True
                else:
                    try:
                        last_d = datetime.datetime.strptime(last_sent_date, "%Y-%m-%d").date()
                        diff_days = (now.date() - last_d).days
                        if diff_days >= days and now_hm >= target_time and last_sent_date != today_str:
                            should_send = True
                    except Exception:
                        should_send = True

                if should_send:
                    # 1. Telegram Bot 途径
                    if "tgbot" in channels:
                        if token and chat_id:
                            raw = await asyncio.to_thread(_backup_json)
                            if len(raw) > 8 * 1024 * 1024:
                                import gzip
                                content = gzip.compress(raw, compresslevel=9)
                                fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json.gz"
                                mime = "application/gzip"
                                caption = f"🗄 定时自动备份（Gzip 压缩，原始 {len(raw) // 1024}KB → 压缩后 {len(content) // 1024}KB）"
                            else:
                                content = raw
                                fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
                                mime = "application/json"
                                caption = f"🗄 定时自动备份（核心设置/业务规则/转存与整理台账，{len(content) // 1024}KB）"

                            ok = _jobs().enqueue_tg_document(fname, content, mime, caption)
                            if ok:
                                logstream.info("system", f"定时自动备份已加入 TG 发送队列（{fname}，{len(content) // 1024}KB）")
                            else:
                                logstream.warn("system", "定时自动备份入队 TG 失败（队列已满）")
                        else:
                            logstream.warn("system", "定时自动备份已跳过 TG Bot 推送：未配置 Bot Token 或 Chat ID")

                    # 2. WebDAV 云端途径
                    if "webdav" in channels:
                        if dav_test_ok:
                            from pathlib import Path
                            from ...core import migration_bundle, webdav

                            client = await asyncio.to_thread(webdav.create_configured_client)
                            if client:
                                def _run_dav_backup():
                                    import shutil
                                    work_dir = None
                                    try:
                                        work_dir, zip_path, fname, _ = migration_bundle.build_migration_bundle(
                                            include_credentials=False,
                                            meta_mode="nfo",
                                            slim_db=True,
                                            bundle_type="auto",
                                        )
                                        up_res = client.upload_file(zip_path, fname)
                                        pruned = client.prune_backups(dav_keep)
                                        return fname, up_res["bytes"], pruned
                                    finally:
                                        if work_dir and Path(work_dir).exists():
                                            shutil.rmtree(work_dir, ignore_errors=True)

                                try:
                                    fname, size_b, pruned = await asyncio.to_thread(_run_dav_backup)
                                    db.set_setting("webdav_last_backup_date", today_str)
                                    db.set_setting("webdav_last_backup_at", time.time())
                                    db.set_setting("webdav_last_backup_status", f"成功 ({fname})")
                                    prune_msg = f"，已自动轮转清理 {pruned} 份旧备份" if pruned > 0 else ""
                                    logstream.info("system", f"定时整机迁移包已成功上传至 WebDAV（{fname}，{size_b // 1024} KB{prune_msg}）")
                                except Exception as e:
                                    logstream.error("system", f"定时整机备份上传 WebDAV 失败: {logstream.exc(e)}")
                                    db.set_setting("webdav_last_backup_status", f"失败: {str(e)[:100]}")
                        else:
                            logstream.warn("system", "定时自动备份已跳过 WebDAV：未配置或未通过测试连接")

                    # 3. 本地存储途径
                    if "local" in channels:
                        def _run_local_backup():
                            from ... import config
                            import gzip
                            local_dir = config.DATA_DIR / "backups"
                            local_dir.mkdir(parents=True, exist_ok=True)
                            raw = _backup_json()
                            content = gzip.compress(raw, compresslevel=9)
                            fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json.gz"
                            (local_dir / fname).write_bytes(content)
                            # 自动轮转保留最新 10 份
                            existing = sorted(local_dir.glob("autosave_backup_*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
                            if len(existing) > 10:
                                for old_p in existing[10:]:
                                    try:
                                        old_p.unlink()
                                    except Exception:
                                        pass
                            return fname, len(content)

                        try:
                            loc_name, loc_size = await asyncio.to_thread(_run_local_backup)
                            logstream.info("system", f"定时自动备份已写入本地目录（{loc_name}，{loc_size // 1024} KB）")
                        except Exception as e:
                            logstream.error("system", f"定时自动备份写入本地失败: {logstream.exc(e)}")

                    # 标记当轮备份已完成
                    def _mark_backup_sent():
                        db.set_setting("backup_last_sent", time.time())
                        db.set_setting("backup_last_sent_date", today_str)

                    await asyncio.to_thread(_mark_backup_sent)
        except Exception as e:
            logstream.error("system", f"自动备份异常: {logstream.exc(e)}")
        await asyncio.sleep(60)
