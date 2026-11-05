"""生成演示用的名单文件。

    python scripts/make_sample_excel.py

会在 backend/samples/ 下生成两个文件：
- sample_roster.xlsx        干净名单（40 人），录视频时导入这个
- sample_roster_dirty.xlsx  故意做脏的名单，用来演示「脏数据报错」
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

BACKEND_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BACKEND_DIR / "samples"

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = ["子轩", "一诺", "思远", "雨桐", "昊然", "梦琪", "嘉懿", "若汐", "宇轩", "欣怡"]
MAJORS = ["软件工程", "计算机科学与技术", "人工智能", "数据科学与大数据技术", "网络空间安全"]
CLASSES = ["软工2301", "软工2302", "计科2301", "智能2302", "大数据2301", "网安2301"]


def _write(path: Path, rows: list[list], *, widths: dict[int, int] | None = None) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "学生名单"
    for row in rows:
        ws.append(row)

    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F5597")
        cell.alignment = Alignment(horizontal="center")
    for idx, width in (widths or {}).items():
        ws.column_dimensions[chr(64 + idx)].width = width
    ws.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"已生成 {path}（{len(rows) - 1} 行数据）")


def build_clean() -> list[list]:
    rows: list[list] = [["学号", "姓名", "专业", "班级"]]
    for i in range(1, 41):
        name = SURNAMES[i % len(SURNAMES)] + GIVEN[i % len(GIVEN)]
        rows.append(
            [
                f"2023{i:03d}",
                name,
                MAJORS[i % len(MAJORS)],
                CLASSES[i % len(CLASSES)],
            ]
        )
    return rows


def build_dirty() -> list[list]:
    return [
        ["2026 级软件工程专业 课堂点名名单"],  # 第 1 行：大标题
        ["学 号（必填）", "姓名*", "专业名称", "备注"],  # 第 2 行：花式表头
        ["2023001", "张三", "软件工程", "班长"],
        ["", "李四", "软件工程", "学号忘填了"],          # 学号为空
        ["2023003", "", "软件工程", "姓名忘填了"],        # 姓名为空
        ["2023001", "张三丰", "软件工程", "学号撞车了"],  # 学号重复
        [2023005, "王五", "计算机科学与技术", "学号是数字格式"],  # 数字型学号
        ["2023006", "赵六", "人工智能", ""],
    ]


def main() -> int:
    _write(
        SAMPLES_DIR / "sample_roster.xlsx",
        build_clean(),
        widths={1: 14, 2: 12, 3: 28, 4: 14},
    )
    _write(
        SAMPLES_DIR / "sample_roster_dirty.xlsx",
        build_dirty(),
        widths={1: 18, 2: 12, 3: 24, 4: 20},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
