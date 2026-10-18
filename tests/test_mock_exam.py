import asyncio
import json
import random
from collections import Counter
from dataclasses import asdict, replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from webassistant.browsers import launch_browser
from webassistant.mock_exam import ExamPaper, KnowledgeIndex, MockExamServer, PracticePlanner, answer_mock_exam, grade_paper, sample_questions, validate_bank
from webassistant.models import Candidate, PracticeOptions, ExamRunPlan


CANDIDATE = Candidate(2, "黔江区", "演示考生", "110101199001010010")


def correct_answers(paper):
    return {str(i): list(q.answers) for i, q in enumerate(paper.questions, 1)}


def test_balanced_unique_random_papers_and_small_type_pool(sample_bank):
    first = sample_questions(sample_bank, random.Random(3))
    second = sample_questions(sample_bank, random.Random(4))
    assert len(first) == len({q.number for q in first}) == 100
    assert Counter(q.kind for q in first) == {"单选": 34, "多选": 33, "判断": 33}
    assert first != second
    scarce = [q for q in sample_bank if q.kind != "多选"] + [next(q for q in sample_bank if q.kind == "多选")]
    sampled = sample_questions(scarce, random.Random(2))
    assert len(sampled) == 100 and Counter(q.kind for q in sampled)["多选"] == 1


def test_bank_validation_and_text_based_answer_matching(sample_bank):
    knowledge = validate_bank(sample_bank)
    q = next(q for q in sample_bank if q.kind == "多选")
    # Match option text even when option letters have been rearranged.
    options = {"A": q.options["B"], "B": q.options["C"], "C": q.options["A"]}
    assert knowledge.answer(q.kind, q.stem + "\n", options) == ["B", "C"]
    with pytest.raises(ValueError, match="100"):
        validate_bank(sample_bank[:99])
    with pytest.raises(ValueError, match="同时包含"):
        validate_bank([q for q in sample_bank if q.kind != "多选"])
    with pytest.raises(ValueError, match="冲突"):
        KnowledgeIndex([sample_bank[0], replace(sample_bank[0], number=100, answers=("A",))])
    with pytest.raises(ValueError, match="无法"):
        knowledge.answer(q.kind, "题库以外的问题", q.options)


def test_identical_option_text_keeps_distinct_option_letters(sample_bank):
    q = replace(sample_bank[1], options={"A": "相同选项", "B": "相同选项", "C": "另一个选项"}, answers=("A", "C"))
    knowledge = KnowledgeIndex([q])
    assert knowledge.answer(q.kind, q.stem, q.options) == ["A", "C"]


def test_grading_exact_multi_selection_and_invalid_submission(sample_bank):
    paper = ExamPaper("paper", CANDIDATE, sample_questions(sample_bank, random.Random(7)))
    answers = correct_answers(paper)
    assert grade_paper(paper, answers).score == 100
    multi = next(str(i) for i, q in enumerate(paper.questions, 1) if q.kind == "多选")
    answers[multi] = answers[multi][:1]
    assert grade_paper(paper, answers).score == 99
    answers[multi] = ["A", "B", "C"]
    assert grade_paper(paper, answers).score == 99
    answers[multi] = ["Z"]
    with pytest.raises(ValueError):
        grade_paper(paper, answers)
    with pytest.raises(ValueError):
        grade_paper(paper, {"score": 100})


def test_server_privacy_scoring_and_repeat_submission(sample_bank):
    server = MockExamServer()
    try:
        paper = server.create_paper(CANDIDATE, sample_bank)
        target = server.url_for(paper)
        with urlopen(target + "data") as response:
            public = json.loads(response.read())
        assert CANDIDATE.id_number not in json.dumps(public)
        assert public["name"] == CANDIDATE.name
        assert len(public["questions"]) == 100
        assert all("answers" not in q for q in public["questions"])
        answers = correct_answers(paper)
        def submit(payload, origin=server.origin):
            return urlopen(Request(target + "submit", json.dumps(payload).encode(), {"Content-Type": "application/json", "Origin": origin}))
        with submit(answers) as response:
            assert json.loads(response.read())["score"] == 100
        with submit(answers) as response:
            assert json.loads(response.read())["score"] == 100
        answers["1"] = []
        with pytest.raises(HTTPError) as error:
            submit(answers)
        assert error.value.code == 409
        with pytest.raises(HTTPError) as error:
            submit(answers, "https://example.com")
        assert error.value.code == 403
        server.discard(paper)
        with pytest.raises(HTTPError):
            urlopen(target + "data")
    finally:
        server.close()


