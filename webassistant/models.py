from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
import math


DEFAULT_URL = "https://ks.cqsdx.cn/exam/"
DEFAULT_SCORE_MIN = 98
DEFAULT_SCORE_MAX = 100


@dataclass(frozen=True)
class Candidate:
    row: int
    district: str
    name: str = field(repr=False)
    id_number: str = field(repr=False)

    @property
    def masked_id(self) -> str:
        if len(self.id_number) < 8:
            return "*" * len(self.id_number)
        return self.id_number[:3] + "*" * (len(self.id_number) - 7) + self.id_number[-4:]


@dataclass(frozen=True)
class Question:
    number: int
    kind: str
    stem: str
    options: dict[str, str]
    answers: tuple[str, ...]
    pages: tuple[int, ...]


@dataclass
class IdentityImport:
    candidates: list[Candidate]
    issues: list[str]
    sheet: str
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)


@dataclass
class BankImport:
    questions: list[Question]
    issues: list[str]
    page_count: int


@dataclass(frozen=True)
class ExecutionOptions:
    # Keep programmatic callers serial unless they explicitly request concurrency.
    concurrency: int = 1
    background: bool = True

    def validate(self):
        if isinstance(self.concurrency, bool) or not isinstance(self.concurrency, int) or not 1 <= self.concurrency <= 30:
            raise ValueError("并发数量须为 1–30 的整数")


@dataclass(frozen=True)
class PracticeOptions:
    fastest: bool = False
    minutes_min: float = 10.0
    minutes_max: float = 10.0
    minimum_score: int = 90
    score_min: int = DEFAULT_SCORE_MIN
    score_max: int = DEFAULT_SCORE_MAX

    def validate(self):
        if (not math.isfinite(self.minutes_min) or not math.isfinite(self.minutes_max)
                or not 1 <= self.minutes_min <= self.minutes_max <= 180):
            raise ValueError("答题时间须为 1–180 分钟，最短时间不能大于最长时间")
        if not 0 <= self.minimum_score <= 100 or not 0 <= self.score_min <= self.score_max <= 100:
            raise ValueError("最低分与分数范围须在 0–100 分内，分数下限不能大于上限")
        if max(self.minimum_score, self.score_min) >= self.score_max:
            raise ValueError("最低分与得分范围的交集至少需要两个整数分数，才能避免批量全部同分")


@dataclass(frozen=True)
class ExamRunPlan:
    target_score: int = 100
    duration_seconds: float = 0
    minimum_score: int = 90
    score_min: int = 90
    score_max: int = 100

    def validate(self):
        if not 0 <= self.minimum_score <= self.target_score <= 100 or not self.score_min <= self.target_score <= self.score_max:
            raise ValueError("模拟目标分数不符合最低分或得分范围")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("模拟答题用时不正确")


@dataclass(frozen=True)
class SiteConfig:
    url: str = DEFAULT_URL
    chrome_path: str = ""
    timeout_ms: int = 20000
    # Only the automated local test harness can enable this; absent from GUI/config files.
    allow_local_test: bool = False

    def validate(self) -> None:
        p = urlparse(self.url)
        if p.username or p.password or p.query or p.fragment:
            raise ValueError("网址不能包含账号、查询参数或片段")
        local = self.allow_local_test and p.hostname in {"127.0.0.1", "localhost"}
        if not local and (p.scheme != "https" or p.hostname != "ks.cqsdx.cn" or p.port not in (None, 443)):
            raise ValueError("本版仅支持 https://ks.cqsdx.cn 考试系统")
        if local and p.scheme != "http":
            raise ValueError("本地测试地址须使用 HTTP")
        if p.path.rstrip("/") not in {"/exam", "/exam/index"}:
            raise ValueError("请使用区县选择入口 https://ks.cqsdx.cn/exam/")
        if self.chrome_path and not Path(self.chrome_path).is_file():
            raise ValueError("指定的浏览器程序不存在")
