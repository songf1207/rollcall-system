"""按白名单打包可交接源码，排除凭据、虚拟环境、数据库及临时文件。"""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import zipfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DIRECT_FILES = (
    ".gitignore", ".env.example", "tidb.env.example", "README.md", "requirements.txt",
    "constraints-tested.txt", "conftest.py", "start_backend.cmd",
)
DIRECTORIES = {
    "app": {".py"}, "tests": {".py"}, "scripts": {".py"},
    "sql": {".sql"}, "deploy": {".yaml", ".yml"},
    "samples": {".xlsx"}, "docs": {".md", ".txt", ".json", ".png"},
}


def main():
    entries = {}
    for name in DIRECT_FILES:
        entries[f"backend/{name}"] = (ROOT / name).read_bytes()
    for directory, suffixes in DIRECTORIES.items():
        for path in sorted((ROOT / directory).rglob("*")):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in suffixes:
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            entries[f"backend/{relative.as_posix()}"] = path.read_bytes()
    entries["先读我.md"] = (ROOT / "docs/handoff.md").read_bytes()
    entries["render.yaml"] = (ROOT / "deploy/render.yaml").read_bytes()

    secrets = []
    for name in (".tidb-password.txt", ".teacher-key.txt"):
        path = ROOT / name
        if path.exists():
            value = path.read_text(encoding="utf-8-sig").strip()
            if value:
                secrets.append(value.encode("utf-8"))
    for name, data in entries.items():
        if any(secret in data for secret in secrets):
            raise RuntimeError(f"发现本地口令出现在拟交付文件中，停止打包：{name}")
    manifest = "\n".join(f"{hashlib.sha256(data).hexdigest()}  {name}" for name, data in sorted(entries.items())) + "\n"
    entries["MANIFEST.sha256"] = manifest.encode("utf-8")

    output_dir = ROOT / "deliverables"
    output_dir.mkdir(exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    output = output_dir / f"classroom-rollcall-backend-{stamp}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(entries)
        for name, data in entries.items():
            assert hashlib.sha256(archive.read(name)).digest() == hashlib.sha256(data).digest()
        assert not any(name.endswith(("/.env", "/.teacher-key.txt", "/.tidb-password.txt", ".db", ".whl")) for name in archive.namelist())
    print("交接包：", output)
    print("文件数：", len(entries), "压缩大小：", output.stat().st_size, "字节")
    print("已验证 ZIP 完整性、逐文件摘要及本地口令排除。")


if __name__ == "__main__":
    main()
