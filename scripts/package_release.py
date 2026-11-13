"""打包发版脚本：自动打包 115atuosave.zip

严格排除：
- data/ (运行时数据库，绝不能打入包中覆盖生产环境)
- .git/ / .github/ / .venv/ / web/node_modules/ / web/dist/
- __pycache__/ / .pytest_cache/ / .screenshots/ / scratch/
- *.pyc, *.log, *.bak, *.db, *.sqlite*, *.session*, *.key, *.zip, .env*

发版前置校验：
- 确认 backend/static/index.html 与静态资源已存在（保障 Docker 无 Node 阶段构建完整）
- 确认 docker-entrypoint.sh 为 LF 换行（防止 Linux 容器启动报 no such file or directory）
- 确认 backend/config.py 与 web/package.json 版本号一致
"""
import json
import os
import re
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ZIP = Path(r"C:\Users\littl\Desktop\115atuosave.zip")

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".screenshots",
    ".tmp_backup",
    "data",
    "scratch",
    "dist",
}

EXCLUDE_EXTS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".session",
    ".session-journal",
    ".key",
    ".pem",
    ".bak",
    ".log",
    ".tmp",
    ".zip",
    ".swp",
}

EXCLUDE_FILES = {
    ".env",
    ".DS_Store",
    "Thumbs.db",
    "115_cookie.txt",
    "secret.key",
    "initial_password.txt",
    "DATABASE_RECOVERY_NOTICE.txt",
    "方案.md",
}


def should_exclude(rel_path: Path) -> bool:
    for p in rel_path.parts:
        if p in EXCLUDE_DIRS:
            return True
    name = rel_path.name
    if name in EXCLUDE_FILES or name.startswith(".env."):
        return True
    if rel_path.suffix.lower() in EXCLUDE_EXTS:
        return True
    return False


def preflight_check() -> str:
    """发版前预检：静态构建产物、容器入口脚本换行、前后端版本号对齐。"""
    static_index = ROOT_DIR / "backend" / "static" / "index.html"
    assets_dir = ROOT_DIR / "backend" / "static" / "assets"
    if not static_index.is_file() or not assets_dir.is_dir() or not any(assets_dir.glob("*.js")):
        raise RuntimeError("缺少前端预构建产物 backend/static/，请先执行 `npm --prefix web run build`")

    entrypoint = ROOT_DIR / "docker-entrypoint.sh"
    if not entrypoint.is_file():
        raise RuntimeError("缺少 docker-entrypoint.sh")
    if b"\r\n" in entrypoint.read_bytes():
        raise RuntimeError("docker-entrypoint.sh 包含 CRLF，请转换为 LF 换行，否则 Linux Docker 容器无法执行")

    cfg_text = (ROOT_DIR / "backend" / "config.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', cfg_text, re.MULTILINE)
    app_ver = m.group(1) if m else ""
    pkg_ver = json.loads((ROOT_DIR / "web" / "package.json").read_text(encoding="utf-8")).get("version", "")
    if not app_ver or app_ver != pkg_ver:
        raise RuntimeError(f"前后端版本号不一致: backend/config.py={app_ver!r}, web/package.json={pkg_ver!r}")
    return app_ver


def build_zip():
    ver = preflight_check()
    print(f"预检通过（版本 v{ver}），正在从 {ROOT_DIR} 打包到 {OUTPUT_ZIP}...")
    if OUTPUT_ZIP.exists():
        try:
            OUTPUT_ZIP.unlink()
        except Exception as e:
            print(f"删除旧 zip 失败: {e}")

    file_count = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
            for file in sorted(files):
                abs_file = Path(root) / file
                rel_file = abs_file.relative_to(ROOT_DIR)
                if should_exclude(rel_file):
                    continue
                zf.write(abs_file, str(rel_file).replace("\\", "/"))
                file_count += 1

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"打包完成！共写入 {file_count} 个文件，文件大小: {size_mb:.2f} MB")

    # 严格校验：黑名单阻断 + 核心容器/静态运行文件完备性 + LF 校验
    with zipfile.ZipFile(OUTPUT_ZIP, "r") as zf:
        names = zf.namelist()
        name_set = set(names)
        forbidden = [
            n for n in names
            if (
                n.startswith("data/")
                or n.startswith(".screenshots/")
                or n.startswith(".git/")
                or n.startswith(".venv/")
                or "node_modules/" in n
                or "__pycache__/" in n
                or n.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".session", ".key", ".pem", ".log"))
                or Path(n).name in EXCLUDE_FILES
                or Path(n).name.startswith(".env")
            )
        ]
        if forbidden:
            raise RuntimeError(f"打包严重错误，发现了禁止打包的文件: {forbidden}")

        required = [
            "Dockerfile",
            "docker-compose.yml",
            "docker-entrypoint.sh",
            "backend/main.py",
            "backend/requirements.lock",
            "backend/static/index.html",
        ]
        missing = [r for r in required if r not in name_set]
        if missing or not any(n.startswith("backend/static/assets/") and n.endswith(".js") for n in names):
            raise RuntimeError(f"发版包缺少核心运行文件: {missing}")

        if b"\r\n" in zf.read("docker-entrypoint.sh"):
            raise RuntimeError("zip 内 docker-entrypoint.sh 含 CRLF！")

        # 所有 shell 脚本必须 LF（Linux 容器执行 CRLF 脚本会报 no such file or directory）
        crlf_sh = [n for n in names if n.endswith(".sh") and b"\r\n" in zf.read(n)]
        if crlf_sh:
            raise RuntimeError(f"zip 内以下 shell 脚本含 CRLF: {crlf_sh}")

        # index.html 引用的静态资产必须全部在包内（防止打包出残缺前端导致白屏）
        index_html = zf.read("backend/static/index.html").decode("utf-8")
        refs = re.findall(r'(?:src|href)="/?(assets/[^"\']+\.(?:js|css|png|svg|ico|woff2?))"',
                          index_html)
        missing_assets = [r for r in refs
                          if f"backend/static/{r.lstrip('/')}" not in name_set]
        if missing_assets:
            raise RuntimeError(f"index.html 引用的资产缺失（构建产物不完整）: {missing_assets}")

        # 包内所有 Python 文件必须能通过语法编译（发版包里不能有半成品代码）
        syntax_errors = []
        for n in names:
            if n.startswith("backend/") and n.endswith(".py"):
                try:
                    compile(zf.read(n).decode("utf-8"), n, "exec")
                except SyntaxError as e:
                    syntax_errors.append(f"{n}: {e}")
        if syntax_errors:
            raise RuntimeError(f"包内 Python 文件语法错误: {syntax_errors}")

        print("校验通过：无任何 data/ 凭据与垃圾文件，容器运行文件与预构建前端完整！")
        print(f"校验通过：全部 {len(crlf_sh)}+1 个 .sh 为 LF；index.html 引用的 {len(refs)} 个资产齐全；"
              "包内 Python 全部可编译")


if __name__ == "__main__":
    build_zip()
