"""Excel 导入解析 / 导出生成。

导入要解决两件事：

1. **表头模糊匹配**：老师给的名单，表头五花八门——「学号」「学生学号」「学 号」
   「学号（必填）」「学号*」。写死列号一定会翻车，所以走「归一化 + 同义词 + 打分」：
   先去掉空格/星号/冒号/全角符号并转小写，再和同义词表比对，精确命中 3 分、
   包含命中 2 分，按分数从高到低把列分配给字段。
2. **脏数据定位到行**：「第 7 行学号为空」比「导入失败」有用得多。
   校验不过的行只记错误，不阻塞其余行入库。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..errors import AppError

# 字段 -> 同义词（第一条是展示用中文名）
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "student_no": ("学号", "学生学号", "学籍号", "学生编号", "编号", "student_no", "studentno", "sno", "no", "id"),
    "name": ("姓名", "名字", "学生姓名", "学生名字", "name", "studentname", "student_name"),
    "major": ("专业", "专业名称", "专业方向", "major", "speciality", "specialty"),
    "class_name": ("班级", "班号", "班级名称", "行政班", "class", "classname", "class_name"),
}

FIELD_LABELS = {k: v[0] for k, v in FIELD_SYNONYMS.items()}
REQUIRED_FIELDS = ("student_no", "name")

EXPORT_HEADERS = ("学号", "姓名", "专业", "随机点名次数", "总积分")

MAX_DATA_ROWS = 5000
HEADER_SCAN_ROWS = 10


@dataclass
class RowError:
    row: int
    reason: str


@dataclass
class ParsedSheet:
    header_row: int
    columns: dict[str, int]
    header_map: dict[str, str]
    rows: list[dict] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    raw_count: int = 0


def normalize_header(text) -> str:
    """归一化表头：去空白/星号/冒号/点号，全角括号转半角，转小写。"""
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = s.replace("\u3000", " ")
    s = re.sub(r"[\s\*＊·•.．:：,，、]+", "", s)
    s = s.replace("（", "(").replace("）", ")").replace("【", "[").replace("】", "]")
    return s


def match_field(header) -> tuple[str | None, int]:
    """把单个表头文字匹配到字段，返回 (字段名, 分数)。"""
    norm = normalize_header(header)
    if not norm:
        return None, 0
    best_field, best_score = None, 0
    for fld, synonyms in FIELD_SYNONYMS.items():
        for syn in synonyms:
            ns = normalize_header(syn)
            if not ns:
                continue
            if norm == ns:
                score = 3
            elif len(norm) >= 2 and (ns in norm or norm in ns):
                score = 2
            else:
                continue
            if score > best_score:
                best_field, best_score = fld, score
    return best_field, best_score


def detect_columns(row: tuple) -> tuple[dict[str, int], dict[str, str]]:
    """在一行里认出各字段对应的列号。"""
    scored: list[tuple[int, int, str]] = []
    for idx, cell in enumerate(row or ()):
        fld, score = match_field(cell)
        if fld and score > 0:
            scored.append((score, idx, fld))
    scored.sort(key=lambda item: (-item[0], item[1]))

    columns: dict[str, int] = {}
    header_map: dict[str, str] = {}
    used_cols: set[int] = set()
    for _score, idx, fld in scored:
        if fld in columns or idx in used_cols:
            continue
        columns[fld] = idx
        used_cols.add(idx)
        header_map[fld] = str(row[idx]).strip()
    return columns, header_map


def cell_text(value) -> str:
    """单元格 -> 干净字符串。数字型学号 2023001.0 要变成 '2023001'。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _pick_header(rows: list[tuple]) -> tuple[int, dict[str, int], dict[str, str]]:
    """在前 10 行里挑最像表头的一行。"""
    best = (-1, -1, {}, {})
    for i, row in enumerate(rows):
        columns, labels = detect_columns(row)
        score = len(columns) + (2 if "student_no" in columns else 0)
        if score > best[0]:
            best = (score, i, columns, labels)
    _score, idx, columns, labels = best
    if not columns or "student_no" not in columns:
        raise AppError("表头里找不到「学号」列，请确认第一行有表头（如：学号、姓名、专业）")
    missing = [FIELD_LABELS[f] for f in REQUIRED_FIELDS if f not in columns]
    if missing:
        raise AppError(f"表头缺少必填列：{'、'.join(missing)}")
    return idx, columns, labels


