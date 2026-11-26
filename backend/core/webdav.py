"""WebDAV 远端存储客户端：支持连接校验/探针测试/递归建目录/流式上传/旧备份轮转/远端拉取。"""
from __future__ import annotations

import datetime
import email.utils
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

from .. import database as db

logger = logging.getLogger("webdav")

_DEFAULT_ROOT = "/115AutoSave_Backup"
_PROBE_FILE = ".autosave_probe"


def get_webdav_config() -> dict[str, Any]:
    """读取已保存的 WebDAV 配置项（密码脱敏）。"""
    url = str(db.get_setting("webdav_url", "") or "").strip()
    user = str(db.get_setting("webdav_username", "") or "").strip()
    raw_pwd = str(db.get_setting("webdav_password", "") or "")
    root_dir = str(db.get_setting("webdav_root_dir", _DEFAULT_ROOT) or _DEFAULT_ROOT).strip()
    keep_count = int(db.get_setting("webdav_keep_count", 10) or 10)
    auto_backup = bool(db.get_setting("webdav_auto_backup_enabled", False))
    last_test_ok = bool(db.get_setting("webdav_last_test_ok", False))
    last_test_at = float(db.get_setting("webdav_last_test_at", 0) or 0)
    last_test_ms = int(db.get_setting("webdav_last_test_ms", 0) or 0)
    last_backup_at = float(db.get_setting("webdav_last_backup_at", 0) or 0)
    last_backup_status = str(db.get_setting("webdav_last_backup_status", "") or "")

    # 历史脏数据自愈纠偏：若时间戳异常（如小于 2000 年的 946684800 秒），优先从状态文件名还原真实时间
    if last_backup_at < 946684800:
        m = re.search(r"(\d{8})_(\d{6})", last_backup_status)
        if m:
            try:
                dt = datetime.datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S")
                last_backup_at = dt.timestamp()
                db.set_setting("webdav_last_backup_at", last_backup_at)
            except Exception:
                last_backup_at = 0.0
        else:
            last_backup_at = 0.0

    return {
        "enabled": bool(url and user),
        "url": url,
        "username": user,
        "password_set": bool(raw_pwd),
        "root_dir": root_dir if root_dir.startswith("/") else f"/{root_dir}",
        "keep_count": max(1, min(100, keep_count)),
        "auto_backup_enabled": auto_backup,
        "last_test_ok": last_test_ok,
        "last_test_at": last_test_at,
        "last_test_ms": last_test_ms,
        "last_backup_at": last_backup_at,
        "last_backup_status": last_backup_status,
    }


def is_configured() -> bool:
    """检查 WebDAV 是否已完整配置（具有服务地址、用户名及密码）。"""
    cfg = get_webdav_config()
    return bool(cfg.get("url") and cfg.get("username") and cfg.get("password_set"))


def save_webdav_config(
    url: str,
    username: str,
    password: str | None,
    root_dir: str = _DEFAULT_ROOT,
    keep_count: int = 10,
    auto_backup_enabled: bool = False,
) -> dict[str, Any]:
    """保存 WebDAV 配置（密码留空时保留原有密码）。"""
    clean_url = str(url or "").strip().rstrip("/")
    clean_user = str(username or "").strip()
    clean_root = str(root_dir or _DEFAULT_ROOT).strip()
    if not clean_root.startswith("/"):
        clean_root = f"/{clean_root}"
    clean_root = clean_root.rstrip("/")
    if not clean_root:
        clean_root = _DEFAULT_ROOT

    db.set_setting("webdav_url", clean_url)
    db.set_setting("webdav_username", clean_user)
    if password is not None and password != "" and password != "******":
        db.set_setting("webdav_password", str(password))
    db.set_setting("webdav_root_dir", clean_root)
    db.set_setting("webdav_keep_count", max(1, min(100, int(keep_count or 10))))
    db.set_setting("webdav_auto_backup_enabled", bool(auto_backup_enabled))
    return get_webdav_config()


def clear_webdav_config() -> dict[str, Any]:
    """清除 WebDAV 配置与凭据。"""
    db.set_setting("webdav_url", "")
    db.set_setting("webdav_username", "")
    db.set_setting("webdav_password", "")
    db.set_setting("webdav_root_dir", _DEFAULT_ROOT)
    db.set_setting("webdav_keep_count", 10)
    db.set_setting("webdav_auto_backup_enabled", False)
    db.set_setting("webdav_last_test_ok", False)
    db.set_setting("webdav_tested_url", "")
    db.set_setting("webdav_tested_user", "")
    db.set_setting("webdav_last_backup_status", "")
    return get_webdav_config()


