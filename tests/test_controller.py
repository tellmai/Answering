import os
import time
import asyncio

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from webassistant.controller import BrowserWorker
from webassistant.browser import CaptchaFailure
from webassistant.models import Candidate, SiteConfig, PracticeOptions


def wait(app, condition):
    deadline = time.monotonic() + 10
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert condition()


@pytest.mark.parametrize("stop_immediately", [False, True])
def test_worker_thread_batch_and_stop_during_startup(tmp_path, monkeypatch, stop_immediately):
    visited, ready, finished, errors = [], [], [], []
    class Session:
        def __init__(self, *_):
            pass
        async def login(self, candidate, _):
            visited.append(candidate.row)
            await asyncio.sleep(0.02)
            if candidate.row == 2:
                raise CaptchaFailure("验证码连续三次失败")
        async def close(self):
            pass
    monkeypatch.setattr("webassistant.controller.SiteSession", Session)
    app = QApplication.instance() or QApplication([])
    worker = BrowserWorker()
    worker.ready.connect(lambda: ready.append(True))
    worker.job_finished.connect(lambda: finished.append(True))
    worker.failed.connect(errors.append)
    worker.start()
    try:
        wait(app, lambda: ready)
        candidates = [Candidate(row, "黔江区", "演示人员", "110101199001010010") for row in (2, 3)]
        worker.start_job("login", SiteConfig(), candidates, str(tmp_path))
        if stop_immediately:
            worker.stop_job()
        wait(app, lambda: finished)
        assert not errors
        statuses = [r.status for r in worker.last_store.records]
        assert statuses == (["pending", "pending"] if stop_immediately else ["failed", "completed"])
        assert visited == ([] if stop_immediately else [2, 3])
        assert (worker.last_store.folder / "未完成信息统计.xlsx").exists()
    finally:
        worker.shutdown()
        wait(app, lambda: not worker.isRunning())


@pytest.mark.parametrize("graceful", [False, True])
def test_stop_during_mock_exam_includes_whole_person(tmp_path, monkeypatch, sample_bank, graceful):
    from webassistant.mock_exam import ExamScore
    entered, ready, finished, closed = [], [], [], []
    class Session:
        page = None
        def __init__(self, *_):
            pass
        async def login(self, *_):
            pass
        async def close(self):
            closed.append(True)
    async def answering(*_, **kwargs):
        entered.append(True)
        await asyncio.sleep(0.25 if graceful else 30)
        score = kwargs["plan"].target_score
        return ExamScore(score, score, 100, 100, ())
    monkeypatch.setattr("webassistant.controller.SiteSession", Session)
    monkeypatch.setattr("webassistant.controller.answer_mock_exam", answering)
    app = QApplication.instance() or QApplication([])
    worker = BrowserWorker()
    worker.ready.connect(lambda: ready.append(True))
    worker.job_finished.connect(lambda: finished.append(True))
    worker.start()
    try:
        wait(app, lambda: ready)
        candidates = [Candidate(row, "黔江区", "演示人员", "110101199001010010") for row in (2, 3)]
        worker.start_job("login", SiteConfig(), candidates, str(tmp_path), (), sample_bank, PracticeOptions(fastest=True))
        wait(app, lambda: entered)
        (worker.stop_after_current if graceful else worker.stop_job)()
        wait(app, lambda: finished)
        records = worker.last_store.records
        assert records[0].status == ("completed" if graceful else "stopped")
        assert (91 <= records[0].score <= 99) if graceful else records[0].score is None
        assert records[1].status == "pending" and len(entered) == 1
        assert not closed and worker.retained_sessions and worker.retained_servers
    finally:
        worker.shutdown()
        wait(app, lambda: not worker.isRunning())


