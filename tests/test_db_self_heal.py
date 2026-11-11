"""测试 SQLite 损坏自动容灾自愈机制"""
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from backend import config, database


def test_db_self_healing_on_malformed_disk(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    test_db = Path(temp_dir) / "app.db"
    monkeypatch.setattr(config, "DB_PATH", str(test_db))

    # 1. 制造损坏文件（写入非法非 SQLite 数据）
    with open(test_db, "wb") as f:
        f.write(b"NOT_A_VALID_SQLITE_DATABASE_CORRUPTED_BYTES_1234567890")

    # 同时创建假的 wal 和 shm 文件
    wal_file = Path(temp_dir) / "app.db-wal"
    wal_file.write_bytes(b"corrupted wal")

    # 2. 调用 database.init()，应该触发自愈且不报错
    database.init()

    # 3. 验证数据库已正常可用且已自动执行 SCHEMA 与 seed
    assert database._conn is not None
    res = database.query("SELECT * FROM routes")
    assert len(res) >= 4

    # 4. 验证备份文件已生成
    bak_files = list(Path(temp_dir).glob("*.bak"))
    assert len(bak_files) >= 1

    # 5. 验证通知文件已生成
    notice_file = Path(temp_dir) / "DATABASE_RECOVERY_NOTICE.txt"
    assert notice_file.exists()
    content = notice_file.read_text(encoding="utf-8")
    assert "数据库损坏自动容灾自愈触发" in content

    # 清理
    database._conn.close()
    database._conn = None
    shutil.rmtree(temp_dir, ignore_errors=True)
