import asyncio
from dataclasses import asdict
from PySide6.QtCore import QThread, Signal

from .batch import BatchControl, BatchRunner, BatchStore, PersonOutcome
from .browser import SiteSession, inspect_environment
from .ocr import LocalOCR
from .privacy import redact
from .mock_exam import MockExamServer, PracticePlanner, answer_mock_exam, validate_bank
from .browsers import launch_browser
from .models import Candidate, PracticeOptions, ExecutionOptions


class BrowserWorker(QThread):
    progress = Signal(str)
    check_result = Signal(str, bool, str)
    completed = Signal(str)
    failed = Signal(str)
    stopped = Signal()
    job_finished = Signal()
    ready = Signal()
    batch_started = Signal(str, object)
    person_updated = Signal(object)
    batch_finished = Signal(object)
    score_ready = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = self.task = None
        self.sessions = []
        self.batch_browser = None
        self.ocr = LocalOCR()
        self.secrets = ()
        self.control = BatchControl()
        self.last_store = None
        self._entered = False
        self._closing = False
        self.preview_server = self.preview_browser = None
        self.retained_sessions, self.retained_servers, self.retained_browsers = [], [], []

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._lifetime())
        except Exception:
            self.failed.emit("浏览器控制模块初始化失败，请检查软件是否完整")
        finally:
            self.ocr.close()
            self.loop.close()

    async def _lifetime(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            self.playwright = playwright
            self.exit_event = asyncio.Event()
            self.ready.emit()
            if self._closing:
                self.exit_event.set()
            await self.exit_event.wait()
            if self.task and not self.task.done():
                self.control.immediate.set()
                if self._entered:
                    self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            await self._close_sessions()
            await self._close_preview()
            await asyncio.gather(*(s.close() for s in self.retained_sessions), return_exceptions=True)
            await asyncio.gather(*(b.close() for b in self.retained_browsers), return_exceptions=True)
            await asyncio.gather(*(asyncio.to_thread(s.close) for s in self.retained_servers), return_exceptions=True)

    async def _close_sessions(self):
        await asyncio.gather(*(s.close() for s in self.sessions), return_exceptions=True)
        self.sessions.clear()
        if self.batch_browser:
            browser, self.batch_browser = self.batch_browser, None
            await browser.close()

    async def _close_preview(self):
        browser, server = self.preview_browser, self.preview_server
        self.preview_browser = self.preview_server = None
        try:
            if browser:
                await browser.close()
        finally:
            if server:
                await asyncio.to_thread(server.close)

    def start_job(self, kind, config, candidates=None, output_root="", rejected=(), test_questions=(), practice_options=None, execution_options=None):
        self.control = BatchControl()
        self._entered = False
        def schedule():
            if self.task and not self.task.done():
                return
            self.task = self.loop.create_task(self._execute(kind, config, candidates, output_root, rejected, test_questions, practice_options, execution_options))
        self.loop.call_soon_threadsafe(schedule)

    async def _execute(self, kind, config, candidates, output_root, rejected, test_questions, practice_options, execution_options=None):
        self._entered = True
        runner = None
        exam_server = None
        execution = execution_options or ExecutionOptions()
        candidates = list(candidates or [])
        self.secrets = tuple(value for c in candidates for value in (c.name, c.id_number))
        try:
            if kind == "login":
                execution.validate()
                knowledge = validate_bank(test_questions) if test_questions else None
                planner = PracticePlanner(practice_options or PracticeOptions()) if knowledge else None
                store = BatchStore(output_root, candidates, rejected, "登录后本地模拟测试" if knowledge else "登录验证")
                store.practice_settings = asdict(planner.options) if planner else None
                store.execution_settings = asdict(execution)
                self.last_store = store
                runner = BatchRunner(store, self.control, self.person_updated.emit)
                self.batch_started.emit(str(store.folder), list(store.records))
                store.persist()
                if self.control.immediate.is_set():
                    raise asyncio.CancelledError
                await self._close_preview()
                await self._close_sessions()
                self.batch_browser, _ = await launch_browser(self.playwright, config.chrome_path, headless=execution.background, progress=self._progress)
                session_gate = asyncio.Lock()
                completed_sessions, papers = [], {}
                async def perform(candidate):
                    nonlocal exam_server
                    async with session_gate:
                        if len(self.sessions) >= execution.concurrency and completed_sessions:
                            old = completed_sessions.pop(0)
                            await old.close()
                            self.sessions.remove(old)
                            if old in papers:
                                exam_server.discard(papers.pop(old))
                        progress = lambda message: self._progress(f"第 {candidate.row} 行：{message}")
                        session = SiteSession(self.playwright, config, progress, browser=self.batch_browser)
                        self.sessions.append(session)
                    plan = None
                    try:
                        await session.login(candidate, self.ocr)
                        if knowledge:
                            index = next(r.index for r in store.records if r.candidate.row == candidate.row)
                            plan = planner.reserve_plan()
                            runner.update(index, stage="本地模拟测试", reason="登录成功，正在进行本地模拟测试", target_score=plan.target_score, planned_seconds=plan.duration_seconds)
                            mode = "最快完成" if not plan.duration_seconds else f"{plan.duration_seconds / 60:.1f} 分钟"
                            progress(f"登录成功，跳转本地模拟测试 · 目标 {plan.target_score} 分 · {mode}")
                            if exam_server is None:
                                exam_server = MockExamServer()
                            paper = papers[session] = exam_server.create_paper(candidate, test_questions, plan=plan)
                            result = await answer_mock_exam(session.page, exam_server, paper, knowledge, progress, plan=plan)
                            planner.record_success(result.score)
                            self.score_ready.emit(candidate, result)
                            return PersonOutcome("本地模拟测试", "100 题已作答、两次复核并提交评分", result.score, result.correct, result.total, result.maximum, result.elapsed_seconds, result.planned_seconds, result.target_score)
                    finally:
                        if plan:
                            planner.release_plan(plan)
                        completed_sessions.append(session)
                counts = await runner.run(perform, execution.concurrency)
                label = "批量本地模拟测试" if knowledge else "批量登录验证"
                stopping = self.control.after_current.is_set() or self.control.immediate.is_set()
                suffix = "已停止接入新人员，当前会话保留至关闭软件。" if stopping else "本地试卷页面已关闭。" if knowledge else f"保留最近最多 {execution.concurrency} 个身份会话。"
                self.completed.emit(f"{label}已结束：完成 {counts['completed']} 人，失败/中止 {counts['failed'] + counts['stopped']} 人，未开始 {counts['pending']} 人。结果已保存。{suffix}")
            elif kind == "preview":
                validate_bank(test_questions)
                planner = PracticePlanner(practice_options or PracticeOptions())
                await self._close_preview()
                self.preview_server = MockExamServer()
                paper = self.preview_server.create_paper(Candidate(0, "本地预览", "预览考生", ""), test_questions, plan=planner.next_plan())
                self.preview_browser, _ = await launch_browser(self.playwright, config.chrome_path, progress=self._progress)
                page = await self.preview_browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                await page.goto(self.preview_server.url_for(paper), wait_until="domcontentloaded")
                await page.locator("article.question").nth(99).wait_for()
                self.completed.emit("已生成 100 题本地模拟试卷预览；每题 1 分，满分 100 分，预览成绩不计入人员统计")
            elif kind == "export":
                if self.last_store:
                    self.last_store.persist()
                    self.completed.emit("统计已重新导出到本批次结果目录")
            else:
                if self.control.immediate.is_set():
                    raise asyncio.CancelledError
                candidate = candidates[0] if candidates else None
                ok = await inspect_environment(self.playwright, config, self.ocr,
                    candidate.district if candidate else "", self.check_result.emit)
                self.completed.emit("环境检测通过" if ok else "环境检测发现问题，请查看逐项结果")
        except asyncio.CancelledError:
            if kind == "preview" and self._closing:
                await self._close_preview()
            self.stopped.emit()
        except Exception as error:
            if kind == "preview":
                await self._close_preview()
            from playwright.async_api import Error as PlaywrightError
            message = "浏览器操作失败，请检查浏览器与网络" if isinstance(error, PlaywrightError) else redact(str(error), self.secrets)
            self.failed.emit(message)
        finally:
            try:
                # A stop dialog can still be open when the very last person finishes.
                # Keep that result page until the user chooses Stop or Cancel.
                while (self.control.paused.is_set() and not self.control.immediate.is_set()
                       and not self.control.after_current.is_set() and not self._closing):
                    try:
                        await asyncio.sleep(0.05)
                    except asyncio.CancelledError:
                        break
                stopped = (self.control.immediate.is_set() or self.control.after_current.is_set()) and not self._closing
                if stopped:
                    self.retained_sessions.extend(self.sessions)
                    self.sessions.clear()
                    if self.batch_browser:
                        self.retained_browsers.append(self.batch_browser)
                        self.batch_browser = None
                    if exam_server:
                        self.retained_servers.append(exam_server)
                        exam_server = None
                    if kind == "preview":
                        if self.preview_browser:
                            self.retained_browsers.append(self.preview_browser)
                            self.preview_browser = None
                        if self.preview_server:
                            self.retained_servers.append(self.preview_server)
                            self.preview_server = None
                elif kind == "login" and test_questions:
                    closing = asyncio.create_task(self._close_sessions())
                    while not closing.done():
                        try:
                            await asyncio.shield(closing)
                        except asyncio.CancelledError:
                            continue
                    closing.result()
                if exam_server:
                    cleanup = asyncio.create_task(asyncio.to_thread(exam_server.close))
                    # A late Stop must not skip completion signals or abandon the server.
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            continue
                    cleanup.result()
            finally:
                if runner:
                    self.batch_finished.emit(runner.counts())
                self.secrets = ()
                self._entered = False
                self.job_finished.emit()

    def _progress(self, message):
        self.progress.emit(redact(message, self.secrets))

    def pause_queue(self):
        self.control.paused.set()

    def resume_queue(self):
        self.control.paused.clear()

    def stop_after_current(self):
        self.control.after_current.set()
        self.control.paused.clear()

    def stop_job(self):
        self.control.immediate.set()
        self.control.paused.clear()
        def cancel():
            # Save the pending queue even if Stop arrives before the coroutine starts.
            if self.task and not self.task.done() and self._entered:
                self.task.cancel()
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(cancel)

    def shutdown(self):
        self._closing = True
        if self.loop and not self.loop.is_closed() and hasattr(self, "exit_event"):
            self.loop.call_soon_threadsafe(self.exit_event.set)
