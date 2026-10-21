"""Local practice papers, independent scoring and browser-driven answering."""
import asyncio
import json
import random
import re
import secrets
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .models import Candidate, Question, ExamRunPlan, PracticeOptions


QUESTION_COUNT = 100
KINDS = ("单选", "多选", "判断")
ASSETS = Path(__file__).parent / "assets" / "mock_exam"


class PracticePlanner:
    """Choose different scores based on successful submissions, not failed attempts."""
    def __init__(self, options: PracticeOptions, rng=None):
        options.validate()
        self.options = options
        self.rng = rng or random.SystemRandom()
        self.counts = {score: 0 for score in range(max(options.minimum_score, options.score_min), options.score_max + 1)}
        self.last_score = None
        self.reserved = Counter()
        self.last_reserved = None

    def reserve_plan(self):
        eligible = [score for score in self.counts if score != self.last_reserved]
        least = min(self.counts[score] + self.reserved[score] for score in eligible)
        score = self.rng.choice([score for score in eligible if self.counts[score] + self.reserved[score] == least])
        self.reserved[score] += 1
        self.last_reserved = score
        duration = 0 if self.options.fastest else self.rng.uniform(self.options.minutes_min, self.options.minutes_max) * 60
        return ExamRunPlan(score, duration, self.options.minimum_score, self.options.score_min, self.options.score_max)

    def release_plan(self, plan):
        self.reserved[plan.target_score] -= 1

    def next_plan(self):
        eligible = [score for score in self.counts if score != self.last_score]
        least = min(self.counts[score] for score in eligible)
        score = self.rng.choice([score for score in eligible if self.counts[score] == least])
        duration = 0 if self.options.fastest else self.rng.uniform(self.options.minutes_min, self.options.minutes_max) * 60
        return ExamRunPlan(score, duration, self.options.minimum_score, self.options.score_min, self.options.score_max)

    def record_success(self, score):
        if score not in self.counts:
            raise ValueError("实际模拟得分不在设定范围内")
        self.counts[score] += 1
        self.last_score = score


def normalized(text):
    return re.sub(r"\s+", "", text).casefold()


def question_key(kind, stem, options):
    return kind, normalized(stem), tuple(sorted(normalized(v) for v in options.values()))


def labeled_key(kind, stem, options):
    return kind, normalized(stem), tuple(sorted((label, normalized(value)) for label, value in options.items()))


class KnowledgeIndex:
    def __init__(self, questions):
        self.entries = {}
        self.labeled_entries = {}
        for q in questions:
            if q.kind not in KINDS or not q.stem.strip() or not q.options or not q.answers:
                raise ValueError("题库存在缺少题干、选项、答案或不支持题型的题目")
            if not set(q.answers).issubset(q.options) or any(not re.fullmatch(r"[A-Z]", key) for key in q.options):
                raise ValueError("题库存在答案或选项编号异常的题目")
            if q.kind != "多选" and len(q.answers) != 1:
                raise ValueError("单选或判断题只能有一个正确答案")
            values = [normalized(v) for v in q.options.values()]
            exact = labeled_key(q.kind, q.stem, q.options)
            labels = tuple(sorted(q.answers))
            if exact in self.labeled_entries and self.labeled_entries[exact] != labels:
                raise ValueError("题库存在同题干、同选项但答案冲突的题目")
            self.labeled_entries[exact] = labels
            key = question_key(q.kind, q.stem, q.options)
            answer = frozenset(normalized(q.options[a]) for a in q.answers)
            if len(set(values)) != len(values) or (key in self.entries and self.entries[key] != answer):
                self.entries[key] = None
            else:
                self.entries[key] = answer

    def answer(self, kind, stem, options):
        exact = labeled_key(kind, stem, options)
        if exact in self.labeled_entries:
            return list(self.labeled_entries[exact])
        key = question_key(kind, stem, options)
        if key not in self.entries or self.entries[key] is None:
            raise ValueError("本地模拟试题无法在当前题库中唯一匹配，已停止本人员的作答")
        correct = self.entries[key]
        return sorted(label for label, value in options.items() if normalized(value) in correct)