def parse_workbook(data: bytes, *, max_rows: int = MAX_DATA_ROWS) -> ParsedSheet:
    """解析上传的 .xlsx，返回通过校验的行 + 错误清单。"""
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - 任何格式错误都归为「文件不合法」
        raise AppError("无法解析该文件，请上传 .xlsx 格式的 Excel（.xls 请先另存为 .xlsx）") from exc

    try:
        ws = wb.worksheets[0]
        head: list[tuple] = []
        header_idx = -1
        columns: dict[str, int] = {}
        labels: dict[str, str] = {}
        rows_iter = ws.iter_rows(values_only=True)

        # 先在开头若干行里定位表头（有些名单上面有标题行）
        for i, row in enumerate(rows_iter):
            head.append(row)
            if i >= HEADER_SCAN_ROWS - 1:
                break
        if not head:
            raise AppError("这个 Excel 是空的")
        header_idx, columns, labels = _pick_header(head)

        parsed = ParsedSheet(
            header_row=header_idx + 1,
            columns=columns,
            header_map=labels,
        )

        seen: dict[str, int] = {}
        data_rows = head[header_idx + 1 :]
        for row in rows_iter:
            data_rows.append(row)
            if len(data_rows) > max_rows:
                raise AppError(f"一次最多导入 {max_rows} 行，请拆分文件后再试")

        for offset, row in enumerate(data_rows):
            excel_row = header_idx + 2 + offset  # Excel 行号从 1 开始，且表头占一行
            student_no = cell_text(_at(row, columns.get("student_no")))
            name = cell_text(_at(row, columns.get("name")))
            major = cell_text(_at(row, columns.get("major"))) or None
            class_name = cell_text(_at(row, columns.get("class_name"))) or None

            if not student_no and not name and not major:
                continue  # 整行空白，跳过且不计入统计

            parsed.raw_count += 1
            problem = _validate(student_no, name, seen, major, class_name)
            if problem:
                parsed.errors.append(RowError(row=excel_row, reason=problem))
                continue

            seen[student_no] = excel_row
            parsed.rows.append(
                {
                    "student_no": student_no,
                    "name": name,
                    "major": major,
                    "class_name": class_name,
                    "excel_row": excel_row,
                }
            )

        if not parsed.rows and not parsed.errors:
            raise AppError("Excel 里没有数据行（只有表头？）")
        return parsed
    finally:
        wb.close()


def _at(row: tuple, idx: int | None):
    if idx is None or row is None or idx >= len(row):
        return None
    return row[idx]


def _validate(student_no: str, name: str, seen: dict[str, int], major: str | None = None, class_name: str | None = None) -> str | None:
    if not student_no:
        return "学号为空"
    if len(student_no) > 20:
        return f"学号过长（{len(student_no)} 个字符，上限 20）"
    if not name:
        return "姓名为空"
    if len(name) > 50:
        return "姓名过长（上限 50 个字符）"
    if major and len(major) > 100:
        return "专业过长（上限 100 个字符）"
    if class_name and len(class_name) > 50:
        return "班级过长（上限 50 个字符）"
    if student_no in seen:
        return f"学号重复（与第 {seen[student_no]} 行相同）"
    return None


# ----------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------
def build_points_workbook(rows: list[dict]) -> bytes:
    """生成积分详单。表头必须严格是这 5 列，一个字都不能多。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "积分详单"
    ws.append(list(EXPORT_HEADERS))

    for item in rows:
        ws.append(
            [
                item["student_no"],
                item["name"],
                item.get("major") or "",
                int(item.get("random_call_count") or 0),
                round(float(item.get("total_points") or 0), 1),
            ]
        )

    header_fill = PatternFill("solid", fgColor="1F3864")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = {1: 16, 2: 12, 3: 24, 4: 16, 5: 10}
    for idx, width in widths.items():
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"

    for row in ws.iter_rows(min_row=2, min_col=5, max_col=5):
        for cell in row:
            cell.number_format = "0.0"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
