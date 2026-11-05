"""首次安装生成教师管理口令，保存至已忽略的本地文件；不输出口令。"""
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parent.parent


def main():
    env_path = ROOT / ".env"
    content = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else ""
    key = ""
    for line in content.splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "TEACHER_API_KEY":
            key = value.strip().strip('"').strip("'")
    if not key:
        key = secrets.token_urlsafe(32)
        lines = [line for line in content.splitlines() if line.partition("=")[0].strip() != "TEACHER_API_KEY"]
        lines.append("TEACHER_API_KEY=" + key)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / ".teacher-key.txt").write_text(key, encoding="utf-8")
    print("教师管理口令已保存至 .teacher-key.txt；重启后端后生效。请私下交给队长，不提交仓库。")


if __name__ == "__main__":
    main()
