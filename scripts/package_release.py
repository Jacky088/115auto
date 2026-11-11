"""打包发版脚本：自动打包 115atuosave.zip

严格排除：
- data/ (运行时数据库，绝不能打入包中覆盖生产环境)
- .git/
- .venv/
- web/node_modules/
- web/dist/
- __pycache__/
- .pytest_cache/
- .screenshots/
- tests/ (可选)
- *.pyc, *.log, *.bak, *.db, *.zip
"""
import os
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ZIP = Path(r"C:\Users\littl\Desktop\115atuosave.zip")

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".screenshots",
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
    ".bak",
    ".log",
    ".tmp",
    ".zip",
}

EXCLUDE_FILES = {
    ".env",
    ".DS_Store",
    "Thumbs.db",
}


def should_exclude(rel_path: Path) -> bool:
    parts = rel_path.parts
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
    if rel_path.name in EXCLUDE_FILES:
        return True
    if rel_path.suffix.lower() in EXCLUDE_EXTS:
        return True
    return False


def build_zip():
    print(f"正在从 {ROOT_DIR} 打包到 {OUTPUT_ZIP}...")
    if OUTPUT_ZIP.exists():
        try:
            OUTPUT_ZIP.unlink()
        except Exception as e:
            print(f"删除旧 zip 失败: {e}")

    file_count = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ROOT_DIR):
            # 剪枝目录以加快遍历并防止进入大目录
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                abs_file = Path(root) / file
                rel_file = abs_file.relative_to(ROOT_DIR)
                if should_exclude(rel_file):
                    continue
                zf.write(abs_file, str(rel_file).replace("\\", "/"))
                file_count += 1

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"打包完成！共写入 {file_count} 个文件，文件大小: {size_mb:.2f} MB")

    # 严格校验：确保没有任何 data/ 目录或 .db 文件进入 zip
    with zipfile.ZipFile(OUTPUT_ZIP, "r") as zf:
        names = zf.namelist()
        forbidden = [n for n in names if n.startswith("data/") or n.endswith(".db") or "node_modules" in n]
        if forbidden:
            raise RuntimeError(f"打包严重错误，发现了禁止打包的文件: {forbidden}")
        print("校验通过：未包含任何 data/、.db 或 node_modules 文件！")


if __name__ == "__main__":
    build_zip()
