"""接口级集成测试：走完整流程（导入 -> 点名 -> 记分 -> 改判 -> 统计 -> 导出）。"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook, load_workbook

from app.database import SessionLocal
from app.models import PointLog, Student

HEADER = ["学号", "姓名", "专业", "班级"]
ROSTER = [
    ["2023001", "张三", "软件工程", "软工2301"],
    ["2023002", "李四", "软件工程", "软工2301"],
    ["2023003", "王五", "计算机科学与技术", "计科2301"],
    ["2023004", "赵六", "人工智能", "智能2302"],
    ["2023005", "钱七", "软件工程", "软工2302"],
]


def make_xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_roster(client, rows: list[list] | None = None):
    data = make_xlsx([HEADER] + (ROSTER if rows is None else rows))
    return client.post(
        "/api/students/import",
        files={
            "file": (
                "roster.xlsx",
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def new_session(client, mode: str = "random") -> int:
    return client.post("/api/sessions", json={"mode": mode}).json()["data"]["id"]


def draw(client, session_id: int) -> dict:
    response = client.post(f"/api/sessions/{session_id}/draw")
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ----------------------------------------------------------------------
# 导入
# ----------------------------------------------------------------------
def test_import_is_idempotent(client, clean_db):
    first = import_roster(client).json()["data"]
    assert first["inserted"] == 5
    assert first["updated"] == 0
    assert first["failed"] == []
    assert first["header_map"]["student_no"] == "学号"

    second = import_roster(client).json()["data"]
    assert second["inserted"] == 0
    assert second["updated"] == 5

    page = client.get("/api/students", params={"size": 50}).json()["data"]
    assert page["total"] == 5
    assert page["items"][0]["student_no"] == "2023001"


def test_import_rejects_wrong_extension(client, clean_db):
    response = client.post(
        "/api/students/import",
        files={"file": ("roster.csv", b"a,b", "text/csv")},
    )
    assert response.status_code == 400
    assert "xlsx" in response.json()["msg"]


def test_reimport_keeps_points(client, clean_db):
    """重复导入名单不能把已有积分清零 —— 这是最容易被踩的坑。"""
    import_roster(client)
    session_id = new_session(client)
    rollcall = draw(client, session_id)["rollcall"]
    client.post(
        f"/api/rollcalls/{rollcall['id']}/score",
        json={"arrived": True, "repeat_ok": True, "answer_score": 1},
    )

    import_roster(client)
    items = client.get("/api/students", params={"size": 50}).json()["data"]["items"]
    scored = [s for s in items if s["total_points"] == 2.5]
    assert len(scored) == 1


# ----------------------------------------------------------------------
# 点名 + 记分 + 改判
# ----------------------------------------------------------------------
def test_full_scoring_flow(client, clean_db):
    import_roster(client)
    session_id = new_session(client)
    data = draw(client, session_id)

    rollcall = data["rollcall"]
    assert rollcall["student"] is not None
    assert rollcall["status"] == "pending"
    assert data["progress"] == {
        "round_no": 1,
        "called_in_round": 1,
        "remaining_in_round": 4,
        "total_students": 5,
        "is_new_round": False,
    }

    scored = client.post(
        f"/api/rollcalls/{rollcall['id']}/score",
        json={"arrived": True, "repeat_ok": True, "answer_score": 2},
    ).json()["data"]
    assert scored["point_delta"] == 3.5  # +1 到达 +0.5 复述 +2 回答
    assert scored["status"] == "scored"

    # 同一次点名不能重复记分
    duplicate = client.post(f"/api/rollcalls/{rollcall['id']}/score", json={"arrived": True})
    assert duplicate.status_code == 409

    # 改判：回答问题从 2 分改成 0.5 分 -> 1 + 0.5 + 0.5 = 2.0
    rejudged = client.patch(
        f"/api/rollcalls/{rollcall['id']}/score",
        json={"arrived": True, "repeat_ok": True, "answer_score": 0.5},
    ).json()["data"]
    assert rejudged["point_delta"] == 2.0

    ranking = client.get("/api/stats/ranking", params={"top": 10}).json()["data"]
    assert ranking["items"][0]["total_points"] == 2.0
    assert len(ranking["categories"]) == 5
    assert len(ranking["points_series"]) == len(ranking["calls_series"]) == 5


def test_repeat_wrong_costs_one_point(client, clean_db):
    import_roster(client)
    session_id = new_session(client)
    rollcall = draw(client, session_id)["rollcall"]

    scored = client.post(
        f"/api/rollcalls/{rollcall['id']}/score",
        json={"arrived": True, "repeat_ok": False, "answer_score": None},
    ).json()["data"]
    assert scored["point_delta"] == 0.0  # +1 -1


def test_answer_score_out_of_range_rejected(client, clean_db):
    import_roster(client)
    session_id = new_session(client)
    rollcall = draw(client, session_id)["rollcall"]

    response = client.post(
        f"/api/rollcalls/{rollcall['id']}/score",
        json={"arrived": True, "repeat_ok": True, "answer_score": 0.3},
    )
    assert response.status_code == 400
    assert "0.5" in response.json()["msg"]


# ----------------------------------------------------------------------
# 一轮不重复
# ----------------------------------------------------------------------
def test_round_does_not_repeat(client, clean_db):
    import_roster(client)
    session_id = new_session(client)

    seen = []
    for _ in range(5):
        data = draw(client, session_id)
        assert data["progress"]["is_new_round"] is False
        seen.append(data["rollcall"]["student"]["student_no"])
    assert len(set(seen)) == 5

    sixth = draw(client, session_id)
    assert sixth["progress"]["is_new_round"] is True
    assert sixth["progress"]["round_no"] == 2
    assert sixth["progress"]["called_in_round"] == 1


# ----------------------------------------------------------------------
# 顺序模式
# ----------------------------------------------------------------------
def test_sequential_mode_follows_student_no(client, clean_db):
    import_roster(client)
    session_id = new_session(client, mode="sequential")

    order = [draw(client, session_id)["rollcall"]["student"]["student_no"] for _ in range(5)]
    assert order == [row[0] for row in ROSTER]

    sixth = draw(client, session_id)
    assert sixth["progress"]["round_no"] == 2
    assert sixth["rollcall"]["student"]["student_no"] == order[0]


# ----------------------------------------------------------------------
# 转移权
# ----------------------------------------------------------------------
def test_transfer_requires_credit(client, clean_db):
    import_roster(client)
    session_id = new_session(client)
    rollcall = draw(client, session_id)["rollcall"]
    others = client.get("/api/students", params={"size": 50}).json()["data"]["items"]
    target_id = next(s["id"] for s in others if s["id"] != rollcall["student_id"])

    response = client.post(
        f"/api/rollcalls/{rollcall['id']}/transfer", json={"to_student_id": target_id}
    )
    assert response.status_code == 409
    assert "转移权" in response.json()["msg"]


def test_transfer_moves_rollcall_to_another_student(client, clean_db):
    import_roster(client)
    session_id = new_session(client)
    rollcall = draw(client, session_id)["rollcall"]

    # 直接给这位同学补 1 次转移权，专注测试转移逻辑本身
    with SessionLocal() as db:
        student = db.get(Student, rollcall["student_id"])
        student.transfer_credit += 1
        db.commit()

    others = client.get("/api/students", params={"size": 50}).json()["data"]["items"]
    target = next(s for s in others if s["id"] != rollcall["student_id"])

    moved = client.post(
        f"/api/rollcalls/{rollcall['id']}/transfer", json={"to_student_id": target["id"]}
    )
    assert moved.status_code == 200, moved.text
    new_rollcall = moved.json()["data"]
    assert new_rollcall["student_id"] == target["id"]
    assert new_rollcall["transferred_from"] == rollcall["student_id"]
    assert new_rollcall["status"] == "pending"

    old = client.get(f"/api/rollcalls/{rollcall['id']}").json()["data"]
    assert old["status"] == "transferred"

    # 转移过来的这次点名可以正常记分
    scored = client.post(
        f"/api/rollcalls/{new_rollcall['id']}/score", json={"arrived": True, "repeat_ok": True}
    ).json()["data"]
    assert scored["point_delta"] == 1.5


# ----------------------------------------------------------------------
# 概率盘 / 历史 / 导出
# ----------------------------------------------------------------------
def test_probability_excludes_called_students(client, clean_db):
    import_roster(client)
    session_id = new_session(client)
    called = draw(client, session_id)["rollcall"]["student_id"]

    data = client.get("/api/stats/probability").json()["data"]
    assert data["alpha"] == 1.0
    excluded = [item for item in data["items"] if item["excluded"]]
    assert [item["student_id"] for item in excluded] == [called]
    for item in data["items"]:
        expected = 0.0 if item["student_id"] == called else 0.25
        assert item["probability"] == pytest.approx(expected, abs=1e-6)


def test_history_lists_rollcalls(client, clean_db):
    import_roster(client)
    session_id = new_session(client)
    draw(client, session_id)
    draw(client, session_id)

    data = client.get("/api/stats/history").json()["data"]
    assert data["total"] == 2
    assert data["items"][0]["seq"] == 2  # 按时间倒序
    assert data["items"][0]["name"]


def test_ranking_chart_uses_random_counts(client, clean_db):
    import_roster(client)
    for mode in ("sequential", "random"):
        rc = draw(client, new_session(client, mode))["rollcall"]
        response = client.post(f"/api/rollcalls/{rc['id']}/score", json={"arrived": True})
        assert response.status_code == 200
    data = client.get("/api/stats/ranking").json()["data"]
    assert data["calls_series"] == [s["random_call_count"] for s in data["items"]]
    assert sum(data["calls_series"]) == 1
    assert sum(s["call_count"] for s in data["items"]) == 2


def test_draw_counts_immediately_and_scoring_does_not_count_again(client, clean_db):
    import_roster(client)
    rc = draw(client, new_session(client))["rollcall"]
    assert rc["student"]["call_count"] == rc["student"]["random_call_count"] == 1
    ranked = client.get("/api/stats/ranking").json()["data"]
    assert sum(ranked["calls_series"]) == 1
    result = client.post(f"/api/rollcalls/{rc['id']}/score", json={"arrived": False}).json()["data"]
    assert result["student"]["call_count"] == result["student"]["random_call_count"] == 1
    client.patch(f"/api/rollcalls/{rc['id']}/score", json={"arrived": True})
    ws = load_workbook(io.BytesIO(client.get("/api/export/points").content)).active
    assert sum(row[3] for row in ws.iter_rows(min_row=2, values_only=True)) == 1


def test_transfer_target_is_not_counted_as_random_winner(client, clean_db):
    import_roster(client)
    rc = draw(client, new_session(client))["rollcall"]
    with SessionLocal() as db:
        db.get(Student, rc["student_id"]).transfer_credit = 1
        db.commit()
    target = next(s for s in client.get("/api/students").json()["data"]["items"] if s["id"] != rc["student_id"])
    moved = client.post(f"/api/rollcalls/{rc['id']}/transfer", json={"to_student_id": target["id"]}).json()["data"]
    assert moved["student"]["call_count"] == 1
    assert moved["student"]["random_call_count"] == 0
    client.post(f"/api/rollcalls/{moved['id']}/score", json={"arrived": True})
    rows = client.get("/api/students").json()["data"]["items"]
    assert sum(s["random_call_count"] for s in rows) == 1
    assert sum(s["call_count"] for s in rows) == 2


@pytest.mark.parametrize("mode", ["random", "sequential"])
def test_probability_previews_new_round_without_mutating_session(client, clean_db, mode):
    import_roster(client)
    session_id = new_session(client, mode)
    for _ in ROSTER:
        draw(client, session_id)
    for _ in range(2):
        preview = client.get("/api/stats/probability").json()["data"]
        assert preview["round_no"] == 2
        assert preview["is_new_round"] is True
        assert not any(item["excluded"] for item in preview["items"])
        assert sum(item["probability"] for item in preview["items"]) == pytest.approx(1)
    session = client.get(f"/api/sessions/{session_id}").json()["data"]
    assert session["round_no"] == 1
    result = draw(client, session_id)
    assert result["progress"]["round_no"] == preview["round_no"]
    if mode == "sequential":
        winner = next(s for s in preview["items"] if s["probability"] == 1)
        assert winner["student_id"] == result["rollcall"]["student_id"]


def test_probability_sequential_skips_transferred_student(client, clean_db):
    import_roster(client)
    session_id = new_session(client, "sequential")
    first = draw(client, session_id)["rollcall"]
    students = client.get("/api/students").json()["data"]["items"]
    with SessionLocal() as db:
        db.get(Student, first["student_id"]).transfer_credit = 1
        db.commit()
    moved = client.post(f"/api/rollcalls/{first['id']}/transfer", json={"to_student_id": students[1]["id"]})
    assert moved.status_code == 200
    for expected in students[2:]:
        preview = client.get("/api/stats/probability").json()["data"]
        winner = next(s for s in preview["items"] if s["probability"] == 1)
        assert winner["student_id"] == expected["id"]
        assert draw(client, session_id)["rollcall"]["student_id"] == expected["id"]
    assert draw(client, session_id)["rollcall"]["student_id"] == first["student_id"]


def test_probability_missing_and_closed_sessions_rejected(client, clean_db):
    assert client.get("/api/stats/probability", params={"session_id": 99999}).status_code == 404
    session_id = new_session(client)
    client.post(f"/api/sessions/{session_id}/close")
    assert client.get("/api/stats/probability", params={"session_id": session_id}).status_code == 409


def test_negative_points_change_actual_probability(client, clean_db):
    import_roster(client, ROSTER[:2])
    session_id = new_session(client)
    rc = draw(client, session_id)["rollcall"]
    # 负分可来自历史数据或不同积分配置，不能靠“缺席仍复述”的矛盾输入构造。
    with SessionLocal() as db:
        db.get(Student, rc["student_id"]).total_points = -1
        db.add(PointLog(student_id=rc["student_id"], delta=-1, reason="manual", note="历史负分测试数据"))
        db.commit()
    new_session(client)
    items = client.get("/api/stats/probability").json()["data"]["items"]
    negative = next(s for s in items if s["student_id"] == rc["student_id"])
    assert negative["probability"] == pytest.approx(2 / 3, abs=1e-6)


def test_student_points_replays_corrections_and_preserves_old_logs(client, clean_db):
    import_roster(client)
    rc = draw(client, new_session(client))["rollcall"]
    path = f"/api/stats/students/{rc['student_id']}/points"
    empty = client.get(path).json()["data"]
    assert empty["points_series"] == [0]
    assert empty["items"] == []
    client.post(f"/api/rollcalls/{rc['id']}/score", json={"arrived": True, "repeat_ok": True, "answer_score": 2})
    before = client.get(path).json()["data"]
    assert before["points_series"] == [0, 1, 1.5, 3.5]
    client.patch(f"/api/rollcalls/{rc['id']}/score", json={"arrived": True, "repeat_ok": False, "answer_score": 0})
    after = client.get(path).json()["data"]
    assert after["items"][:-1] == before["items"]
    assert after["items"][-1]["reason"] == "manual"
    assert after["items"][-1]["delta"] == -3.5
    assert after["points_series"][-1] == after["student"]["total_points"] == 0
    assert len(after["categories"]) == len(after["points_series"])
    assert client.get("/api/stats/students/99999/points").status_code == 404


def test_export_returns_xlsx_with_five_columns(client, clean_db):
    import_roster(client)
    response = client.get("/api/export/points")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]

    ws = load_workbook(io.BytesIO(response.content)).active
    assert tuple(cell.value for cell in ws[1]) == ("学号", "姓名", "专业", "随机点名次数", "总积分")
    assert ws.max_row == 6  # 表头 + 5 名学生
    assert ws.max_column == 5


def test_draw_without_students_rejected(client, clean_db):
    session_id = new_session(client)
    response = client.post(f"/api/sessions/{session_id}/draw")
    assert response.status_code == 400
    assert "名单为空" in response.json()["msg"]


def test_health(client, clean_db):
    data = client.get("/health").json()["data"]
    assert data["status"] == "ok"
    assert data["students"] == 0


def test_rules_config_endpoint(client, clean_db):
    """前端靠这个接口渲染「积分规则说明」，改了 .env 页面能自动跟着变。"""
    data = client.get("/api/stats/config").json()["data"]
    assert data["alpha"] == 1.0
    assert data["point_arrive"] == 1.0
    assert data["point_repeat_fail"] == -1.0
    assert data["answer_min"] == 0.5
    assert data["answer_max"] == 3.0
