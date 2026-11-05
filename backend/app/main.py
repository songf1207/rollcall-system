"""FastAPI 应用入口。

启动：uvicorn app.main:app --reload
文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from . import models  # noqa: F401  确保 create_all 能注册到全部表
from .api import classes as classes_api
from .api import export as export_api
from .api import rollcalls, sessions, stats, students
from .config import settings
from .database import Base, SessionLocal, engine
from .errors import AppError
from .schemas import HealthOut, Resp
from .auth import require_teacher

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 本地开发/首次部署时自动建表；已用 schema.sql 建好表时这一步是空操作
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="课堂随机点名系统 API",
    version="1.0.0",
    description=(
        "2026 软工 K 班结对编程 · 后端子系统\n\n"
        "统一响应格式：`{\"code\": 0, \"msg\": \"ok\", \"data\": ...}`，code=0 表示成功。\n\n"
        "核心算法：加权随机，一轮不重复。非负分 w=(1+p)^(-α)，负分 w=(1-p)^α。"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def handle_app_error(_request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "msg": exc.message, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request, exc: RequestValidationError):
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    msg = f"参数不合法：{loc} {first.get('msg', '')}".strip()
    return JSONResponse(status_code=422, content={"code": 422, "msg": msg, "data": None})


@app.exception_handler(IntegrityError)
async def handle_integrity_error(_request, _exc: IntegrityError):
    # 例如绕过接口重复点名、学号重复；数据库约束是最后一道防线
    return JSONResponse(
        status_code=409,
        content={"code": 409, "msg": "数据冲突：可能是本轮重复点名或学号重复", "data": None},
    )


@app.exception_handler(OperationalError)
async def handle_database_unavailable(_request, exc: OperationalError):
    code = getattr(exc.orig, "args", (None,))[0]
    busy = code in (1205, 1213) or "database is locked" in str(exc.orig).lower()
    status = 409 if busy else 503
    message = "操作冲突，请刷新后重试" if busy else "数据库暂时不可用，请稍后重试"
    logger.error("数据库操作异常：%s，错误码=%s", type(exc.orig).__name__, code if isinstance(code, int) else "unknown")
    return JSONResponse(status_code=status, content={"code": status, "msg": message, "data": None})


@app.exception_handler(SQLAlchemyError)
async def handle_database_error(_request, _exc: SQLAlchemyError):
    logger.error("数据库操作失败：%s", type(_exc).__name__)
    return JSONResponse(status_code=500, content={"code": 500, "msg": "数据操作失败，请联系管理员", "data": None})


for router in (students.router, classes_api.router, sessions.router, rollcalls.router, stats.router, export_api.router):
    app.include_router(router, dependencies=[Depends(require_teacher)])


@app.get("/", include_in_schema=False)
def root():
    # 有打包好的前端就由下面挂载的静态目录接管；没有（纯后端开发）时跳文档
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=Resp[HealthOut], tags=["运维"], summary="健康检查（Render 探活用）")
def health():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        count = int(db.scalar(text("SELECT COUNT(*) FROM students")) or 0)
        return Resp[HealthOut](data=HealthOut(status="ok", database="ok", students=count))
    except SQLAlchemyError:
        result = Resp[HealthOut](code=503, msg="数据库暂时不可用", data=HealthOut(status="degraded", database="error", students=0))
        return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
    finally:
        db.close()


# ----------------------------------------------------------------------
# 前端静态文件托管（单服务部署）
# 把 npm run build 产出的 dist 放到 app/static/，FastAPI 就直接把界面也发了，
# 前后端同源：不用配 CORS，只部署一个服务就够。
# 目录不存在时（纯后端开发）自动跳过。
# ----------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="web")
    logger.info("已挂载前端静态目录：%s", STATIC_DIR)
