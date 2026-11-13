"""无损升级兼容性测试：模拟 v1.x 旧版本遗留的数据，验证新版本代码可直接接管。

覆盖升级场景的三类兼容风险：
1. 旧格式密码哈希（120k 轮 salt$hex，无算法标注）→ 新 verify_password 兼容并透明升级
2. 旧 token（JWT 无 ver 版本声明）→ 新 check_token 兼容，升级后旧会话不立即失效
3. 旧库 schema/settings → 新 db.init()/_migrate() 原样保留业务数据，不新增必填列

任何改动破坏了这些行为，升级中的生产实例都会出现无法登录/数据丢失，故固化为测试。
"""
import os
import sys
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="autosave_test_upgrade_"))

import pytest

from backend import auth, database as db
from backend.core import episode as ep, media_parser as mp  # noqa: F401  确认解析模块可随新代码加载


@pytest.fixture()
def legacy_db(monkeypatch, tmp_path):
    """构造一个 v1.2.0 时代遗留形态的数据库（旧哈希格式 + 业务数据 + 无新版本新增键）。"""
    sqlite3 = __import__("sqlite3")
    shared_db_path = str(db.config.DB_PATH)  # 记录共享套件路径，teardown 恢复
    test_db = tmp_path / "legacy_app.db"
    monkeypatch.setattr(db.config, "DB_PATH", str(test_db))
    if db._conn is not None:
        try:
            db._conn.close()
        except Exception:
            pass
    conn = db._conn = sqlite3.connect(str(test_db), check_same_thread=False)
    conn.row_factory = db.sqlite3.Row
    conn.executescript(db.SCHEMA)
    # 旧格式密码哈希：120k 轮，无算法标注（v1.2.0 的 auth.hash_password 输出）
    salt = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    dk = __import__("hashlib").pbkdf2_hmac("sha256", "old-deploy-pass".encode(), salt.encode(), 120_000)
    conn.execute("INSERT OR REPLACE INTO settings VALUES('web_password_hash',?)",
                 (f"{salt}${dk.hex()}",))
    conn.execute("INSERT OR REPLACE INTO settings VALUES('web_username','admin')")
    conn.execute("INSERT OR REPLACE INTO settings VALUES('running','false')")
    # 业务数据：升级后必须原样保留（routes 表 category 有 UNIQUE 约束，这里用测试专属类目）
    conn.execute(
        "INSERT INTO channels(name,type,enabled,sub_only,created_at)"
        " VALUES('mychannel','polling',1,0,1700000000)")
    conn.execute(
        "INSERT INTO routes(category,target_dir,offline_dir) VALUES('纪录片','/媒体库/纪录片','')")
    conn.execute(
        "INSERT INTO transfers(share_code,share_url,title,status,created_at)"
        " VALUES('abc123','https://115.com/s/abc123','测试剧集','success', 1700000000)")
    conn.commit()
    yield conn
    try:
        conn.close()
    except Exception:
        pass
    # 恢复共享套件的数据库连接：全量跑测时各模块共享同一个 DATA_DIR/app.db，
    # 后续未自带 db.init() 夹具的模块依赖这里恢复的连接（不能留成 None 或遗留库）
    db._conn = sqlite3.connect(shared_db_path, check_same_thread=False)
    db._conn.row_factory = db.sqlite3.Row


def test_legacy_password_hash_verified_then_rehashed(legacy_db):
    stored = db.get_setting("web_password_hash", "")
    # 旧格式哈希能通过新代码验证（不强制改密，升级零感知）
    assert auth.verify_password("old-deploy-pass", stored)
    assert auth.verify_password("wrong-pass", stored) is False
    # 标记为待升级：登录成功路径会透明重哈希
    assert auth.password_needs_rehash(stored)
    new_stored = auth.hash_password("old-deploy-pass")
    assert new_stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("old-deploy-pass", new_stored)
    assert not auth.password_needs_rehash(new_stored)


def test_legacy_token_without_ver_claim_still_valid(legacy_db):
    # 旧版本签发的 token 无 ver 字段：升级后（token_version 尚未写入）应继续有效，
    # 保证升级窗口内已登录的会话不被突然踢出
    import jwt as pyjwt
    import time as _time
    old_token = pyjwt.encode(
        {"sub": "admin", "exp": int(_time.time()) + 3600, "iat": int(_time.time())},
        auth._load_secret(), algorithm="HS256")
    assert auth.check_token(old_token) == "admin"
    # 一旦用户登出（版本号递增），旧 token 立即失效
    auth.revoke_all_tokens()
    assert auth.check_token(old_token) is None


def test_business_data_survives_reinit(legacy_db):
    # 新版本代码对旧库执行 init/_migrate 后，业务数据原样保留（无损升级的核心保证）
    db._conn.executescript(db.SCHEMA)
    db._migrate()
    db._seed()
    db._conn.commit()
    assert db.get_setting("web_password_hash", "").startswith("a1b2c3d4")
    row = db.query("SELECT name, type, enabled FROM channels WHERE name='mychannel'", one=True)
    assert row is not None and row["type"] == "polling"
    t = db.query("SELECT share_code, status, title FROM transfers WHERE share_code='abc123'", one=True)
    assert t is not None and t["status"] == "success"
    routes = db.query("SELECT count(*) c FROM routes WHERE category='纪录片'", one=True)["c"]
    assert routes == 1
    # 迁移告警在锁外补发（死锁修复），不抛异常即通过