def validate_bank(questions):
    if len(questions) < QUESTION_COUNT:
        raise ValueError("测试模式需要至少 100 道有效题目，请先导入题库")
    if len({q.number for q in questions}) != len(questions):
        raise ValueError("题库存在重复题号，请先修正题库")
    if not set(KINDS).issubset(q.kind for q in questions):
        raise ValueError("测试模式题库需要同时包含单选、多选和判断题")
    return KnowledgeIndex(questions)


def sample_questions(questions, rng=None):
    """Balanced sampling without replacement, with fallback for a small type pool."""
    rng = rng or random.SystemRandom()
    groups = {kind: [q for q in questions if q.kind == kind] for kind in KINDS}
    if len(questions) < QUESTION_COUNT or any(not pool for pool in groups.values()):
        raise ValueError("题库不足 100 题或缺少单选、多选、判断题")
    selected = []
    for kind, count in zip(KINDS, (34, 33, 33)):
        selected.extend(rng.sample(groups[kind], min(count, len(groups[kind]))))
    used = {q.number for q in selected}
    selected.extend(rng.sample([q for q in questions if q.number not in used], QUESTION_COUNT - len(selected)))
    rng.shuffle(selected)
    return tuple(selected)


@dataclass(frozen=True)
class ExamPaper:
    id: str
    candidate: Candidate
    questions: tuple[Question, ...]
    plan: ExamRunPlan | None = None

    def public_data(self):
        return {"title": "法治理论知识模拟测试", "name": self.candidate.name,
                "district": self.candidate.district, "maskedId": self.candidate.masked_id,
                "total": len(self.questions), "maximum": 100,
                "plannedSeconds": self.plan.duration_seconds if self.plan else 0,
                "minimumScore": self.plan.minimum_score if self.plan else None,
                "scoreRange": [self.plan.score_min, self.plan.score_max] if self.plan else None,
                "counts": dict(Counter(q.kind for q in self.questions)),
                "questions": [{"id": str(i), "kind": q.kind, "stem": q.stem, "options": q.options}
                              for i, q in enumerate(self.questions, 1)]}


@dataclass(frozen=True)
class ExamScore:
    score: int
    correct: int
    total: int
    maximum: int
    details: tuple[dict, ...]
    elapsed_seconds: float = 0
    planned_seconds: float = 0
    target_score: int | None = None


def grade_paper(paper, answers):
    ids = {str(i) for i in range(1, len(paper.questions) + 1)}
    if not isinstance(answers, dict) or set(answers) != ids:
        raise ValueError("提交内容必须包含本试卷的全部题目")
    correct, details = 0, []
    for i, q in enumerate(paper.questions, 1):
        selected = answers[str(i)]
        if (not isinstance(selected, list) or any(not isinstance(a, str) for a in selected)
                or len(set(selected)) != len(selected) or not set(selected).issubset(q.options)
                or (q.kind != "多选" and len(selected) > 1)):
            raise ValueError("提交的选项不属于本题或选项格式错误")
        ok = set(selected) == set(q.answers)
        correct += int(ok)
        details.append({"id": str(i), "correct": ok, "selected": sorted(selected), "answers": sorted(q.answers)})
    return ExamScore(correct, correct, len(paper.questions), 100, tuple(details))


