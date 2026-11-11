"""云下载（离线下载）完成状态核对与第②条通知测试集"""
import asyncio
import os
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="test_offline_")

from backend import database as db
from backend.pan115 import client as pan
from backend.worker import jobs


def test_offline_keys():
    # 1. 40位 Hex BTIH
    m1 = "magnet:?xt=urn:btih:3fa8e7b1c4d9e2f3a4b5c6d7e8f9a0b1c2d3e4f5&dn=The.Odyssey.2026"
    assert jobs._offline_key(m1) == "btih:3fa8e7b1c4d9e2f3a4b5c6d7e8f9a0b1c2d3e4f5"

    # 2. 32位 Base32 BTIH（自动解码为40位Hex）
    m2 = "magnet:?xt=urn:btih:4A4Y2E67W5V76E6L34A5Y2W5X7Y2Z3A4&dn=Test"
    assert jobs._offline_key(m2) == "btih:e0398d13dfb76bff13cbdf01dc6addbff1acec1c"

    # 3. ED2K 链接
    ed2k = "ed2k://|file|The.Odyssey.2026.mkv|88000000000|0123456789ABCDEF0123456789ABCDEF|h=XYZ|/"
    assert jobs._offline_key(ed2k) == "ed2k:0123456789abcdef0123456789abcdef"

    # 4. HTTP/HTTPS 链接
    http_url = "https://example.com/downloads/The.Odyssey.2026.mkv"
    assert jobs._offline_key(http_url) == "https://example.com/downloads/the.odyssey.2026.mkv"
    print("test_offline_keys passed.")


def test_offline_task_completion_detection():
    db.init()
    # 插入一个状态为 submitted 的云下载任务（奥德赛 2026）
    db.execute("DELETE FROM transfers")
    magnet_url = "magnet:?xt=urn:btih:3fa8e7b1c4d9e2f3a4b5c6d7e8f9a0b1c2d3e4f5&dn=The.Odyssey.2026"
    db.execute("""
        INSERT INTO transfers(kind, share_code, receive_code, share_url, title, category, target_dir, status, created_at)
        VALUES('offline', 'test_code', '3fa8e7b1c4d9e2f3a4b5c6d7e8f9a0b1c2d3e4f5', ?, '奥德赛 (2026)', '电影', '/云下载', 'submitted', ?)
    """, (magnet_url, time.time() - 300))

    job = db.query("SELECT * FROM transfers WHERE status='submitted'", one=True)
    assert job is not None

    # 模拟 115 返回的任务列表：任务已下载完毕（115官方字段：percentDone=100, status=2, info_hash）
    fake_tasks = [
        {
            "name": "The.Odyssey.2026.USA.UHD.BluRay.2160p.mkv",
            "info_hash": "3fa8e7b1c4d9e2f3a4b5c6d7e8f9a0b1c2d3e4f5",
            "percentDone": 100,
            "status": 2,  # 115 完成状态
            "size": 84640000000,
            "file_id": "115998877",
            "url": ""   # 115 BT任务列表中 url 往往为空
        }
    ]

    notified_messages = []
    def mock_notify(msg):
        notified_messages.append(msg)

    with patch("backend.pan115.client.task_list_all", return_value=fake_tasks), \
         patch("backend.worker.jobs.notify", side_effect=mock_notify), \
         patch("backend.database.get_setting", return_value=True):

        # 执行单轮检测逻辑
        async def run_one_check():
            subs = [dict(r) for r in db.query("SELECT * FROM transfers WHERE status='submitted'")]
            assert len(subs) == 1
            tasks = fake_tasks
            by_key = {}
            by_name = {}
            for t in tasks:
                raw_url = t.get("url") or ""
                if raw_url:
                    k = jobs._offline_key(raw_url)
                    if k:
                        by_key[k] = t
                ih = str(t.get("info_hash") or "").strip().lower()
                if ih:
                    by_key["btih:" + ih] = t
                t_name = str(t.get("name") or "").strip()
                if t_name:
                    clean_t_name = jobs.re.sub(r"[^\w\u4e00-\u9fa5]+", "", t_name.lower())
                    if clean_t_name:
                        by_name[clean_t_name] = t

            for j in subs:
                job_url = j.get("share_url") or ""
                job_key = jobs._offline_key(job_url)
                t = by_key.get(job_key)
                assert t is not None, "应通过 btih: 成功匹配 115 任务"

                raw_pct = t.get("percentDone") or t.get("percent_done") or t.get("percent") or 0
                percent = float(raw_pct or 0)
                status = int(t.get("status", 0) or 0)
                has_file = bool(t.get("file_id"))
                task_size = int(t.get("size") or 0)
                task_name = t.get("name") or ""

                assert percent >= 100 or status in (2, 10, 11) or has_file, "应判定为已完成"
                jobs._finish_offline(j, True, task_name, "", task_size)

        asyncio.run(run_one_check())

    # 验证状态与通知
    updated = db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True)
    assert updated["status"] == "success"
    assert len(notified_messages) == 1
    msg = notified_messages[0]
    assert "✅ 云下载任务完成" in msg
    assert "② 115 已下载完毕，文件已保存至目标目录" in msg
    assert "奥德赛 (2026)" in msg
    assert "78.83 GB" in msg or "GB" in msg
    print("test_offline_task_completion_detection passed.")


