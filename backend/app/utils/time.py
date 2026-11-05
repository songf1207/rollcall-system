"""统一的「现在几点」。

为什么要单独抽出来：Render 的免费容器默认是 UTC，
直接用 datetime.now() 会让点名记录的时间比北京时间差 8 小时，
投到教室大屏上很明显。这里统一按配置时区取本地时间，
再以 naive datetime 存库（MySQL DATETIME 本身不带时区）。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import settings


def now() -> datetime:
    try:
        tz = ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):  # 配置写错也不该让接口 500
        tz = ZoneInfo("Asia/Shanghai")
    return datetime.now(tz).replace(tzinfo=None)
