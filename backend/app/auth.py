"""教师管理口令：通过请求头传输，不放进 URL、源码或响应。"""
import secrets

from fastapi import Security
from fastapi.security import APIKeyHeader

from .config import settings
from .errors import AppError

teacher_key = APIKeyHeader(name="X-Teacher-Key", scheme_name="TeacherKey", auto_error=False,
                          description="教师管理口令；由后端负责人私下提供，在此粘贴口令本身")


def require_teacher(key: str | None = Security(teacher_key)):
    # 课堂演示模式：OPEN_ACCESS=1 时完全放开，打开网址即可使用（不校验口令）
    if settings.open_access:
        return
    if not settings.teacher_api_key:
        raise AppError("教师管理口令尚未配置，请联系管理员", status_code=503)
    if not key or not secrets.compare_digest(key.encode("utf-8"), settings.teacher_api_key.encode("utf-8")):
        raise AppError("教师管理口令缺失或错误", status_code=401)