def _parse_mtime(mtime_str: str, fname: str) -> float:
    """将 WebDAV 返回的 HTTP/ISO 时间或文件名时间戳解析为标准 Unix 秒级时间戳。"""
    if mtime_str:
        try:
            return email.utils.parsedate_to_datetime(mtime_str).timestamp()
        except Exception:
            pass
        try:
            dt = datetime.datetime.fromisoformat(mtime_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            pass
    # 从文件名提取如 20261121_220639 作为兜底
    m = re.search(r"(\d{8})_(\d{6})", fname)
    if m:
        try:
            dt = datetime.datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S")
            return dt.timestamp()
        except Exception:
            pass
    return 0.0


class WebDAVClient:
    """轻量 WebDAV 客户端：基于 httpx 实现标准 RFC 4918 操作。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        root_dir: str = _DEFAULT_ROOT,
        timeout: float = 30.0,
    ):
        raw_url = str(base_url or "").strip()
        if not raw_url.startswith(("http://", "https://")):
            raw_url = f"https://{raw_url}"
        self.base_url = raw_url.rstrip("/")
        self.username = str(username or "").strip()
        self.password = str(password or "")
        clean_root = str(root_dir or _DEFAULT_ROOT).strip()
        if not clean_root.startswith("/"):
            clean_root = f"/{clean_root}"
        self.root_dir = clean_root.rstrip("/") or _DEFAULT_ROOT
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(
            auth=(self.username, self.password),
            timeout=self.timeout,
            follow_redirects=False,
            verify=True,
        )

    def _build_url(self, path: str = "") -> str:
        """安全拼接 URL，保留 base_url 的已有 path 前缀（如 /dav），并过滤越界路径段。"""
        p = str(path or "").strip()
        if not p.startswith("/"):
            p = f"/{p}"
        # 兼容 base_url 尾部已有路径（如 https://nas.local/dav）
        parsed = urllib.parse.urlparse(self.base_url)
        combined_path = f"{parsed.path.rstrip('/')}{p}"
        raw_segs = [seg for seg in combined_path.split("/") if seg and seg != "."]
        if any(seg == ".." for seg in raw_segs):
            raise ValueError("WebDAV 路径不允许包含 '..' 段")
        parts = [urllib.parse.quote(seg) for seg in raw_segs]
        escaped_path = "/" + "/".join(parts)
        if p.endswith("/") and not escaped_path.endswith("/"):
            escaped_path += "/"
        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            escaped_path,
            "",
            "",
            "",
        ))

    def ensure_dir(self, dir_path: str) -> bool:
        """逐级确保远端目录存在（通过 MKCOL）。"""
        clean = dir_path.strip().strip("/")
        if not clean:
            return True
        segments = clean.split("/")
        cur = ""
        with self._client() as client:
            for seg in segments:
                cur += f"/{seg}"
                url = self._build_url(cur) + "/"
                # 先用 PROPFIND 探测是否存在
                try:
                    r = client.request("PROPFIND", url, headers={"Depth": "0"})
                    if r.status_code in (200, 207):
                        continue
                except Exception:
                    pass
                # 不存在则尝试创建
                try:
                    mk_res = client.request("MKCOL", url)
                    if mk_res.status_code not in (201, 200, 207, 405):  # 405 Method Not Allowed 通常说明已存在
                        logger.debug("MKCOL %s 响应码: %d", url, mk_res.status_code)
                except Exception as e:
                    logger.debug("MKCOL 创建目录异常: %s", e)
        return True

    def test_connection(self) -> dict[str, Any]:
        """连通性与读写删探针全流程测试。"""
        t0 = time.monotonic()
        with self._client() as client:
            # 1. 基础探测（OPTIONS 或 PROPFIND 根路径）
            try:
                base_check = client.request("OPTIONS", self._build_url("/"))
                if base_check.status_code == 401:
                    return {"ok": False, "message": "认证失败（401 Unauthorized）：请检查用户名与应用密码"}
                if base_check.status_code >= 400 and base_check.status_code not in (405,):
                    # 某些服务不支持 OPTIONS，尝试 PROPFIND
                    pf = client.request("PROPFIND", self._build_url("/"), headers={"Depth": "0"})
                    if pf.status_code == 401:
                        return {"ok": False, "message": "认证失败（401 Unauthorized）：请检查用户名与应用密码"}
                    if pf.status_code >= 400:
                        return {"ok": False, "message": f"连接异常（HTTP {pf.status_code}）：请检查服务器地址是否正确"}
            except httpx.ConnectError:
                return {"ok": False, "message": "服务器无法连接：请检查 WebDAV 地址与端口是否正确"}
            except httpx.TimeoutException:
                return {"ok": False, "message": "连接超时（>30s）：请检查网络连通性或代理配置"}
            except Exception as e:
                return {"ok": False, "message": f"连接错误: {str(e)[:150]}"}

            # 2. 检查或自动创建备份子目录
            try:
                self.ensure_dir(self.root_dir)
            except Exception as e:
                return {"ok": False, "message": f"备份目录创建失败: {str(e)[:150]}"}

            # 3. 探针文件写入测试（PUT）
            probe_path = f"{self.root_dir}/{_PROBE_FILE}"
            probe_url = self._build_url(probe_path)
            probe_data = f"115autosave_probe_{int(time.time())}".encode("utf-8")
            try:
                put_res = client.put(probe_url, content=probe_data, headers={"Content-Type": "text/plain"})
                if put_res.status_code not in (200, 201, 204):
                    return {"ok": False, "message": f"写入测试失败（HTTP {put_res.status_code}）：目录可能为只读或权限不足"}
            except Exception as e:
                return {"ok": False, "message": f"写入测试异常: {str(e)[:150]}"}

            # 4. 探针文件清理（DELETE）
            try:
                del_res = client.delete(probe_url)
                if del_res.status_code not in (200, 204, 404):
                    logger.debug("删除探针文件状态码: %d", del_res.status_code)
            except Exception as e:
                logger.debug("清理探针文件失败（不影响结论）: %s", e)

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        return {
            "ok": True,
            "message": f"连接与读写权限测试成功（耗时 {elapsed_ms} ms）",
            "elapsed_ms": elapsed_ms,
            "latency_ms": elapsed_ms,
        }

    def upload_file(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        """将本地文件以流式 PUT 上传到目标目录。"""
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"本地待上传文件不存在: {local_path}")
        self.ensure_dir(self.root_dir)
        target_url = self._build_url(f"{self.root_dir}/{remote_filename}")
        file_size = local_path.stat().st_size
        t0 = time.monotonic()
        with self._client() as client:
            with open(local_path, "rb") as fp:
                resp = client.put(
                    target_url,
                    content=fp,
                    headers={
                        "Content-Type": "application/zip",
                        "Content-Length": str(file_size),
                    },
                )
            if resp.status_code not in (200, 201, 204):
                raise RuntimeError(f"WebDAV 上传失败（HTTP {resp.status_code}）: {resp.text[:200]}")
        elapsed = round(time.monotonic() - t0, 2)
        return {
            "ok": True,
            "filename": remote_filename,
            "bytes": file_size,
            "elapsed_sec": elapsed,
        }

    def download_file(self, remote_filename: str, local_dest: Path) -> Path:
        """从 WebDAV 下载指定的备份文件。"""
        clean_name = str(remote_filename or "").strip()
        if not clean_name or "/" in clean_name or "\\" in clean_name or ".." in clean_name:
            raise ValueError("非法的文件名")
        target_url = self._build_url(f"{self.root_dir}/{clean_name}")
        local_dest.parent.mkdir(parents=True, exist_ok=True)
        with self._client() as client:
            with client.stream("GET", target_url) as resp:
                if resp.status_code == 404:
                    raise FileNotFoundError(f"远端备份文件不存在: {clean_name}")
                if resp.status_code >= 400:
                    raise RuntimeError(f"WebDAV 下载失败（HTTP {resp.status_code}）")
                with open(local_dest, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
        return local_dest

    def list_backups(self) -> list[dict[str, Any]]:
        """获取备份目录下的全部 .zip 迁移包文件，按修改时间倒序排列。

        连接失败、认证失败、返回内容无法解析都必须抛出异常（由接口层转成明确提示），
        否则「连不上/密码失效」会被静默伪装成「远端目录没有备份」，用户无从排查。
        仅 404（目录尚未创建，首次使用）按空列表处理。
        """
        target_url = self._build_url(f"{self.root_dir}/")
        try:
            with self._client() as client:
                resp = client.request("PROPFIND", target_url, headers={"Depth": "1"})
        except Exception as e:
            logger.error("WebDAV 列取目录失败: %s", e)
            raise RuntimeError(f"连接 WebDAV 失败：{str(e)[:150]}") from e

        if resp.status_code == 404:
            return []
        if resp.status_code == 401:
            raise RuntimeError("WebDAV 认证失败（401）：请重新填写应用密码并点击「测试连接」")
        if resp.status_code not in (200, 207):
            raise RuntimeError(f"列出远端备份失败（HTTP {resp.status_code}）")

        # 解析 XML 多状态响应（限制 10MB 防止畸形超大响应引发 OOM）
        if len(resp.content) > 10 * 1024 * 1024:
            raise RuntimeError("远端目录返回 XML 体积过大（>10 MB），已中止解析")
        items: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(resp.content)
        except Exception as e:
            logger.error("解析 WebDAV PROPFIND XML 失败: %s", e)
            raise RuntimeError("远端备份目录返回内容无法解析（对方可能不是标准 WebDAV 服务）") from e

        for resp_node in root.iter():
            if not resp_node.tag.lower().endswith("response"):
                continue
            href = ""
            size = 0
            mtime = ""
            is_dir = False
            for elem in resp_node.iter():
                tag = elem.tag.split("}")[-1].lower()
                if tag == "href":
                    href = elem.text or ""
                elif tag == "getcontentlength":
                    try:
                        size = int(elem.text or 0)
                    except ValueError:
                        size = 0
                elif tag == "getlastmodified":
                    mtime = elem.text or ""
                elif tag == "collection":
                    is_dir = True

            unquoted = urllib.parse.unquote(href).rstrip("/")
            fname = unquoted.split("/")[-1]
            # 仅保留 autosave_ 开头的 zip 备份文件
            if href and not is_dir and fname.lower().endswith(".zip") and fname.startswith("autosave_"):
                parsed_ts = _parse_mtime(mtime, fname)
                kind = "auto" if fname.startswith("autosave_auto_") else "migration"
                items.append({
                    "name": fname,
                    "size": size,
                    "mtime": parsed_ts,
                    "href": href,
                    "kind": kind,
                })

        # 按时间戳倒序，时间戳相同按文件名倒序
        items.sort(key=lambda x: (float(x.get("mtime") or 0), x.get("name") or ""), reverse=True)
        return items

    def delete_file(self, filename: str) -> bool:
        """删除远端指定的备份文件。"""
        clean_name = str(filename or "").strip()
        if not clean_name or "/" in clean_name or "\\" in clean_name or ".." in clean_name:
            raise ValueError("非法的文件名")
        del_url = self._build_url(f"{self.root_dir}/{clean_name}")
        with self._client() as client:
            r = client.delete(del_url)
            if r.status_code in (200, 204):
                logger.info("已成功删除 WebDAV 远端备份: %s", clean_name)
                return True
            if r.status_code == 404:
                return True
            raise RuntimeError(f"删除失败（HTTP {r.status_code}）")

    def prune_backups(self, keep_count: int = 10) -> int:
        """自动轮转：超出保留上限的旧备份文件自动删除。

        轮转属于「上传成功后」的收尾动作：即便列取失败也不能让已经成功的备份被判为失败，
        因此这里吞掉异常并返回 0（真实原因由上传链路日志体现）。
        """
        if keep_count <= 0:
            return 0
        try:
            backups = self.list_backups()
        except Exception as e:
            logger.warning("自动轮转跳过（列取远端备份失败）: %s", e)
            return 0
        if len(backups) <= keep_count:
            return 0
        to_delete = backups[keep_count:]
        deleted = 0
        with self._client() as client:
            for it in to_delete:
                fn = it["name"]
                del_url = self._build_url(f"{self.root_dir}/{fn}")
                try:
                    r = client.delete(del_url)
                    if r.status_code in (200, 204, 404):
                        deleted += 1
                        logger.info("已自动清理 WebDAV 超期旧备份: %s", fn)
                except Exception as e:
                    logger.warning("清理 WebDAV 旧备份失败 (%s): %s", fn, e)
        return deleted


def create_configured_client() -> WebDAVClient | None:
    """根据已保存的有效配置构建 WebDAVClient，未配置返回 None。"""
    cfg = get_webdav_config()
    if not cfg["url"] or not cfg["username"]:
        return None
    raw_pwd = str(db.get_setting("webdav_password", "") or "")
    if not raw_pwd:
        return None
    return WebDAVClient(
        base_url=cfg["url"],
        username=cfg["username"],
        password=raw_pwd,
        root_dir=cfg["root_dir"],
    )
