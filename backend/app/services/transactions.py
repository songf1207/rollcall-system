"""单班级写事务串行化；MySQL/TiDB 连接配合 READ COMMITTED 避免等待后的旧快照。"""
from functools import wraps

from sqlalchemy import update

from ..errors import AppError
from ..models import WriteLock


def serialized_write(function):
    @wraps(function)
    def wrapped(db, *args, **kwargs):
        if db.new or db.dirty or db.deleted:
            raise RuntimeError("业务写操作必须从没有待保存更改的 Session 开始")
        # 路由可能已经读取实体。结束旧快照，避免等待锁后仍读到 pending 等旧状态。
        db.rollback()
        try:
            result = db.execute(
                update(WriteLock).where(WriteLock.id == 1)
                .values(revision=WriteLock.revision + 1)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise AppError("数据库写锁尚未初始化，请先初始化数据库", status_code=503)
            db.expire_all()
            value = function(db, *args, **kwargs)
            db.commit()
            return value
        except Exception:
            db.rollback()
            raise
    return wrapped
