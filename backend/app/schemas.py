"""接口出入参数据结构（Pydantic v2）。

作用有两个：
1. 自动校验：前端传错类型直接 422，不用每个接口手写 if；
2. 自动文档：/docs 里的请求体、响应体都是这些类生成的，前端照着填就行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

DataT = TypeVar("DataT")


class Resp(BaseModel, Generic[DataT]):
    """统一响应包。code=0 表示成功。"""

    code: int = 0
    msg: str = "ok"
    data: DataT | None = None


# ----------------------------------------------------------------------
# 学生
# ----------------------------------------------------------------------
class StudentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_no: str
    name: str
    major: str | None = None
    class_name: str | None = None
    total_points: float = 0.0
    call_count: int = 0
    random_call_count: int = 0
    arrive_count: int = 0
    transfer_credit: int = 0


class StudentPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[StudentOut]


class ImportErrorItem(BaseModel):
    row: int = Field(description="Excel 里的行号（从 1 开始，含表头行）")
    reason: str


class ImportResult(BaseModel):
    total_rows: int = Field(description="Excel 里读到数据行数（不含表头）")
    inserted: int
    updated: int
    failed: list[ImportErrorItem]
    header_map: dict[str, str] = Field(
        default_factory=dict, description="字段 -> 实际匹配到的表头文字，便于前端展示"
    )


# ----------------------------------------------------------------------
# 班级
# ----------------------------------------------------------------------
class ClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=50, description="班级名称")
    note: str | None = Field(default=None, max_length=200, description="备注，可空")


class ClassOut(BaseModel):
    id: int
    name: str
    note: str | None = None
    student_count: int = Field(default=0, description="该班级下的学生人数")


class ClassListOut(BaseModel):
    items: list[ClassOut]
    total_students: int = Field(description="所有学生人数，用于展示「全部班级」")


class DeleteStudentResult(BaseModel):
    student_id: int
    student_no: str
    name: str


# ----------------------------------------------------------------------
# 场次
# ----------------------------------------------------------------------
class SessionCreate(BaseModel):
    mode: Literal["random", "sequential", "clock"] = "random"
    class_name: str | None = Field(
        default=None, max_length=50, description="本场次点名的班级；不传表示全部学生"
    )


class PickIn(BaseModel):
    student_id: int = Field(description="被选中的学生 id（按时间点名模式由老师手动指定）")


class ClockCandidateItem(BaseModel):
    student_id: int
    student_no: str
    name: str
    major: str | None = None
    called_count: int = Field(default=0, description="本轮已被点次数（>0 说明可以再点一次）")


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mode: str
    class_name: str | None = None
    seq_cursor: int
    round_no: int
    draw_count: int
    status: str
    started_at: datetime


# ----------------------------------------------------------------------
# 点名 / 记分
# ----------------------------------------------------------------------
class RollCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    student_id: int
    round_no: int
    seq: int
    mode: str
    arrived: bool | None = None
    repeat_ok: bool | None = None
    answer_score: float | None = None
    point_delta: float = 0.0
    status: str
    transferred_from: int | None = None
    created_at: datetime
    student: StudentOut | None = None


class DrawProgress(BaseModel):
    round_no: int
    called_in_round: int = Field(description="本轮已被点名人数")
    remaining_in_round: int = Field(description="本轮还没被点名的人数")
    total_students: int
    is_new_round: bool = False


class DrawOut(BaseModel):
    rollcall: RollCallOut
    progress: DrawProgress


class ClockCandidatesOut(BaseModel):
    """按时间点名的候选列表。"""

    digit: int = Field(description="当前时间的分钟个位")
    items: list[ClockCandidateItem]
    progress: DrawProgress


class ScoreIn(BaseModel):
    """记分入参。

    到达课堂 +1；准确复述问题 +0.5，没复述对 -1；
    回答问题按表现加 0.5~3（未作答传 0 或 null）。
    """

    arrived: bool = True
    repeat_ok: bool | None = None
    answer_score: float | None = Field(default=None, ge=0, le=3)


class TransferIn(BaseModel):
    to_student_id: int = Field(description="转移给哪位同学（students.id）")


class AuditCandidate(BaseModel):
    student_id: int
    student_no: str | None
    name: str | None
    weight: float
    rand_u: float
    stored_key: float
    recomputed_key: float | None
    probability: float | None
    key_matches: bool


class DrawAuditOut(BaseModel):
    draw_id: int
    session_id: int
    round_no: int
    seq: int
    algorithm: str
    alpha: float
    winner_id: int
    rollcall_student_id: int
    replayed_winner_id: int | None
    verified: bool | None
    status: Literal["verified", "precision_limited", "mismatch", "unavailable", "not_applicable"]
    explanation: str
    created_at: datetime
    candidates: list[AuditCandidate]
    limitation: str


# ----------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------
class RankItem(BaseModel):
    rank: int
    student_id: int
    student_no: str
    name: str
    major: str | None = None
    total_points: float
    call_count: int
    random_call_count: int


class RankOut(BaseModel):
    items: list[RankItem]
    # 直接给前端画图用的数组（ECharts 拿来就能用）
    categories: list[str]
    points_series: list[float]
    calls_series: list[int] = Field(description="随机点名次数，与 items.random_call_count 对应")


class ProbabilityItem(BaseModel):
    student_id: int
    student_no: str
    name: str
    total_points: float
    weight: float
    probability: float = Field(description="下一次点名被点到的概率，0~1；顺序模式为 0 或 1")
    excluded: bool = Field(description="本轮已被点过，不再参与本轮抽签")


class ProbabilityOut(BaseModel):
    session_id: int
    mode: str
    round_no: int
    is_new_round: bool = Field(default=False, description="下一次点名是否将开启新一轮；查询不修改数据库")
    alpha: float
    items: list[ProbabilityItem]


class PointHistoryItem(BaseModel):
    log_id: int
    rollcall_id: int | None
    delta: float
    total_points: float
    reason: str
    note: str | None
    created_at: datetime


class StudentPointsOut(BaseModel):
    student: StudentOut
    items: list[PointHistoryItem]
    categories: list[str]
    points_series: list[float]


class HistoryItem(BaseModel):
    rollcall_id: int
    student_no: str
    name: str
    mode: str
    round_no: int
    seq: int
    arrived: bool | None = None
    repeat_ok: bool | None = None
    answer_score: float | None = None
    point_delta: float
    status: str
    called_at: datetime


class HistoryPage(BaseModel):
    total: int
    page: int
    size: int
    items: list[HistoryItem]


class HealthOut(BaseModel):
    status: str
    database: str
    students: int