def test_late_stop_during_server_cleanup_still_finishes_job(tmp_path, monkeypatch, sample_bank):
    import threading
    from webassistant.mock_exam import ExamScore, MockExamServer
    ready, finished = [], []
    cleaning = threading.Event()
    class SlowServer(MockExamServer):
        def close(self):
            cleaning.set()
            time.sleep(0.2)
            super().close()
    class Session:
        page = None
        def __init__(self, *_):
            pass
        async def login(self, *_):
            pass
        async def close(self):
            pass
    async def answering(*_, **kwargs):
        score = kwargs["plan"].target_score
        return ExamScore(score, score, 100, 100, ())
    monkeypatch.setattr("webassistant.controller.SiteSession", Session)
    monkeypatch.setattr("webassistant.controller.MockExamServer", SlowServer)
    monkeypatch.setattr("webassistant.controller.answer_mock_exam", answering)
    app = QApplication.instance() or QApplication([])
    worker = BrowserWorker()
    worker.ready.connect(lambda: ready.append(True))
    worker.job_finished.connect(lambda: finished.append(True))
    worker.start()
    try:
        wait(app, lambda: ready)
        worker.start_job("login", SiteConfig(), [Candidate(2, "黔江区", "演示人员", "110101199001010010")], str(tmp_path), (), sample_bank, PracticeOptions(fastest=True))
        wait(app, cleaning.is_set)
        worker.stop_job()
        wait(app, lambda: finished)
        assert worker.last_store.records[0].status == "completed"
        assert 91 <= worker.last_store.records[0].score <= 99
    finally:
        worker.shutdown()
        wait(app, lambda: not worker.isRunning())


def test_mock_resource_failure_records_every_person(tmp_path, monkeypatch, sample_bank):
    ready, finished = [], []
    class Session:
        def __init__(self, *_):
            pass
        async def login(self, *_):
            pass
        async def close(self):
            pass
    def unavailable():
        raise ValueError("模拟网页资源不完整")
    monkeypatch.setattr("webassistant.controller.SiteSession", Session)
    monkeypatch.setattr("webassistant.controller.MockExamServer", unavailable)
    app = QApplication.instance() or QApplication([])
    worker = BrowserWorker()
    worker.ready.connect(lambda: ready.append(True))
    worker.job_finished.connect(lambda: finished.append(True))
    worker.start()
    try:
        wait(app, lambda: ready)
        candidates = [Candidate(row, "黔江区", "演示人员", "110101199001010010") for row in (2, 3)]
        worker.start_job("login", SiteConfig(), candidates, str(tmp_path), (), sample_bank, PracticeOptions(fastest=True))
        wait(app, lambda: finished)
        assert all(r.status == "failed" and r.stage == "本地模拟测试" and r.score is None for r in worker.last_store.records)
    finally:
        worker.shutdown()
        wait(app, lambda: not worker.isRunning())


def test_last_person_keeps_result_while_stop_dialog_is_open(tmp_path, monkeypatch, sample_bank):
    from webassistant.mock_exam import ExamScore
    ready, entered, finished, closed = [], [], [], []
    class Session:
        page = None
        def __init__(self, *_):
            pass
        async def login(self, *_):
            pass
        async def close(self):
            closed.append(True)
    async def answering(*_, **kwargs):
        entered.append(True)
        await asyncio.sleep(0.15)
        score = kwargs["plan"].target_score
        return ExamScore(score, score, 100, 100, ())
    monkeypatch.setattr("webassistant.controller.SiteSession", Session)
    monkeypatch.setattr("webassistant.controller.answer_mock_exam", answering)
    app = QApplication.instance() or QApplication([])
    worker = BrowserWorker()
    worker.ready.connect(lambda: ready.append(True))
    worker.job_finished.connect(lambda: finished.append(True))
    worker.start()
    try:
        wait(app, lambda: ready)
        worker.start_job("login", SiteConfig(), [Candidate(2, "黔江区", "演示人员", "110101199001010010")], str(tmp_path), (), sample_bank, PracticeOptions(fastest=True))
        wait(app, lambda: entered)
        worker.pause_queue()
        wait(app, lambda: worker.last_store.records[0].status == "completed")
        time.sleep(0.1)
        app.processEvents()
        assert not finished and not closed
        worker.stop_after_current()
        wait(app, lambda: finished)
        assert worker.retained_sessions and worker.retained_servers and not closed
    finally:
        worker.shutdown()
        wait(app, lambda: not worker.isRunning())
