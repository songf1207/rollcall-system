"""pytest 公共装置。

注意：必须在 import app 之前把 DATABASE_URL 指向临时库，
因为 app.database 在 import 时就会按配置建 engine。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

_TMP_DB = Path(tempfile.mkdtemp(prefix="rollcall-test-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.as_posix()}"
os.environ["AUTO_CREATE_TABLES"] = "1"
os.environ["ALPHA"] = "1.0"
os.environ["TRANSFER_EVERY"] = "2"
os.environ["CORS_ORIGINS"] = "*"
os.environ["TEACHER_API_KEY"] = "test-teacher-key"
# 避免读到开发者本机的 .env，导致测试连到真实数据库
os.environ.setdefault("ROLLCALL_TESTING", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app, headers={"X-Teacher-Key": "test-teacher-key"}) as test_client:
        yield test_client


@pytest.fixture()
def clean_db(client):
    """每个接口用例开始前清空所有表，用例之间互不影响。"""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == "write_locks":
                continue
            conn.execute(table.delete())
    yield