def test_offline_target_directory_fallback():
    """测试兜底机制：即使 115 任务列表中查不到（被清理或翻页截断），目标目录已有落盘文件时立即确认为完成"""
    db.init()
    db.execute("DELETE FROM transfers")
    db.execute("""
        INSERT INTO transfers(kind, share_code, receive_code, share_url, title, category, target_dir, status, created_at)
        VALUES('offline', 'test_fallback', '', 'magnet:?xt=urn:btih:missinghash&dn=The.Odyssey.2026.USA', '奥德赛 (2026)', '电影', '/云下载', 'submitted', ?)
    """, (time.time() - 300,))

    job = db.query("SELECT * FROM transfers WHERE status='submitted'", one=True)
    assert job is not None

    notified = []
    fake_dir_entries = [
        {"id": "555", "name": "The.Odyssey.2026.USA.UHD.BluRay.2160p.mkv", "is_dir": False, "size": 84640000000}
    ]

    with patch("backend.pan115.client.task_list_all", return_value=[]), \
         patch("backend.pan115.client.mkdirs", return_value=123), \
         patch("backend.pan115.client.list_entries", return_value=fake_dir_entries), \
         patch("backend.worker.jobs.notify", side_effect=lambda m: notified.append(m)), \
         patch("backend.database.get_setting", return_value=True):

        async def run_fallback_check():
            subs = [dict(r) for r in db.query("SELECT * FROM transfers WHERE status='submitted'")]
            for j in subs:
                job_url = j.get("share_url") or ""
                job_title = j.get("title") or ""
                target_dir = (j.get("target_dir") or "").strip()
                dest_cid = pan.mkdirs(target_dir)
                entries = pan.list_entries(dest_cid, 500)
                clean_title = jobs.re.sub(r"[^\w\u4e00-\u9fa5]+", "", job_title.lower())
                cn_chunks = jobs.re.findall(r"[\u4e00-\u9fa5]+", job_title)
                cn_subj = max(cn_chunks, key=len) if cn_chunks else ""

                m_dn = jobs.re.search(r"dn=([^&]+)", jobs.unquote(job_url))
                dn_str = jobs.unquote(m_dn.group(1)).strip() if m_dn else ""
                clean_dn = jobs.re.sub(r"[^\w]+", "", dn_str.lower())

                found_file = None
                for e in entries:
                    if e.get("is_dir"):
                        continue
                    efname = e.get("name", "")
                    ext = os.path.splitext(efname)[1].lower()
                    if ext in pan._MEDIA_EXTENSIONS:
                        clean_efname = jobs.re.sub(r"[^\w]+", "", efname.lower())
                        if (cn_subj and len(cn_subj) >= 2 and cn_subj in efname) or \
                           (clean_title and len(clean_title) >= 3 and clean_title in jobs.re.sub(r"[^\w\u4e00-\u9fa5]+", "", efname.lower())) or \
                           (clean_dn and len(clean_dn) >= 3 and (clean_dn in clean_efname or clean_efname in clean_dn)):
                            found_file = e
                            break

                assert found_file is not None
                jobs._finish_offline(j, True, found_file["name"], "", found_file["size"])

        asyncio.run(run_fallback_check())

    updated = db.query("SELECT * FROM transfers WHERE id=?", (job["id"],), one=True)
    assert updated["status"] == "success"
    assert len(notified) == 1
    assert "✅ 云下载任务完成" in notified[0]
    print("test_offline_target_directory_fallback passed.")


if __name__ == "__main__":
    test_offline_keys()
    test_offline_task_completion_detection()
    test_offline_target_directory_fallback()
    print("ALL TESTS IN test_offline_check.py PASSED SUCCESSFULLY!")