class MockExamServer:
    def __init__(self):
        try:
            self.assets = {name: (ASSETS / name).read_bytes() for name in ("index.html", "app.css", "app.js")}
        except OSError as error:
            raise ValueError("本地模拟网页资源不完整，请重新解压软件") from error
        self.token = secrets.token_urlsafe(24)
        self.papers, self.results, self.submissions = {}, {}, {}
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send(self, status, body, mime="application/json; charset=utf-8"):
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def parts(self):
                # Only accept requests addressed to our loopback socket.
                if self.headers.get("Host") != owner.host:
                    return []
                p = urlparse(self.path)
                parts = p.path.strip("/").split("/")
                return parts[1:] if not p.query and parts[0] == owner.token else []

            def do_GET(self):
                parts = self.parts()
                if len(parts) == 1 and parts[0] in ("app.css", "app.js"):
                    name = parts[0]
                    self.send(200, owner.assets[name], "text/css; charset=utf-8" if name.endswith("css") else "text/javascript; charset=utf-8")
                    return
                with owner.lock:
                    paper = owner.papers.get(parts[0]) if parts else None
                if paper and len(parts) == 1 and self.path.endswith("/"):
                    self.send(200, owner.assets["index.html"], "text/html; charset=utf-8")
                elif paper and parts[1:] == ["data"]:
                    self.send(200, paper.public_data())
                else:
                    self.send(404, {"error": "模拟试卷不存在或已结束"})

            def do_POST(self):
                parts = self.parts()
                if (len(parts) != 2 or parts[1] != "submit"
                        or self.headers.get("Origin") != owner.origin
                        or self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
                    self.send(403, {"error": "提交来源或格式不正确"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("提交内容大小不正确")
                    answers = json.loads(self.rfile.read(length))
                    with owner.lock:
                        paper = owner.papers.get(parts[0])
                        if not paper:
                            self.send(404, {"error": "试卷不存在"})
                            return
                        if paper.id in owner.results:
                            if owner.submissions[paper.id] != answers:
                                self.send(409, {"error": "此试卷已提交，不能修改答案"})
                                return
                            result = owner.results[paper.id]
                        else:
                            result = grade_paper(paper, answers)
                            owner.results[paper.id] = result
                            owner.submissions[paper.id] = answers
                    self.send(200, asdict(result))
                except (ValueError, UnicodeError):
                    self.send(400, {"error": "试卷答案格式错误，请检查后重新提交"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.host = f"127.0.0.1:{self.httpd.server_port}"
        self.origin = f"http://{self.host}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        self.closed = False

    def create_paper(self, candidate, questions, rng=None, plan=None):
        paper = ExamPaper(secrets.token_hex(16), candidate, sample_questions(questions, rng), plan)
        with self.lock:
            self.papers[paper.id] = paper
        return paper

    def url_for(self, paper):
        return f"{self.origin}/{self.token}/{paper.id}/"

    def result_for(self, paper):
        with self.lock:
            return self.results.get(paper.id)

    def discard(self, paper):
        with self.lock:
            for mapping in (self.papers, self.results, self.submissions):
                mapping.pop(paper.id, None)

    def close(self):
        if not self.closed:
            self.closed = True
            self.httpd.shutdown()
            self.httpd.server_close()
            self.thread.join(timeout=2)


def build_answer_plan(questions, knowledge, target_score, rng=None):
    rng = rng or random.SystemRandom()
    correct, alternatives = {}, {}
    for question in questions:
        qid = question["id"]
        labels = knowledge.answer(question["kind"], question["stem"], question["options"])
        correct[qid] = labels
        options = list(question["options"])
        wrong = [key for key in options if [key] != labels]
        if wrong:
            alternatives[qid] = [rng.choice(wrong)]
        elif len(options) > 1:
            alternatives[qid] = sorted(options)
    errors = len(questions) - target_score
    if errors < 0 or errors > len(alternatives):
        raise ValueError("题库中可用于本地模拟分数分布的题目不足")
    answers = {qid: list(values) for qid, values in correct.items()}
    for qid in rng.sample(list(alternatives), errors):
        answers[qid] = alternatives[qid]
    return answers, correct


async def answer_mock_exam(page, server, paper, knowledge, progress=lambda _: None, *, plan=None, clock=None, sleep=None):
    plan = plan or ExamRunPlan()
    plan.validate()
    clock, sleep = clock or time.monotonic, sleep or asyncio.sleep
    started = clock()
    async def wait_until(deadline):
        remaining = deadline - clock()
        if remaining > 0:
            await sleep(remaining)
    target = server.url_for(paper)
    await page.goto(target, wait_until="domcontentloaded")
    await page.locator("article.question").nth(QUESTION_COUNT - 1).wait_for()
    if page.url != target:
        raise ValueError("本地模拟试卷地址发生变化，已停止作答")
    questions = await page.locator("article.question").evaluate_all("""cards => cards.map(c => ({
        id: c.dataset.questionId, kind: c.dataset.kind, stem: c.querySelector('.stem').textContent,
        options: Object.fromEntries([...c.querySelectorAll('.option')].map(o =>
            [o.querySelector('input').value, o.querySelector('.option-text').textContent]))
    }))""")
    if len(questions) != QUESTION_COUNT or {q['id'] for q in questions} != {str(i) for i in range(1, QUESTION_COUNT + 1)}:
        raise ValueError("本地试卷题目数量或编号异常")
    expected, correct = build_answer_plan(questions, knowledge, plan.target_score)
    async def fill(question, labels):
        card = page.locator(f'article[data-question-id="{question["id"]}"]')
        if question["kind"] == "多选":
            for key in question["options"]:
                await card.locator(f'input[value="{key}"]').set_checked(key in labels)
        else:
            await card.locator(f'input[value="{labels[0]}"]').check()
    answering_seconds = max(0, plan.duration_seconds - min(5, plan.duration_seconds * 0.05))
    for i, question in enumerate(questions, 1):
        await fill(question, expected[question["id"]])
        await wait_until(started + answering_seconds * i / QUESTION_COUNT)
        if i % 5 == 0:
            progress(f"本地模拟作答：{i}/100 题 · 已用 {(clock() - started) / 60:.1f} 分钟")
    async def review():
        if page.url != target:
            raise ValueError("本地试卷地址发生变化，未提交")
        actual = await page.locator("article.question").evaluate_all("""cards => Object.fromEntries(cards.map(c =>
            [c.dataset.questionId, [...c.querySelectorAll('input:checked')].map(i => i.value).sort()]))""")
        if actual != expected:
            progress("复核发现选项变化，正在恢复本地模拟答题计划")
            for question in questions:
                if actual.get(question["id"]) != expected[question["id"]]:
                    await fill(question, expected[question["id"]])
            actual = await page.locator("article.question").evaluate_all("""cards => Object.fromEntries(cards.map(c =>
                [c.dataset.questionId, [...c.querySelectorAll('input:checked')].map(i => i.value).sort()]))""")
        predicted = sum(actual.get(qid) == labels for qid, labels in correct.items())
        if (actual != expected or predicted != plan.target_score or predicted < plan.minimum_score
                or not plan.score_min <= predicted <= plan.score_max):
            raise ValueError("本地模拟答案复核未达到目标分数、最低分或范围要求，未提交试卷")
        return predicted
    progress("正在首次复核本地模拟试卷的 100 道答案")
    await review()
    if plan.duration_seconds and clock() < started + plan.duration_seconds:
        progress(f"答案首次复核通过，按计划在 {plan.duration_seconds / 60:.1f} 分钟提交")
        await wait_until(started + plan.duration_seconds)
    progress("正在提交前再次复核全部答案与目标分数")
    await review()
    await page.locator("#submitExam").click()
    await page.locator("#examResult:not([hidden])").wait_for()
    result = server.result_for(paper)
    if result is None or await page.locator("#scoreValue").inner_text() != str(result.score):
        raise ValueError("本地模拟评分结果未确认")
    if result.score != plan.target_score or result.score < plan.minimum_score or not plan.score_min <= result.score <= plan.score_max:
        raise ValueError(f"本地模拟实际得分 {result.score} 分，不符合设定目标，未记为完成")
    return replace(result, elapsed_seconds=round(clock() - started, 3), planned_seconds=plan.duration_seconds, target_score=plan.target_score)
