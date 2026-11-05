"""Excel 导入解析 / 导出生成的单元测试。"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook, load_workbook

from app.errors import AppError
from app.services.excel_io import build_points_workbook, cell_text, match_field, parse_workbook


def make_xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------
# 表头模糊匹配
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("学号", "student_no"),
        ("学生学号", "student_no"),
        (" 学 号 ", "student_no"),
        ("学号（必填）", "student_no"),
        ("学号*", "student_no"),
        ("Student No", "student_no"),
        ("姓名", "name"),
        ("学生姓名", "name"),
        ("名字", "name"),
        ("专业名称", "major"),
        ("专业", "major"),
        ("班级", "class_name"),
    ],
)
def test_header_fuzzy_match(header, field):
    matched, score = match_field(header)
    assert matched == field
    assert score > 0


def test_unknown_header_is_ignored():
    assert match_field("备注") == (None, 0)


def test_title_row_above_header_is_skipped():
    """有些名单第一行是大标题，表头在第二行。"""
    data = make_xlsx(
        [
            ["2026 级软件工程专业学生名单"],
            ["学号", "姓名", "专业", "班级"],
            ["2023001", "张三", "软件工程", "软工2301"],
        ]
    )
    parsed = parse_workbook(data)
    assert parsed.header_row == 2
    assert parsed.columns["student_no"] == 0
    assert len(parsed.rows) == 1


def test_missing_required_column_raises():
    data = make_xlsx([["姓名", "专业"], ["张三", "软件工程"]])
    with pytest.raises(AppError) as exc:
        parse_workbook(data)
    assert "学号" in str(exc.value)


def test_extra_columns_are_tolerated():
    data = make_xlsx(
        [
            ["学号", "姓名", "专业", "备注", "手机号"],
            ["2023001", "张三", "软件工程", "班长", "13800000000"],
        ]
    )
    parsed = parse_workbook(data)
    assert parsed.rows[0]["name"] == "张三"
    assert set(parsed.columns) == {"student_no", "name", "major"}


# ----------------------------------------------------------------------
# 脏数据
# ----------------------------------------------------------------------
def test_dirty_rows_reported_with_excel_row_numbers():
    data = make_xlsx(
        [
            ["学号", "姓名", "专业"],
            ["2023001", "张三", "软件工程"],   # 第 2 行：正常
            ["", "李四", "软件工程"],           # 第 3 行：学号为空
            ["2023003", "", "软件工程"],        # 第 4 行：姓名为空
            ["2023001", "张三丰", "软件工程"],  # 第 5 行：学号重复
            ["2023005", "王五", "软件工程"],   # 第 6 行：正常
        ]
    )
    parsed = parse_workbook(data)

    assert [e.row for e in parsed.errors] == [3, 4, 5]
    assert "学号为空" == parsed.errors[0].reason
    assert "姓名为空" == parsed.errors[1].reason
    assert "第 2 行" in parsed.errors[2].reason
    # 脏数据不阻塞好数据：两行正常数据照样入库
    assert [r["student_no"] for r in parsed.rows] == ["2023001", "2023005"]


def test_blank_rows_are_skipped():
    data = make_xlsx(
        [
            ["学号", "姓名"],
            ["2023001", "张三"],
            [None, None],
            ["2023002", "李四"],
        ]
    )
    parsed = parse_workbook(data)
    assert len(parsed.rows) == 2
    assert parsed.errors == []
    assert parsed.raw_count == 2


def test_numeric_student_no_keeps_text_form():
    data = make_xlsx([["学号", "姓名"], [2023001, "张三"]])
    parsed = parse_workbook(data)
    assert parsed.rows[0]["student_no"] == "2023001"


def test_cell_text_normalizes_float_and_none():
    assert cell_text(2023001.0) == "2023001"
    assert cell_text(None) == ""
    assert cell_text("  张三  ") == "张三"


def test_non_excel_bytes_raise_friendly_error():
    with pytest.raises(AppError) as exc:
        parse_workbook(b"not an excel file at all")
    assert "xlsx" in str(exc.value)


# ----------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------
def test_export_headers_are_exactly_five_columns():
    content = build_points_workbook(
        [
            {
                "student_no": "2023001",
                "name": "张三",
                "major": "软件工程",
                "random_call_count": 3,
                "total_points": 4.5,
            }
        ]
    )
    ws = load_workbook(io.BytesIO(content)).active

    assert tuple(cell.value for cell in ws[1]) == ("学号", "姓名", "专业", "随机点名次数", "总积分")
    assert ws.max_column == 5
    assert [cell.value for cell in ws[2]] == ["2023001", "张三", "软件工程", 3, 4.5]
