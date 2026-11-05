"""业务边界、交错请求、真实并发和管理口令回归，全部使用临时库。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
from threading import Barrier

from openpyxl import Workbook
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal
from app.errors import ConflictError
from app.models import DrawCandidate, DrawLog, PointLog, RollCall, RollSession, Student
from app.services.points import get_rollcall, rejudge_rollcall, score_rollcall


@pytest.fixture
def roster(client, clean_db):
    with SessionLocal() as db:
        students = [Student(student_no=f"{i:03}", name=f"演示学生{i}") for i in range(1, 13)]
        db.add_all(students)
        db.commit()
        return [s.id for s in students]


def start(client, mode="random"):
    response = client.post("/api/sessions", json={"mode": mode})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def draw(client, session_id):
    response = client.post(f"/api/sessions/{session_id}/draw")
    assert response.status_code == 200, response.text
    return response.json()["data"]["rollcall"]


def totals(student_id):
    with SessionLocal() as db:
        student = db.get(Student, student_id)
        ledger = db.scalar(select(func.sum(PointLog.delta)).where(PointLog.student_id == student_id))
        return student.total_points, float(ledger or 0), student.arrive_count


def parallel(functions):
    barrier = Barrier(len(functions))
    def run(function):
        barrier.wait(timeout=10)
        return function()
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        futures = [pool.submit(run, function) for function in functions]
        return [future.result(timeout=30) for future in futures]


def test_stale_pending_record_cannot_score_twice(client, roster):
    call = draw(client, start(client))
    with SessionLocal() as first, SessionLocal() as second:
        a, b = get_rollcall(first, call["id"]), get_rollcall(second, call["id"])
        score_rollcall(first, a, arrived=True, repeat_ok=None, answer_score=None)
        with pytest.raises(ConflictError):
            score_rollcall(second, b, arrived=True, repeat_ok=None, answer_score=None)
    assert totals(call["student_id"]) == (1, 1, 1)


def test_simultaneous_http_scores_only_one_succeeds(client, roster):
    call = draw(client, start(client))
    path = f"/api/rollcalls/{call['id']}/score"
    responses = parallel([lambda: client.post(path, json={"arrived": True})] * 2)
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert totals(call["student_id"]) == (1, 1, 1)


def test_simultaneous_different_calls_preserve_shared_student_total(client, roster):
    a = draw(client, start(client, "sequential"))
    b = draw(client, start(client, "sequential"))
    assert a["student_id"] == b["student_id"]
    responses = parallel([
        lambda: client.post(f"/api/rollcalls/{a['id']}/score", json={"arrived": True}),
        lambda: client.post(f"/api/rollcalls/{b['id']}/score", json={"arrived": True}),
    ])
    assert all(r.status_code == 200 for r in responses)
    assert totals(a["student_id"]) == (2, 2, 2)


def test_parallel_draws_have_unique_students_and_sequence_numbers(client, roster):
    session_id = start(client)
    responses = parallel([lambda: client.post(f"/api/sessions/{session_id}/draw")] * 8)
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    calls = [r.json()["data"]["rollcall"] for r in responses]
    assert len({c["student_id"] for c in calls}) == 8
    assert sorted(c["seq"] for c in calls) == list(range(1, 9))
    with SessionLocal() as db:
        assert db.get(RollSession, session_id).draw_count == 8
        assert db.scalar(select(func.sum(Student.random_call_count))) == 8


def test_parallel_session_creation_leaves_only_one_open(client, roster):
    responses = parallel([lambda: client.post("/api/sessions", json={"mode": "random"})] * 2)
    assert all(r.status_code == 200 for r in responses)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RollSession).where(RollSession.status == "open")) == 1


def test_stale_rejudgements_keep_ledger_and_total_equal(client, roster):
    call = draw(client, start(client))
    client.post(f"/api/rollcalls/{call['id']}/score", json={"arrived": True})
    with SessionLocal() as first, SessionLocal() as second:
        a, b = get_rollcall(first, call["id"]), get_rollcall(second, call["id"])
        rejudge_rollcall(first, a, arrived=True, repeat_ok=None, answer_score=1)
        rejudge_rollcall(second, b, arrived=True, repeat_ok=None, answer_score=2)
    assert totals(call["student_id"]) == (3, 3, 1)


def test_simultaneous_score_and_transfer_cannot_both_succeed(client, roster):
    call = draw(client, start(client, "sequential"))
    with SessionLocal() as db:
        db.get(Student, call["student_id"]).transfer_credit = 1
        db.commit()
    responses = parallel([
        lambda: client.post(f"/api/rollcalls/{call['id']}/score", json={"arrived": True}),
        lambda: client.post(f"/api/rollcalls/{call['id']}/transfer", json={"to_student_id": roster[1]}),
    ])
    assert sorted(r.status_code for r in responses) == [200, 409]


@pytest.mark.parametrize("payload", [
    {"arrived": False, "repeat_ok": True},
    {"arrived": False, "repeat_ok": False},
    {"arrived": False, "answer_score": 3},
])
def test_absent_students_cannot_receive_question_scores(client, roster, payload):
    call = draw(client, start(client))
    response = client.post(f"/api/rollcalls/{call['id']}/score", json=payload)
    assert response.status_code == 400
    assert totals(call["student_id"]) == (0, 0, 0)
    assert client.get(f"/api/rollcalls/{call['id']}").json()["data"]["status"] == "pending"


@pytest.mark.parametrize("score", [.46, .49, .499])
def test_answer_minimum_checked_before_rounding(client, roster, score):
    call = draw(client, start(client))
    response = client.post(f"/api/rollcalls/{call['id']}/score", json={"arrived": True, "answer_score": score})
    assert response.status_code == 400
    assert totals(call["student_id"]) == (0, 0, 0)


def test_long_major_and_class_are_reported_without_blocking_valid_rows(client, clean_db):
    wb = Workbook()
    ws = wb.active
    ws.append(["学号", "姓名", "专业", "班级"])
    ws.append(["001", "甲", "软件工程", "一班"])
    ws.append(["002", "乙", "专" * 101, "二班"])
    ws.append(["003", "丙", "软件工程", "班" * 51])
    ws.append(["004", "丁", "专" * 100, "班" * 50])
    buf = io.BytesIO()
    wb.save(buf)
    response = client.post("/api/students/import", files={"file": ("roster.xlsx", buf.getvalue())})
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["inserted"] == 2
    assert [e["row"] for e in result["failed"]] == [3, 4]
    assert client.get("/api/students").json()["data"]["total"] == 2


def test_closed_session_cannot_transfer(client, roster):
    session_id = start(client, "sequential")
    call = draw(client, session_id)
    with SessionLocal() as db:
        db.get(Student, call["student_id"]).transfer_credit = 1
        db.commit()
    client.post(f"/api/sessions/{session_id}/close")
    response = client.post(f"/api/rollcalls/{call['id']}/transfer", json={"to_student_id": roster[1]})
    assert response.status_code == 409
    with SessionLocal() as db:
        assert db.get(Student, call["student_id"]).transfer_credit == 1
        assert db.get(RollCall, call["id"]).status == "pending"


def test_prior_round_cannot_transfer(client, roster):
    session_id = start(client, "sequential")
    first = draw(client, session_id)
    for _ in range(12):
        draw(client, session_id)
    response = client.post(f"/api/rollcalls/{first['id']}/transfer", json={"to_student_id": roster[1]})
    assert response.status_code == 409
    assert "当前轮次" in response.json()["msg"]


def test_disabled_transfer_rejects_existing_credit(client, roster, monkeypatch):
    from app.services import points
    call = draw(client, start(client, "sequential"))
    with SessionLocal() as db:
        db.get(Student, call["student_id"]).transfer_credit = 1
        db.commit()
    monkeypatch.setattr(points, "settings", replace(points.settings, transfer_every=0))
    response = client.post(f"/api/rollcalls/{call['id']}/transfer", json={"to_student_id": roster[1]})
    assert response.status_code == 409


@pytest.mark.parametrize("path", ["/api/students", "/api/stats/ranking", "/api/stats/history", "/api/stats/probability", "/api/stats/config", "/api/export/points"])
def test_missing_teacher_key_rejected_for_read_endpoints(client, clean_db, path):
    request = client.build_request("GET", path)
    request.headers.pop("X-Teacher-Key")
    assert client.send(request).status_code == 401


def test_teacher_key_protects_writes_and_missing_configuration_fails_closed(client, clean_db, monkeypatch):
    from app import auth
    assert client.post("/api/sessions", json={"mode": "random"}, headers={"X-Teacher-Key": "wrong"}).status_code == 401
    monkeypatch.setattr(auth, "settings", replace(auth.settings, teacher_api_key=""))
    assert client.get("/api/students").status_code == 503


def test_openapi_exposes_teacher_key_authorization(client, clean_db):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["components"]["securitySchemes"]["TeacherKey"]["name"] == "X-Teacher-Key"
    assert schema["paths"]["/api/sessions"]["post"]["security"] == [{"TeacherKey": []}]
    assert client.get("/docs").status_code == 200


def test_database_failure_returns_503_health(client, clean_db, monkeypatch):
    from app import main
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, RuntimeError("offline"))
        def close(self):
            pass
    monkeypatch.setattr(main, "SessionLocal", BrokenSession)
    response = client.get("/health")
    assert response.status_code == response.json()["code"] == 503
    assert response.json()["data"]["database"] == "error"


def test_audit_replays_original_draw_and_detects_modified_key(client, roster):
    call = draw(client, start(client))
    path = f"/api/rollcalls/{call['id']}/audit"
    data = client.get(path).json()["data"]
    assert data["verified"] is True
    assert data["winner_id"] == data["replayed_winner_id"] == call["student_id"]
    assert len(data["candidates"]) == 12
    assert sum(c["probability"] for c in data["candidates"]) == pytest.approx(1)
    assert all(c["key_matches"] for c in data["candidates"])
    with SessionLocal() as db:
        candidate = db.scalar(select(DrawCandidate).where(DrawCandidate.draw_id == data["draw_id"]))
        candidate.sort_key += 1
        db.commit()
    altered = client.get(path).json()["data"]
    assert altered["status"] == "mismatch" and altered["verified"] is False


def test_sequential_audit_does_not_claim_random_verification(client, roster):
    call = draw(client, start(client, "sequential"))
    data = client.get(f"/api/rollcalls/{call['id']}/audit").json()["data"]
    assert data["verified"] is None and data["status"] == "not_applicable"
    assert data["candidates"] == []


def test_precision_limited_snapshot_is_not_reported_as_exact_verification(client, roster):
    call = draw(client, start(client))
    path = f"/api/rollcalls/{call['id']}/audit"
    original = client.get(path).json()["data"]
    with SessionLocal() as db:
        snapshot = db.scalar(select(DrawCandidate).where(DrawCandidate.draw_id == original["draw_id"]))
        snapshot.sort_key += 1e-7
        db.commit()
    data = client.get(path).json()["data"]
    assert data["status"] == "precision_limited"
    assert data["verified"] is None
    assert data["replayed_winner_id"] == call["student_id"]
