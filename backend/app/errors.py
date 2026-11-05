"""业务异常。

统一在 main.py 注册一个 handler，任何业务错误都以
{"code": 400, "msg": "第 7 行学号重复", "data": null} 返回，
前端只要看 code/msg，不用猜 HTTP 状态码的含义。
"""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(message, status_code=404)


class ConflictError(AppError):
    def __init__(self, message: str = "当前状态不允许该操作") -> None:
        super().__init__(message, status_code=409)
