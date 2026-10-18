import argparse
import asyncio
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Windows 网页自动登录助手")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help="离线自检并保存界面截图，不提交真实登录")
    args = parser.parse_args()
    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QStandardPaths, QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    app.setApplicationName("WebLoginAssistant")
    app.setOrganizationName("LocalTools")
    family = "Microsoft YaHei UI"
    # The offscreen Qt platform cannot always discover Windows system fonts.
    # Load from the OS, without bundling or redistributing Microsoft font files.
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("msyh.ttc", "msyhbd.ttc", "simsun.ttc", "segoeui.ttf"):
        font_path = fonts / filename
        if font_path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                family = families[0]
                break
    app.setFont(QFont(family, 10))
    from webassistant.gui import MainWindow
    from webassistant.privacy import redact
    log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    log_dir.mkdir(parents=True, exist_ok=True)
    class PrivateFormatter(logging.Formatter):
        def format(self, record):
            return redact(super().format(record))
    handler = RotatingFileHandler(log_dir / "assistant.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(PrivateFormatter("%(asctime)s %(message)s"))
    logger = logging.getLogger("assistant")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    window = MainWindow()
    window.show()
    smoke_result = {}
    if args.smoke_test:
        output = Path(args.smoke_test).resolve()
        output.mkdir(parents=True, exist_ok=True)
        def smoke():
            try:
                from webassistant.models import BankImport, Candidate, IdentityImport, Question, PracticeOptions, ExamRunPlan
                from webassistant.browser import find_chrome
                from webassistant.browsers import launch_browser
                from webassistant.batch import BatchStore
                from webassistant.mock_exam import MockExamServer, answer_mock_exam, validate_bank
                from webassistant.ocr import LocalOCR
                import openpyxl
                import pdfplumber
                window.identities_loaded(IdentityImport([Candidate(2, "黔江区", "演示考生", "110101199001010010")], [], "Sheet1"), "演示身份.xlsx")
                questions = []
                for n in range(1, 151):
                    kind = ("单选", "多选", "判断")[(n - 1) % 3]
                    options = {"A": "正确", "B": "错误"} if kind == "判断" else {"A": "演示选项甲", "B": "演示选项乙", "C": "演示选项丙"}
                    answers = ("A", "C") if kind == "多选" else ("A",)
                    questions.append(Question(n, kind, f"第 {n} 道本地界面演示题。", options, answers, (1,)))
                window.bank_loaded(BankImport(questions, [], 1), "演示题库.pdf")
                window.test_mode.blockSignals(True)
                window.test_mode.setChecked(True)
                window.test_mode.blockSignals(False)
                window.fastest.setChecked(True)
                store = BatchStore(output / "statistics", window.candidates, stage="离线本地模拟自检")
                store.practice_settings = vars(PracticeOptions(fastest=True))
                store.persist()
                window._batch_started(str(store.folder), list(store.records))
                window.output_root.setText(str(output / "statistics"))
                local = LocalOCR()
                try:
                    asyncio.run(local.check())
                finally:
                    local.close()
                async def check_browser():
                    from playwright.async_api import async_playwright
                    async with async_playwright() as playwright:
                        browser, _ = await launch_browser(playwright, headless=True)
                        server = MockExamServer()
                        try:
                            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                            await page.set_content("<h1>offline-check</h1>")
                            assert await page.locator('h1').inner_text() == 'offline-check'
                            plan = ExamRunPlan(94, 0, 90, 91, 99)
                            paper = server.create_paper(window.candidates[0], questions, plan=plan)
                            await page.goto(server.url_for(paper))
                            await page.locator("article.question").nth(99).wait_for()
                            await page.screenshot(path=str(output / "模拟试卷预览.png"))
                            result = await answer_mock_exam(page, server, paper, validate_bank(questions), plan=plan)
                            assert result.score == 94 and result.total == 100
                            await page.screenshot(path=str(output / "模拟提交结果.png"))
                            return result
                        finally:
                            await browser.close()
                            await asyncio.to_thread(server.close)
                result = asyncio.run(check_browser())
                record = store.update(0, status="completed", stage="本地模拟测试", reason="离线模拟自检：已作答、两次复核并评分", score=result.score, maximum=result.maximum, correct=result.correct, total=result.total, elapsed_seconds=result.elapsed_seconds, planned_seconds=result.planned_seconds, target_score=result.target_score)
                window._person_updated(record)
                window._score_ready(window.candidates[0], result)
                window.result_tabs.setCurrentIndex(1)
                assert (store.folder / "已完成信息统计.xlsx").is_file()
                app.processEvents()
                window.grab().save(str(output / "界面预览.png"))
                smoke_result.update(ok=window.backend_ready, gui=True, ocr_model=True, chrome_detected=bool(find_chrome()), chrome_launch=True, batch_statistics=True, mock_exam=True, mock_score=result.score, mock_target=result.target_score, mock_total=result.total, document_libraries=True, browser_backend=window.backend_ready, frozen=bool(getattr(sys, "frozen", False)), python_version=sys.version.split()[0])
            except Exception as error:
                smoke_result.update(ok=False, error=type(error).__name__)
            (output / "result.json").write_text(json.dumps(smoke_result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()
        def wait_ready():
            if window.backend_ready or not window.backend.isRunning():
                smoke()
            else:
                QTimer.singleShot(100, wait_ready)
        QTimer.singleShot(300, wait_ready)
    exit_code = app.exec()
    return (0 if smoke_result.get("ok") else 1) if args.smoke_test else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
