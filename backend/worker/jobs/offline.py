"""115 离线/云下载任务状态巡检、磁力/ED2K 标识提取与完成落库通知。"""
import asyncio
import os
import re
import time
from urllib.parse import unquote

from ... import database as db
from ...core import classifier, logstream, transfer_gate
from ...pan115 import client as pan

def _jobs():
    """延迟获取 jobs 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import jobs
    return jobs


OFFLINE_CONFIRM_WINDOW = 3 * 86400  # 云下载提交后 3 天内跟踪结果


def _extract_btih(url: str) -> str:
    """从磁力链提取 40 位 hex hash，支持 base32 与 hex。"""
    u = unquote((url or "").strip())
    m = re.search(r"xt=urn:btih:([0-9A-Za-z]+)", u, re.I)
    if m:
        val = m.group(1).lower()
        if len(val) == 32:
            try:
                import base64
                val = base64.b32decode(val.upper()).hex().lower()
            except Exception:
                pass
        return val
    return ""


def _offline_key(url: str) -> str:
    """云下载 URL 归一化，用于和 115 任务列表里的 url / info_hash 匹配。

    115 任务列表可能把 URL 做 percent-解码/编码、改大小写或去掉结尾斜杠，
    ED2K/磁力这类带中文文件名的链接（如「厨房春光...iso」）直接字符串比对会匹配失败，
    任务因此一直找不到、超窗后被误判为下载失败。ED2K/磁力统一按 hash 匹配，其余按小写。
    """
    u = unquote((url or "").strip())
    if not u:
        return ""
    if u.lower().startswith("ed2k://"):
        parts = u.split("|")
        if len(parts) >= 5 and parts[4]:
            return "ed2k:" + parts[4].lower()
    btih = _extract_btih(u)
    if btih:
        return "btih:" + btih
    return u.rstrip("/").lower()


async def offline_check_loop():
    """核对"已提交"云下载任务的真实结果（下载完成/失败），并推送第②条通知。

    动态调度：存在已提交任务时每 30 秒轮询一次，空闲时每 60 秒轮询一次。
    双重确认机制：
    1. 115 云下载任务列表跟踪（支持 info_hash / btih / ed2k / url / 标题软化归一化）；
    2. 目标目录落盘反查兜底（即使 115 任务列表已被清空或删除，只要目标目录已有对应文件，立即确认完成）。
    """
    logstream.info("transfer", "云下载结果跟踪任务启动（动态轮询核对）")
    while True:
        try:
            subs = [dict(r) for r in await asyncio.to_thread(
                db.query, "SELECT * FROM transfers WHERE status='submitted'")]
            if not subs:
                await asyncio.sleep(60)
                continue

            # 整理独占移动阶段暂停云下载核对，防止与自动移动并发打 115（draining 排空阶段正常放行）
            await transfer_gate.wait_transfer_worker_allowed()

            try:
                tasks = await pan.run_pan_thread(pan.task_list_all)
            except Exception as e:
                logstream.warn("transfer", f"拉取 115 云下载任务列表失败，本轮跳过核对: {e}")
                await asyncio.sleep(30)
                continue

            by_key: dict[str, dict] = {}
            by_name: dict[str, dict] = {}
            for t in tasks:
                raw_url = t.get("url") or ""
                if raw_url:
                    k = _offline_key(raw_url)
                    if k:
                        by_key[k] = t
                ih = str(t.get("info_hash") or "").strip().lower()
                if ih:
                    by_key["btih:" + ih] = t
                t_name = str(t.get("name") or "").strip()
                if t_name:
                    clean_t_name = re.sub(r"[^\w\u4e00-\u9fa5]+", "", t_name.lower())
                    if clean_t_name:
                        by_name[clean_t_name] = t

            fallback_dir_cache: dict[str, list[dict]] = {}
            fallback_scanned = False

            for job in subs:
                t = None
                job_url = job.get("share_url") or ""
                job_key = _offline_key(job_url)

                # 1. 优先按 key (btih / ed2k / url) 匹配
                if job_key:
                    t = by_key.get(job_key)

                # 1.1 尝试按提交时记录的 receive_code (115 返回的 info_hash) 匹配
                if not t and job.get("receive_code"):
                    t = by_key.get("btih:" + str(job["receive_code"]).strip().lower())

                # 2. 次选按标题或磁力 dn 文件名软化匹配
                job_title = job.get("title") or ""
                clean_title = re.sub(r"[^\w\u4e00-\u9fa5]+", "", job_title.lower())
                m_dn = re.search(r"dn=([^&]+)", unquote(job_url))
                dn_str = unquote(m_dn.group(1)).strip() if m_dn else ""
                clean_dn = re.sub(r"[^\w]+", "", dn_str.lower())

                if not t:
                    for cand_key in (clean_title, clean_dn):
                        if not cand_key or len(cand_key) < 3:
                            continue
                        if cand_key in by_name:
                            t = by_name[cand_key]
                            break
                        for kname, t_cand in by_name.items():
                            if cand_key in kname or kname in cand_key:
                                t = t_cand
                                break
                        if t:
                            break

                # 3. 目标目录落盘反查兜底（极强健壮性：无论 115 任务列表是否被清空，只要目标目录文件已落盘即判定完成）
                if not t:
                    target_dir = (job.get("target_dir") or "").strip()
                    if target_dir:
                        try:
                            if target_dir in fallback_dir_cache:
                                entries = fallback_dir_cache[target_dir]
                            else:
                                # 若此时已进入整理移动阶段，跳过本轮其余目录扫描让位给整理任务
                                if transfer_gate.get_gate_state() == "organizing":
                                    break
                                if fallback_scanned:
                                    await asyncio.sleep(0.5 * pan.pace_factor())
                                fallback_scanned = True
                                dest_cid = await pan.run_pan_thread(pan.mkdirs, target_dir)
                                if dest_cid is not None:
                                    entries = await pan.run_pan_thread(pan.list_entries, dest_cid, 500)
                                    fallback_dir_cache[target_dir] = entries
                                else:
                                    entries = []
                            if entries:
                                cn_chunks = re.findall(r"[\u4e00-\u9fa5]+", job_title)
                                cn_subj = max(cn_chunks, key=len) if cn_chunks else ""
                                found_file = None
                                for e in entries:
                                    if e.get("is_dir"):
                                        continue
                                    efname = e.get("name", "")
                                    ext = os.path.splitext(efname)[1].lower()
                                    if ext in pan._MEDIA_EXTENSIONS:
                                        clean_efname = re.sub(r"[^\w]+", "", efname.lower())
                                        if (cn_subj and len(cn_subj) >= 2 and cn_subj in efname) or \
                                           (clean_title and len(clean_title) >= 3 and clean_title in re.sub(r"[^\w\u4e00-\u9fa5]+", "", efname.lower())) or \
                                           (clean_dn and len(clean_dn) >= 3 and (clean_dn in clean_efname or clean_efname in clean_dn)):
                                            found_file = e
                                            break
                                if found_file:
                                    t = {
                                        "name": found_file.get("name", ""),
                                        "status": 2,
                                        "percentDone": 100,
                                        "file_id": found_file.get("id"),
                                        "size": int(found_file.get("size", 0) or 0)
                                    }
                        except Exception as e:
                            logstream.warn("offline", f"云下载落盘反查兜底异常（任务 {job.get('share_url', '')}）: {e}")

                if t is None:
                    # 仍未找到：超过 3 天确认为失效任务，否则保持 submitted 继续等待
                    if time.time() - job["created_at"] > OFFLINE_CONFIRM_WINDOW:
                        await asyncio.to_thread(
                            _finish_offline, job, False, "",
                            "提交后 3 天内未在 115 任务列表及目标目录中找到该任务")
                    continue

                # 提取进度与状态
                try:
                    raw_pct = t.get("percentDone") or t.get("percent_done") or t.get("percent") or 0
                    percent = float(raw_pct or 0)
                except (TypeError, ValueError):
                    percent = 0.0

                try:
                    status = int(t.get("status", 0) or 0)
                except (TypeError, ValueError):
                    status = 0

                has_file = bool(t.get("file_id"))
                task_size = int(t.get("size") or t.get("file_size") or 0)
                task_name = t.get("name") or ""

                # 完成判定：
                # 115 官方规范中：percentDone>=100 或 status==2(已完成) 或 status in (10, 11) 或 status>=13 或已生成 file_id
                if percent >= 100 or status in (2, 10, 11) or status >= 13 or has_file:
                    await asyncio.to_thread(_finish_offline, job, True, task_name, "", task_size)
                elif status == -1 or status in (3, 9):
                    err_msg = t.get("err_msg") or "115 下载任务失败（链接无效或无资源）"
                    await asyncio.to_thread(_finish_offline, job, False, "", err_msg)
                # 其余状态 (如 status 1 下载中, status 4 搜索中, status 12 进行中等) 保持 submitted 等待下一轮

            await asyncio.sleep(30)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logstream.error("transfer", f"云下载结果核对异常: {logstream.exc(e)}")
            await asyncio.sleep(30)


def _finish_offline(job, ok: bool, name: str = "", reason: str = "", size: int = 0):
    job = dict(job)
    if ok:
        shown = classifier.headline(job.get("title", "")) or name or job.get("title", "")
        changed = db.execute("UPDATE transfers SET status='success', title=?, finished_at=?"
                             " WHERE id=? AND status='submitted'",
                             (shown[:200], time.time(), job["id"]))
        if changed == 0:
            return  # 已被其他核对轮次/看门狗处理，避免重复计额与重复通知
        db.quota_incr()
        logstream.info("transfer",
                       f"✅ 云下载任务完成：{shown[:60]} → {job['target_dir']}，文件已保存至目标目录")
        _jobs().notify_transfer(shown, job.get("category", ""), job.get("channel", ""),
                        job["target_dir"], size=size, kind="offline_done")
        _jobs().check_space_alert(f"云下载「{shown[:40]}」完成后"
                          + (f"（本次 {size / (1 << 40):.2f} TB）" if size else ""))
    else:
        changed = db.execute("UPDATE transfers SET status='failed', error=?"
                             " WHERE id=? AND status='submitted'",
                             (reason[:500], job["id"]))
        if changed == 0:
            return
        logstream.error("transfer", f"❌ 云下载任务失败: {job['title'][:60]} — {reason}")
        _jobs().notify_failure("offline", job["title"], job.get("category", ""),
                       job.get("channel", ""), reason,
                       retry_info="💡 该任务已提交 115 云下载但未成功，请检查链接有效性后重新添加")