def test_browser_answers_reviews_submits_and_manual_button_validation(sample_bank, tmp_path):
    server = MockExamServer()
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser, _ = await launch_browser(p, headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                paper = server.create_paper(CANDIDATE, sample_bank)
                await page.goto(server.url_for(paper))
                await page.locator("article.question").nth(99).wait_for()
                await page.locator("#submitExam").click()
                assert "100 题未作答" in await page.locator("#submitNotice").inner_text()
                assert server.result_for(paper) is None
                assert await page.locator('input[type="checkbox"]').count() > 0
                assert await page.locator('input[type="radio"]').count() > 0
                # Data rendered as text, with no answer key in the page.
                assert not await page.locator("[data-answer]").count()
                await page.screenshot(path=str(tmp_path / "mock-exam.png"))
                progress = []
                result = await answer_mock_exam(page, server, paper, validate_bank(sample_bank), progress.append)
                assert result.score == 100 and result.correct == result.total == 100
                assert any("复核" in step for step in progress)
                assert await page.locator(".answer-feedback").count() == 100
                assert await page.locator("#submitExam").is_disabled()
                assert all(not item["selected"] == [] for item in asdict(result)["details"])
                # Advance a fake monotonic clock to test ten-minute pacing without waiting.
                now = [0.0]
                async def fake_sleep(delay):
                    now[0] += delay
                    if abs(now[0] - 600) < 0.001:
                        await page.locator('article.question input').first.evaluate("i => { for (const x of i.closest('article').querySelectorAll('input')) x.checked=false; }")
                    await asyncio.sleep(0)
                timed_paper = server.create_paper(CANDIDATE, sample_bank)
                progress.clear()
                result = await answer_mock_exam(page, server, timed_paper, validate_bank(sample_bank), progress.append,
                    plan=ExamRunPlan(94, 600, 90, 91, 99), clock=lambda: now[0], sleep=fake_sleep)
                assert result.score == result.correct == 94 and result.total == 100
                assert result.elapsed_seconds == result.planned_seconds == 600
                assert any("恢复" in message for message in progress)
            finally:
                await browser.close()
    try:
        asyncio.run(run())
    finally:
        server.close()


def test_default_timing_score_rotation_and_invalid_intersections():
    defaults = PracticeOptions()
    assert not defaults.fastest and defaults.minutes_min == defaults.minutes_max == 10
    planner = PracticePlanner(PracticeOptions(fastest=True, score_min=91, score_max=94), random.Random(2))
    scores = []
    for _ in range(25):
        plan = planner.next_plan()
        assert plan.duration_seconds == 0 and 91 <= plan.target_score <= 94
        assert not scores or plan.target_score != scores[-1]
        planner.record_success(plan.target_score)
        scores.append(plan.target_score)
    assert len(set(scores[:4])) == 4
    assert max(planner.counts.values()) - min(planner.counts.values()) <= 1
    # Failed runs do not consume the variation required between successful people.
    for _ in range(20):
        assert planner.next_plan().target_score != scores[-1]
    timed = PracticePlanner(PracticeOptions(minutes_min=8, minutes_max=12), random.Random(3))
    assert 480 <= timed.next_plan().duration_seconds <= 720
    for options in (PracticeOptions(score_min=100, score_max=100), PracticeOptions(minimum_score=99, score_max=99), PracticeOptions(minutes_min=12, minutes_max=8)):
        with pytest.raises(ValueError):
            options.validate()


def test_one_minute_real_clock_submission(sample_bank):
    server = MockExamServer()
    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser, _ = await launch_browser(p, headless=True)
            try:
                page = await browser.new_page()
                plan = ExamRunPlan(93, 60, 90, 91, 99)
                paper = server.create_paper(CANDIDATE, sample_bank, plan=plan)
                result = await answer_mock_exam(page, server, paper, validate_bank(sample_bank), plan=plan)
                assert result.score == result.correct == 93 and result.total == 100
                assert 60 <= result.elapsed_seconds < 75
                assert result.planned_seconds == 60
            finally:
                await browser.close()
    try:
        asyncio.run(run())
    finally:
        server.close()
