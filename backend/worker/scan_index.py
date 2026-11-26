"""扫描索引与异步预览任务（分流整理 / 目录整理刮削 / 重复清理共用）。

解决三件事：
1. 预览不再同步阻塞 HTTP 请求：POST start 立刻返回 job_id，前端轮询 status 显示进度，
   可随时 cancel；避免大目录扫描把请求拖到超时。
2. 扫描请求有预算与节流：默认 1200 次请求 / 480 秒 / 1500 个目录，约 60 次/分钟；
   超预算返回部分结果并标记 truncated，再点「继续扫描」靠索引续跑（无需显式游标）。
3. 扫描结果落 SQLite 索引（scan_dirs + scan_entries）：
   - 目录 10 分钟 TTL 内且未变脏 → 0 请求直接读索引；
   - 索引过期/变脏 → 按 user_ptime 新→旧翻页增量，整页 file_id 都已存在即提前停止；
   - 本程序执行整理/移动/删除后经 client._touch_scan_index 把目录标脏，下次只重扫受影响目录。

全局约束：同一时刻只允许一个扫描任务；写任务（转存/整理/删除）优先，
扫描启动前若独占写门禁繁忙则直接拒绝。
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
import uuid
from collections import deque

from .. import database as db
from ..core import logstream, transfer_gate
from ..pan115 import client

SCAN_DIR_TTL = 600.0          # 目录索引有效期（秒）：期内且未变脏直接读索引，0 请求
SCAN_MIN_INTERVAL = 1.0       # 扫描请求最小间隔（≈60 次/分钟，叠加 pan_call 的 0.25s 更稳）
DEFAULT_MAX_REQUESTS = 1200   # 单次扫描预算：115 列表请求数
DEFAULT_MAX_SECONDS = 480.0   # 单次扫描预算：墙钟秒数
DEFAULT_MAX_DIRS = 1500       # 单次扫描预算：目录数
MAX_ENTRIES_PER_DIR = 5000    # 单目录最多索引条数（超出标记 dirty，下次继续）
SCOPE_CONFIRM_DIRS = 20000    # 预估目录数超过此值：先弹确认（分批扫描）
SCOPE_REJECT_DIRS = 100000    # 预估目录数超过此值：直接拒绝全盘扫描
SCOPE_REQUESTS_PER_ROUND = 480  # 每轮约可发起的请求数（480 秒预算、约 1 秒/请求）
_SKIP_DIR_NAMES = {"@eadir", ".ds_store", "__macosx", "_待清理旧版"}
_JOB_KEEP = 30


class ScanCancelled(Exception):
    """扫描任务被用户取消或系统停止指令中断。"""


class ScanContext:
    """一次扫描的进度、预算与取消信号（跨线程使用，全部经锁保护）。"""

    def __init__(self, kind: str, max_requests: int = DEFAULT_MAX_REQUESTS,
                 max_seconds: float = DEFAULT_MAX_SECONDS, max_dirs: int = DEFAULT_MAX_DIRS):
        self.kind = kind
        self.max_requests = max(1, int(max_requests))
        self.max_seconds = max(5.0, float(max_seconds))
        self.max_dirs = max(1, int(max_dirs))
        self.started = time.monotonic()
        self.requests = 0
        self.dirs = 0
        self.files = 0
        self.truncated = False
        self.phase = "starting"
        self.label = "准备扫描"
        self.cached_dirs = 0
        self._cancel = threading.Event()
        self._cancel_reason = ""
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    # ---- 取消 ----
    def cancel(self, reason: str = "用户取消") -> None:
        self._cancel_reason = reason or "用户取消"
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise ScanCancelled(self._cancel_reason or "扫描已取消")

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    # ---- 预算 ----
    def can_continue(self) -> bool:
        self.check()
        if transfer_gate.is_exclusive_busy():
            # 写任务优先：扫描在目录间让路，写任务结束后自动继续
            self.set_phase("waiting", "有整理/转存写任务在执行，扫描暂停让路……")
            for _ in range(240):
                self.check()
                if not transfer_gate.is_exclusive_busy():
                    break
                time.sleep(0.5)
            else:
                self.truncated = True
                return False
            self.set_phase("scanning", "已恢复扫描")
        with self._lock:
            if (self.requests >= self.max_requests or self.dirs >= self.max_dirs
                    or self.elapsed() >= self.max_seconds):
                self.truncated = True
                return False
        return True

    def begin_request(self) -> bool:
        """每页 115 请求前调用：节流 + 计数，预算耗尽返回 False。"""
        self.check()
        with self._lock:
            now = time.monotonic()
            wait = 0.0
            if self._last_request_at > 0:
                wait = self._last_request_at + SCAN_MIN_INTERVAL - now
            self._last_request_at = now + max(0.0, wait)
        if wait > 0:
            time.sleep(min(wait, 3.0))
        with self._lock:
            self.requests += 1
            if self.requests > self.max_requests or self.elapsed() >= self.max_seconds:
                self.truncated = True
                return False
        return True

    def begin_dir(self) -> bool:
        self.check()
        with self._lock:
            self.dirs += 1
            if self.dirs > self.max_dirs:
                self.truncated = True
                return False
        return True

    def add_files(self, n: int) -> None:
        with self._lock:
            self.files += max(0, int(n))

    def add_cached_dir(self) -> None:
        with self._lock:
            self.cached_dirs += 1

    def set_phase(self, phase: str, label: str = "") -> None:
        with self._lock:
            self.phase = phase
            if label:
                self.label = label

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "phase": self.phase,
                "label": self.label,
                "requests": self.requests,
                "dirs": self.dirs,
                "files": self.files,
                "cached_dirs": self.cached_dirs,
                "elapsed": round(self.elapsed(), 1),
                "truncated": self.truncated,
                "budget": {
                    "max_requests": self.max_requests,
                    "max_seconds": self.max_seconds,
                    "max_dirs": self.max_dirs,
                },
            }


# ---------------- 异步预览任务注册表 ----------------

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_ACTIVE_JOB_ID: str | None = None


def start_job(kind: str, runner) -> dict:
    """启动一个后台扫描任务。runner 为 async callable(ctx) -> dict。"""
    global _ACTIVE_JOB_ID
    if transfer_gate.is_exclusive_busy():
        mode = transfer_gate.get_active_mode()
        return {"ok": False, "msg": f"当前有互斥写任务（{mode}）正在执行，写任务优先，"
                                     f"请待整理/转存结束后再扫描"}
    with _JOBS_LOCK:
        cur = _JOBS.get(_ACTIVE_JOB_ID or "")
        if cur and cur["state"] == "running":
            return {"ok": False, "msg": f"已有扫描任务正在执行（{cur['kind']}），"
                                         f"请等待结束或先取消"}
        job_id = uuid.uuid4().hex[:12]
        ctx = ScanContext(kind)
        _JOBS[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "state": "running",
            "created_at": time.time(),
            "ctx": ctx,
            "result": None,
            "error": "",
        }
        _ACTIVE_JOB_ID = job_id
        _prune_jobs_locked()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:  # 无事件循环（脚本/测试）：同步跑完
        _run_job_sync(job_id, runner, ctx)
    else:
        _JOBS[job_id]["task"] = loop.create_task(_run_job(job_id, runner, ctx))
    return {"ok": True, "job_id": job_id, "kind": kind}


def _prune_jobs_locked() -> None:
    if len(_JOBS) <= _JOB_KEEP:
        return
    done = sorted((j for j in _JOBS.values() if j["state"] != "running"),
                  key=lambda j: j["created_at"])
    for j in done[:max(0, len(_JOBS) - _JOB_KEEP)]:
        _JOBS.pop(j["job_id"], None)


def _finish_job(job_id: str, state: str, result: dict | None, error: str = "") -> None:
    global _ACTIVE_JOB_ID
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job:
            # 先落结果再落终态：状态查询是另加锁读快照，避免读到 done 但 result 仍为空
            job["result"] = result
            job["error"] = error
            job["finished_at"] = time.time()
            job["state"] = state
        if _ACTIVE_JOB_ID == job_id:
            _ACTIVE_JOB_ID = None


async def _run_job(job_id: str, runner, ctx: ScanContext) -> None:
    try:
        ctx.set_phase("scanning", "正在扫描 115 目录")
        result = await runner(ctx)
        if not isinstance(result, dict):
            result = {"ok": True, "data": result}
        if ctx.truncated and result.get("ok", True):
            result["truncated"] = True
            result.setdefault(
                "msg", "本轮扫描达到预算上限，已返回部分结果；点击「继续扫描」可基于索引快速续跑")
        _finish_job(job_id, "done" if result.get("ok", True) else "failed", result)
    except ScanCancelled as e:
        logstream.info("scan", f"扫描任务 {job_id} 已取消：{e}")
        _finish_job(job_id, "cancelled",
                    {"ok": False, "cancelled": True, "msg": str(e) or "扫描已取消"})
    except Exception as e:
        logstream.warn("scan", f"扫描任务 {job_id} 失败：{e}")
        _finish_job(job_id, "failed", {"ok": False, "msg": f"扫描失败：{str(e)[:300]}"},
                    error=str(e)[:500])


def _run_job_sync(job_id: str, runner, ctx: ScanContext) -> None:
    try:
        coro = runner(ctx)
        if asyncio.iscoroutine(coro):
            result = asyncio.run(coro)
        else:
            result = coro
        if ctx.truncated and isinstance(result, dict) and result.get("ok", True):
            result["truncated"] = True
        _finish_job(job_id, "done" if isinstance(result, dict) and result.get("ok", True)
                    else "failed", result if isinstance(result, dict) else {"ok": True})
    except ScanCancelled as e:
        _finish_job(job_id, "cancelled", {"ok": False, "cancelled": True, "msg": str(e)})
    except Exception as e:
        _finish_job(job_id, "failed", {"ok": False, "msg": f"扫描失败：{str(e)[:300]}"},
                    error=str(e)[:500])


def job_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id or ""))
    if not job:
        return {"ok": False, "msg": "扫描任务不存在或已过期，请重新发起"}
    ctx: ScanContext = job["ctx"]
    out = {"ok": True, "job_id": job["job_id"], "kind": job["kind"], "state": job["state"]}
    out.update(ctx.snapshot())
    if job["state"] != "running":
        out["result"] = job["result"] or {}
        if job.get("error"):
            out["error"] = job["error"]
    return out


def cancel_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id or ""))
    if not job:
        return {"ok": False, "msg": "扫描任务不存在或已过期"}
    if job["state"] != "running":
        return {"ok": True, "msg": "扫描任务已结束"}
    job["ctx"].cancel("用户取消扫描")
    return {"ok": True, "msg": "已请求取消，将在当前请求返回后安全停止"}


# ---------------- 索引读写 ----------------

def _entry_row(e: dict, cid: int, path: str) -> tuple:
    name = str(e.get("name") or "")
    full = f"{path.rstrip('/')}/{name}"
    return (
        cid, str(e.get("id") or ""), name, 1 if e.get("is_dir") else 0,
        int(e.get("size") or 0), str(e.get("sha1") or ""), str(cid), full,
        float(e.get("uptime") or 0), time.time(),
    )


def _persist_entries(cid: int, path: str, entries: list[dict], mark_gone: bool = True) -> int:
    now = time.time()
    with db.transaction() as conn:
        for e in entries:
            fid = str(e.get("id") or "")
            name = str(e.get("name") or "")
            if not fid or not name:
                continue
            conn.execute(
                "INSERT INTO scan_entries(cid,file_id,name,is_dir,size,sha1,parent_cid,path,"
                "uptime,seen_at,gone_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL) "
                "ON CONFLICT(cid,file_id) DO UPDATE SET name=excluded.name,"
                "is_dir=excluded.is_dir,size=excluded.size,sha1=excluded.sha1,"
                "parent_cid=excluded.parent_cid,path=excluded.path,"
                "uptime=excluded.uptime,seen_at=excluded.seen_at,gone_at=NULL",
                _entry_row(e, cid, path),
            )
        if mark_gone:
            # 本轮未见到的旧条目视为已消失（软删，不物理删除以便判重口径稳定）
            conn.execute(
                "UPDATE scan_entries SET gone_at=? WHERE cid=? AND seen_at<? AND gone_at IS NULL",
                (now, cid, now))
    return len(entries)


def _read_index(cid: int) -> list[dict]:
    rows = db.query(
        "SELECT file_id,name,is_dir,size,sha1,uptime FROM scan_entries "
        "WHERE cid=? AND gone_at IS NULL ORDER BY uptime DESC, file_id ASC", (int(cid),))
    return [{"id": r["file_id"], "name": r["name"], "is_dir": bool(r["is_dir"]),
             "size": int(r["size"] or 0), "sha1": r["sha1"] or "",
             "uptime": float(r["uptime"] or 0)} for r in rows]


def _dir_row(cid: int) -> dict | None:
    row = db.query("SELECT * FROM scan_dirs WHERE cid=?", (int(cid),), one=True)
    return dict(row) if row else None


def _known_ids(cid: int) -> set[str]:
    rows = db.query("SELECT file_id FROM scan_entries WHERE cid=? AND gone_at IS NULL",
                    (int(cid),))
    return {str(r["file_id"]) for r in rows}


def _supports_incremental(fn) -> bool:
    """真实 client.list_entries 支持增量参数；测试里的替身函数只接受 (cid, limit)。"""
    code = getattr(fn, "__code__", None)
    if code is None:
        return False
    args = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]
    return "known_ids" in args


def _scan_dir_live(cid: int, max_entries: int, known: set[str],
                   ctx: ScanContext | None) -> tuple[list[dict], dict]:
    state: dict = {}
    fn = client.list_entries
    if _supports_incremental(fn):
        entries = fn(cid, limit=max_entries, known_ids=known or None, state=state,
                     before_request=(ctx.begin_request if ctx is not None else None))
    else:
        # 测试替身/旧实现：无增量参数，一次性返回全部（视为完整扫描）
        if ctx is not None and not ctx.begin_request():
            state["complete"] = False
        entries = fn(cid, limit=max_entries)
        state.setdefault("complete", True)
    return entries, state


def list_dir_cached(cid: int, path: str, ctx: ScanContext | None = None,
                    use_cache: bool = True, max_entries: int = MAX_ENTRIES_PER_DIR) -> list[dict]:
    """列出一个目录的条目（优先索引）。

    - 索引 TTL 内且未变脏：0 请求直接返回；
    - 否则增量重扫本目录（整页 file_id 已知即提前停止），并回写索引；
    - 命中「频繁/limit/405」风控关键词时暂停退避重试（最多 3 次），避免连续撞风控。
    """
    cid = int(cid)
    path = "/" + str(path or "").strip("/")
    row = _dir_row(cid)
    now = time.time()
    if use_cache and row and not int(row["dirty"] or 0) \
            and now - float(row["last_scan_at"] or 0) < SCAN_DIR_TTL:
        if ctx is not None:
            ctx.add_cached_dir()
        return _read_index(cid)

    known = _known_ids(cid) if row else set()
    entries: list[dict] = []
    state: dict = {}
    for attempt in range(3):
        try:
            entries, state = _scan_dir_live(cid, max_entries, known, ctx)
            break
        except ScanCancelled:
            raise
        except Exception as e:
            risky = client._hits_risk_keyword(str(e)) if hasattr(client, "_hits_risk_keyword") else False
            if not risky or attempt >= 2:
                raise
            client.note_115_error(str(e))
            delay = 10 * (attempt + 1)
            if ctx is not None:
                ctx.set_phase("waiting", f"115 提示访问频繁，暂停 {delay} 秒后重试……")
            time.sleep(delay)
    else:  # pragma: no cover - 循环必然 break 或 raise
        raise client.Pan115Error(f"扫描目录 {path} 失败")
    complete = bool(state.get("complete", True))
    early_stopped = bool(state.get("early_stopped", False))

    # 仅完整扫描才做“消失”标记：整页已知提前停止时更旧的页未观察，
    # 若照常标记会把未翻到的旧条目全部误判为已消失。
    _persist_entries(cid, path, entries, mark_gone=complete and not early_stopped)
    if ctx is not None:
        ctx.add_files(sum(1 for e in entries if not e.get("is_dir")))
    entry_count = db.query(
        "SELECT COUNT(*) c FROM scan_entries WHERE cid=? AND gone_at IS NULL",
        (cid,), one=True)["c"]
    scanned_at = time.time()
    if complete:
        db.execute(
            "INSERT INTO scan_dirs(cid,path,entry_count,last_scan_at,last_full_at,dirty) "
            "VALUES(?,?,?,?,?,0) ON CONFLICT(cid) DO UPDATE SET path=excluded.path,"
            "entry_count=excluded.entry_count,last_scan_at=excluded.last_scan_at,"
            "last_full_at=excluded.last_full_at,dirty=0",
            (cid, path, int(entry_count or 0), scanned_at, scanned_at))
    else:
        # 未扫完整（预算/单目录上限）：保留 dirty，下次继续，避免用残缺索引冒充完整目录
        db.execute(
            "INSERT INTO scan_dirs(cid,path,entry_count,last_scan_at,last_full_at,dirty) "
            "VALUES(?,?,?,?,NULL,1) ON CONFLICT(cid) DO UPDATE SET path=excluded.path,"
            "entry_count=excluded.entry_count,last_scan_at=excluded.last_scan_at,dirty=1",
            (cid, path, int(entry_count or 0), scanned_at))
    return entries


def mark_dirty_for_file_ids(file_ids: list[str]) -> None:
    """把指定文件所在的目录标脏（写操作后调用），下次预览重扫该目录。"""
    ids = [str(x) for x in file_ids if x]
    if not ids:
        return
    cids: set[int] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = db.query(
            "SELECT DISTINCT cid FROM scan_entries WHERE file_id IN ("
            + ",".join("?" for _ in chunk) + ")", tuple(chunk))
        cids.update(int(r["cid"]) for r in rows)
    if cids:
        db.execute("UPDATE scan_dirs SET dirty=1 WHERE cid IN ("
                   + ",".join("?" for _ in cids) + ")", tuple(cids))


def mark_all_dirty() -> None:
    db.execute("UPDATE scan_dirs SET dirty=1")


def index_stats() -> dict:
    d = db.query("SELECT COUNT(*) c FROM scan_dirs", one=True)["c"]
    e = db.query("SELECT COUNT(*) c FROM scan_entries WHERE gone_at IS NULL", one=True)["c"]
    last = db.query("SELECT MAX(last_scan_at) t FROM scan_dirs", one=True)["t"]
    return {"dirs": int(d or 0), "entries": int(e or 0), "last_scan_at": float(last or 0)}


def clear_index() -> dict:
    db.execute("DELETE FROM scan_entries")
    db.execute("DELETE FROM scan_dirs")
    return {"ok": True, "msg": "扫描索引已清空，下次预览将重新建立"}


def validate_scan_root(path: str) -> tuple[bool, str]:
    """根目录 "/" 永不做即时全盘预览（几 PB 盘必须选具体子目录）。"""
    if not str(path or "").strip("/"):
        return False, ("根目录不做即时全盘预览：请选择具体子目录（如 /电影、/电视剧）后再整理。"
                       "全盘扫描请求量极大且极易触发 115 风控。")
    return True, ""


def estimate_scope(paths: list[str]) -> dict:
    """按扫描索引里的历史规模估算本次扫描范围（仅统计已建立索引的目录/条目）。

    首次扫描（索引为空）无法预估，known=False 时不做护栏，避免误拦正常小目录预览。
    """
    roots: list[str] = []
    for p in paths or []:
        v = "/" + str(p or "").strip("/")
        if v != "/" and v not in roots:
            roots.append(v)
    # 去掉被其他根目录包含的子目录：嵌套规则（/电影 与 /电影/合集）不重复计数
    roots = [r for r in roots
             if not any(o != r and r.startswith(o + "/") for o in roots)]
    if not roots:
        return {"known": False, "dirs": 0, "entries": 0, "requests": 0, "rounds": 0, "tier": "ok"}
    dirs = 0
    entries = 0
    for r in roots:
        row = db.query("SELECT COUNT(*) c FROM scan_dirs WHERE path=? OR path LIKE ?",
                       (r, r + "/%"), one=True)
        dirs += int(row["c"] or 0)
        row2 = db.query(
            "SELECT COUNT(*) c FROM scan_entries WHERE is_dir=0 AND gone_at IS NULL "
            "AND (path=? OR path LIKE ?)", (r, r + "/%"), one=True)
        entries += int(row2["c"] or 0)
    requests = dirs + (math.ceil(entries / 1150) if entries else 0)
    rounds = max(1, math.ceil(requests / SCOPE_REQUESTS_PER_ROUND)) if requests else 0
    if dirs > SCOPE_REJECT_DIRS:
        tier = "reject"
    elif dirs > SCOPE_CONFIRM_DIRS:
        tier = "confirm"
    else:
        tier = "ok"
    return {"known": dirs > 0, "dirs": dirs, "entries": entries,
            "requests": requests, "rounds": rounds, "tier": tier}


def scope_guard(paths: list[str], confirm_large: bool = False) -> dict | None:
    """扫描前护栏：>2 万目录先确认分批、>10 万目录直接拒绝；放行返回 None。"""
    est = estimate_scope(paths)
    if not est["known"]:
        return None
    if est["tier"] == "reject":
        return {
            "ok": False, "reject": True,
            "msg": (f"预估扫描范围约 {est['dirs']:,} 个目录，已超过 10 万个目录的全盘扫描上限，"
                    f"已拒绝执行。请缩小到具体子目录（如 /电影、/电视剧）后再试。"),
            **est,
        }
    if est["tier"] == "confirm" and not confirm_large:
        return {
            "ok": False, "need_confirm": True,
            "msg": (f"预估扫描范围约 {est['dirs']:,} 个目录 / {est['entries']:,} 个文件，"
                    f"按每轮约 {SCOPE_REQUESTS_PER_ROUND} 次请求估算，预计需要 {est['rounds']} 轮扫描。"
                    f"建议先缩小目录范围；也可以分批继续扫描（每轮结束后再点一次预览即可续跑）。"),
            **est,
        }
    return None


def exclude_organized(items: list[dict], include_organized: bool = False) -> tuple[list[dict], int]:
    """默认排除已整理执行过的文件（organized_records.undone=0），避免预览重复项。"""
    if include_organized or not items:
        return items, 0
    ids = [str(it.get("file_id") or "") for it in items]
    ids = [x for x in ids if x]
    done: set[str] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = db.query(
            "SELECT DISTINCT file_id FROM organized_records WHERE undone=0 AND file_id IN ("
            + ",".join("?" for _ in chunk) + ")", tuple(chunk))
        done.update(str(r["file_id"]) for r in rows)
    if not done:
        return items, 0
    kept = [it for it in items if str(it.get("file_id") or "") not in done]
    return kept, len(items) - len(kept)


# ---------------- 目录树遍历 ----------------

def walk_files(root_cid: int, root_path: str, include_subdirs: bool = True,
               max_depth: int = 8, ctx: ScanContext | None = None,
               collect: bool = True) -> tuple[list[dict], int]:
    """遍历目录下的文件条目，返回 (文件清单, 是否因预算截断)。

    文件项：{file_id, file_name, file_path, sha1, size, uptime}。
    """
    root = "/" + str(root_path or "").strip("/")
    files: list[dict] = []
    queue: deque[tuple[int, str, int]] = deque([(int(root_cid), root, 0)])
    truncated = 0
    while queue:
        if ctx is not None:
            if not ctx.can_continue():
                truncated = 1
                break
            if not ctx.begin_dir():
                truncated = 1
                break
            ctx.set_phase("scanning", f"正在扫描 {queue[0][1]}")
        cid, cur_path, depth = queue.popleft()
        try:
            entries = list_dir_cached(cid, cur_path, ctx)
        except ScanCancelled:
            raise
        except Exception as e:
            logstream.warn("scan", f"扫描目录 {cur_path} 失败: {e}")
            truncated = 1  # 有目录失败：按部分结果返回，提示用户可续扫
            continue
        for e in entries:
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            if e.get("is_dir"):
                if not include_subdirs or depth >= max_depth:
                    continue
                if name.lower() in _SKIP_DIR_NAMES:
                    continue
                try:
                    sub_cid = int(str(e["id"]))
                except (TypeError, ValueError):
                    continue
                queue.append((sub_cid, f"{cur_path.rstrip('/')}/{name}", depth + 1))
                continue
            if collect:
                files.append({
                    "file_id": str(e.get("id") or ""),
                    "file_name": name,
                    "file_path": f"{cur_path.rstrip('/')}/{name}",
                    "sha1": str(e.get("sha1") or "").strip(),
                    "size": int(e.get("size") or 0),
                    "uptime": float(e.get("uptime") or 0),
                })
    return files, truncated


def walk_media_tree(root_cid: int, root_path: str, max_depth: int = 4,
                    ctx: ScanContext | None = None) -> dict:
    """与 client.walk_media_tree 同结构，但目录列表走扫描索引（BDMV 原盘整体保护不变）。"""
    norm_root = "/" + str(root_path or "").strip("/")
    queue_dirs: deque[tuple[int, str, list[str], int]] = deque(
        [(int(root_cid), norm_root, [], 0)])
    files_out: list[dict] = []
    bdmv_out: list[dict] = []
    dirs_out: list[dict] = []
    truncated = False

    while queue_dirs:
        if ctx is not None:
            if not ctx.can_continue() or not ctx.begin_dir():
                truncated = True
                break
            ctx.set_phase("scanning", f"正在扫描 {queue_dirs[0][1]}")
        cur_cid, cur_path, rel_parents, depth = queue_dirs.popleft()
        if depth > max_depth:
            continue
        try:
            entries = list_dir_cached(cur_cid, cur_path, ctx)
        except ScanCancelled:
            raise
        except Exception as e:
            logstream.warn("scan", f"扫描媒体目录 {cur_path} 失败: {e}")
            truncated = True
            continue
        dir_names_lower = {str(e.get("name") or "").lower() for e in entries if e.get("is_dir")}
        if depth > 0 and ("bdmv" in dir_names_lower or "certificate" in dir_names_lower
                          or "video_ts" in dir_names_lower):
            bdmv_out.append({
                "id": str(cur_cid),
                "name": rel_parents[-1],
                "path": cur_path,
                "parent_dirs": rel_parents[:-1],
                "parent_cid": "",
                "is_bdmv": True,
            })
            continue

        sibling_names = [e["name"] for e in entries if not e.get("is_dir")]
        sibling_file_entries = [x for x in entries if not x.get("is_dir")]
        for e in entries:
            if e.get("is_dir"):
                if str(e["name"]).lower() in _SKIP_DIR_NAMES:
                    continue
                sub_path = f"{cur_path.rstrip('/')}/{e['name']}"
                dirs_out.append({
                    "id": str(e["id"]),
                    "name": e["name"],
                    "path": sub_path,
                    "parent_cid": str(cur_cid),
                    "depth": depth + 1,
                })
                if depth + 1 <= max_depth:
                    try:
                        sub_cid = int(str(e["id"]))
                    except (TypeError, ValueError):
                        sub_cid = 0
                    if sub_cid:
                        queue_dirs.append((sub_cid, sub_path,
                                           rel_parents + [e["name"]], depth + 1))
            else:
                files_out.append({
                    "id": str(e["id"]),
                    "name": e["name"],
                    "size": int(e.get("size") or 0),
                    "sha1": e.get("sha1") or "",
                    "uptime": float(e.get("uptime") or 0),
                    "parent_cid": str(cur_cid),
                    "dir_path": cur_path,
                    "parent_dirs": list(rel_parents),
                    "sibling_names": sibling_names,
                    "sibling_entries": sibling_file_entries,
                })
        if ctx is not None:
            ctx.add_files(len(sibling_file_entries))

    dir_parent_map = {d["path"]: d["parent_cid"] for d in dirs_out}
    for b in bdmv_out:
        b["parent_cid"] = dir_parent_map.get(b["path"], "")

    return {"files": files_out, "bdmv_dirs": bdmv_out, "dirs": dirs_out, "truncated": truncated}


# ---------------- 重复清理：基于索引的 SQL 分组 ----------------

def _path_clause(prefix: str, include_subdirs: bool = True) -> tuple[str, tuple]:
    prefix = "/" + str(prefix or "").strip("/")
    if prefix == "/":
        return "1=1", ()
    if include_subdirs:
        return "(path=? OR path LIKE ?)", (prefix, prefix + "/%")
    # 仅当前层：/媒体库/a.mkv 命中，/媒体库/子目录/a.mkv 不命中
    return "(path=? OR (path LIKE ? AND path NOT LIKE ?))", \
        (prefix, prefix + "/%", prefix + "/%/%")


def dedupe_groups(root_path: str, min_bytes: int = 0, include_subdirs: bool = True,
                  max_groups: int = 2000, max_items: int = 500) -> list[dict]:
    """在索引里按 sha1 分组找重复（仅同 sha1 且非空参与），不把全盘文件读进内存。"""
    clause, params = _path_clause(root_path, include_subdirs)
    rows = db.query(
        f"SELECT sha1, COUNT(*) c, MIN(size) msize FROM scan_entries "
        f"WHERE is_dir=0 AND gone_at IS NULL AND sha1!='' AND size>=? AND {clause} "
        f"GROUP BY sha1 HAVING c>1 ORDER BY (msize*(c-1)) DESC LIMIT ?",
        (int(min_bytes), *params, int(max_groups)))
    groups: list[dict] = []
    for r in rows:
        items = db.query(
            f"SELECT file_id,name,path,size,sha1 FROM scan_entries "
            f"WHERE is_dir=0 AND gone_at IS NULL AND sha1=? AND size>=? AND {clause} "
            f"ORDER BY path ASC, file_id ASC LIMIT ?",
            (r["sha1"], int(min_bytes), *params, int(max_items)))
        if len(items) < 2:
            continue
        group_items = [{
            "file_id": it["file_id"],
            "file_name": it["name"],
            "file_path": it["path"],
            "sha1": it["sha1"] or "",
            "size": int(it["size"] or 0),
        } for it in items]
        size = int(r["msize"] or 0)
        groups.append({
            "sha1": r["sha1"],
            "size": size,
            "freeable": size * (int(r["c"]) - 1),
            "keep_id": group_items[0]["file_id"],
            "items": group_items,
        })
    return groups


def dedupe_subtree_stats(root_path: str, min_bytes: int = 0,
                         include_subdirs: bool = True) -> dict:
    clause, params = _path_clause(root_path, include_subdirs)
    row = db.query(
        f"SELECT COUNT(*) c, "
        f"SUM(CASE WHEN sha1='' THEN 1 ELSE 0 END) no_sha1, "
        f"SUM(CASE WHEN sha1!='' AND size<? THEN 1 ELSE 0 END) small "
        f"FROM scan_entries WHERE is_dir=0 AND gone_at IS NULL AND {clause}",
        (int(min_bytes), *params), one=True)
    return {
        "scanned": int(row["c"] or 0),
        "no_sha1": int(row["no_sha1"] or 0),
        "small_skipped": int(row["small"] or 0),
    }
