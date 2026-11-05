"""通过运行中的 HTTP 服务验收云数据库。会导入演示名单并留下两个演示场次，不清库。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
from pathlib import Path
import sys
from threading import Barrier
from zoneinfo import ZoneInfo

import httpx
from openpyxl import load_workbook
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    from app.config import settings
    from app.database import SessionLocal, engine
    from app.models import DrawCandidate, DrawLog, PointLog, Student
    from app.core.weights import verify_draw

    if not settings.database_url.startswith("mysql+pymysql://"):
        raise RuntimeError("此脚本用于在线 MySQL/TiDB 验收，当前本地配置未连接云库")
    checks = []

    def passed(message):
        checks.append(message)
        print("[OK]", message, flush=True)

    with engine.connect() as connection:
        assert connection.get_isolation_level() == "READ COMMITTED"
    passed("云库连接使用 READ COMMITTED，配合写锁刷新等待后的读取")

    with httpx.Client(base_url=args.base_url, timeout=60, trust_env=False, headers={"X-Teacher-Key": settings.teacher_api_key}) as client:
        request = client.build_request("GET", "/api/students")
        request.headers.pop("X-Teacher-Key", None)
        assert client.send(request).status_code == 401
        passed("未携带教师口令的业务请求被拒绝")
        def data(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}，{response.json().get('msg')}")
            response.raise_for_status()
            body = response.json()
            assert body["code"] == 0, body["msg"]
            return body["data"]

        assert data("GET", "/health")["database"] == "ok"
        passed("服务与数据库健康检查正常")
        roster = (ROOT / "samples/sample_roster.xlsx").read_bytes()
        imported = data("POST", "/api/students/import", files={"file": ("sample_roster.xlsx", roster)})
        assert imported["failed"] == []
        passed(f"Excel 演示名单导入：新增 {imported['inserted']}，更新 {imported['updated']}")
        repeated = data("POST", "/api/students/import", files={"file": ("sample_roster.xlsx", roster)})
        assert repeated["inserted"] == 0 and repeated["failed"] == []
        students = data("GET", "/api/students", params={"size": 200})
        passed(f"重复导入不增加学生；云端现有 {students['total']} 人")

        seq_session = data("POST", "/api/sessions", json={"mode": "sequential"})
        seq_call = data("POST", f"/api/sessions/{seq_session['id']}/draw")["rollcall"]
        assert seq_call["student"]["student_no"] == students["items"][0]["student_no"]
        seq_score = data("POST", f"/api/rollcalls/{seq_call['id']}/score", json={"arrived": True})
        assert seq_score["point_delta"] == 1
        data("POST", f"/api/sessions/{seq_session['id']}/close")
        passed("按学号顺序点名、到场加 1 分并关闭场次")

        session = data("POST", "/api/sessions", json={"mode": "random"})
        call = data("POST", f"/api/sessions/{session['id']}/draw")["rollcall"]
        before = call["student"]["total_points"]
        scored = data("POST", f"/api/rollcalls/{call['id']}/score", json={"arrived": True, "repeat_ok": True, "answer_score": 2})
        assert scored["point_delta"] == 3.5
        assert client.post(f"/api/rollcalls/{call['id']}/score", json={"arrived": True}).status_code == 409
        corrected = data("PATCH", f"/api/rollcalls/{call['id']}/score", json={"arrived": True, "repeat_ok": True, "answer_score": .5})
        assert corrected["point_delta"] == 2
        assert corrected["student"]["total_points"] == before + 2
        passed("加权随机点名、记分、拒绝重复记分、追加改判")

        ranking = data("GET", "/api/stats/ranking", params={"top": 100})
        assert ranking["calls_series"] == [s["random_call_count"] for s in ranking["items"]]
        probability = data("GET", "/api/stats/probability")
        assert next(s for s in probability["items"] if s["student_id"] == call["student_id"])["excluded"]
        assert abs(sum(s["probability"] for s in probability["items"]) - 1) < 1e-4
        curve = data("GET", f"/api/stats/students/{call['student_id']}/points")
        assert curve["points_series"][-1] == corrected["student"]["total_points"]
        assert curve["items"][-1]["reason"] == "manual"
        passed("排行榜随机次数、概率排除、个人积分曲线与改判流水一致")

        export = client.get("/api/export/points")
        export.raise_for_status()
        workbook = load_workbook(io.BytesIO(export.content), read_only=True)
        sheet = workbook.active
        assert next(sheet.values) == ("学号", "姓名", "专业", "随机点名次数", "总积分")
        assert sheet.max_row == students["total"] + 1
        row = next(row for row in sheet.iter_rows(min_row=2, values_only=True) if row[0] == call["student"]["student_no"])
        assert row[3] == corrected["student"]["random_call_count"] and row[4] == before + 2
        workbook.close()
        (ROOT / "samples/cloud_points_demo.xlsx").write_bytes(export.content)
        passed("从云库导出五列 Excel，人数、随机次数与积分一致")

        with SessionLocal() as db:
            persisted = db.get(Student, call["student_id"])
            assert persisted.total_points == before + 2
            ledger = db.scalar(select(func.sum(PointLog.delta)).where(PointLog.student_id == persisted.id))
            assert float(ledger) == persisted.total_points
            audit = db.scalar(select(DrawLog).where(DrawLog.session_id == session["id"]))
            snapshots = list(db.scalars(select(DrawCandidate).where(DrawCandidate.draw_id == audit.id)))
            assert verify_draw((c.student_id, c.weight, c.rand_u) for c in snapshots) == call["student_id"]
        passed("独立数据库连接核对持久化、积分账本及抽签候选复算")
        audit = data("GET", f"/api/rollcalls/{call['id']}/audit")
        assert audit["verified"] is True and audit["replayed_winner_id"] == call["student_id"]
        passed("带教师口令访问抽签复核接口，记录验证通过")
        pending = data("POST", f"/api/sessions/{session['id']}/draw")["rollcall"]
        barrier = Barrier(2)
        def score_together():
            barrier.wait(timeout=10)
            return client.post(f"/api/rollcalls/{pending['id']}/score", json={"arrived": True})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(score_together) for _ in range(2)]
            responses = [future.result(timeout=60) for future in futures]
        assert sorted(r.status_code for r in responses) == [200, 409], [r.status_code for r in responses]
        with SessionLocal() as db:
            stored = db.get(Student, pending["student_id"])
            ledger = db.scalar(select(func.sum(PointLog.delta)).where(PointLog.student_id == stored.id))
            assert stored.total_points == pending["student"]["total_points"] + 1 == float(ledger)
        passed("云端同时记分两次，仅一次成功，账本与学生积分一致")
        data("POST", f"/api/sessions/{session['id']}/close")

    captured_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    report = "# TiDB 云数据库联通与接口验收\n\n"
    report += f"验收时间：{captured_at}（北京时间）。\n\n"
    report += "数据库：TiDB Cloud Starter，MySQL Compatible，classroom_rollcall；含 write_locks 共七张表。\n\n"
    report += "接口运行于本机，连接云端数据库；此记录不代表后端已部署到公网。\n\n"
    report += "\n".join(f"- 通过：{message}" for message in checks) + "\n\n"
    report += f"演示场次：顺序 #{seq_session['id']}、随机 #{session['id']}，均已关闭；演示记录与积分保留。\n\n"
    report += "导出文件：samples/cloud_points_demo.xlsx。报告未记录数据库密码。\n"
    (ROOT / "docs/cloud-validation.md").write_text(report, encoding="utf-8")
    engine.dispose()
    print("验收完成，报告：docs/cloud-validation.md", flush=True)


if __name__ == "__main__":
    main()
