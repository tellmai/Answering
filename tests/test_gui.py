import os
import time
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings, Qt
from webassistant.gui import MainWindow
from webassistant.models import Candidate, IdentityImport


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    # The organization/application overload always uses NativeFormat on Windows.
    # Supply an explicit INI instance so tests cannot change real user preferences.
    monkeypatch.setattr("webassistant.settings.QSettings", lambda organization, application: QSettings(str(tmp_path / f"{application}.ini"), QSettings.IniFormat))


def wait_until(app, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert condition()


@pytest.fixture
def chinese_ui_font():
    from pathlib import Path
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication.instance() or QApplication([])
    original = app.font()
    font_id = QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"))
    app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
    try:
        yield
    finally:
        app.setFont(original)


def test_invalid_import_clears_old_identity_and_duplicate_start_is_blocked(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        assert window.windowTitle() == "Answering"
        wait_until(app, lambda: window.backend_ready)
        candidate = Candidate(2, "黔江区", "演示考生", "110101199001010010")
        window.identities_loaded(IdentityImport([candidate], [], "Sheet1"))
        assert window.start_button.isEnabled()
        calls = []
        window.backend.start_job = lambda *args: calls.append(args)
        window._start_job("login")
        window._start_job("login")
        assert len(calls) == 1
        assert not window.start_button.isEnabled()
        window._job_finished()
        window.import_file("identity", str(tmp_path / "missing.xlsx"))
        assert not window.candidates
        assert not window.start_button.isEnabled()
        wait_until(app, lambda: not window.import_workers)
        assert not window.start_button.isEnabled()
        assert "导入失败" in window.identity_summary.text()
    finally:
        window.close()
        wait_until(app, lambda: not window.backend.isRunning() and not window.import_workers)
        app.processEvents()


@pytest.mark.parametrize("choice,expected", [("after", ["pause", "after", "resume"]), ("now", ["pause", "now", "resume"]), ("cancel", ["pause", "resume"])])
def test_stop_choices_and_batch_views(tmp_path, monkeypatch, choice, expected):
    from webassistant.batch import BatchStore
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        wait_until(app, lambda: window.backend_ready)
        records = [Candidate(2, "黔江区", "演示甲", "110101199001010010"), Candidate(3, "黔江区", "演示乙", "110101199001010011")]
        window.identities_loaded(IdentityImport(records, [], "Sheet1"))
        calls = []
        monkeypatch.setattr(window.backend, "start_job", lambda *args: calls.append(args))
        window.output_root.setText(str(tmp_path))
        window._start_job("login")
        assert calls[0][2] == records
        store = BatchStore(tmp_path, records)
        store.persist()
        window._batch_started(str(store.folder), list(store.records))
        window._person_updated(store.update(0, status="running"))
        assert "演示甲" in window.current_person.text()
        window._person_updated(store.update(0, status="completed"))
        window._person_updated(store.update(1, status="failed", reason="演示失败"))
        assert window.result_tables["completed"].rowCount() == 1
        assert window.result_tables["failed"].rowCount() == 1
        controls = []
        for method, label in (("pause_queue", "pause"), ("stop_after_current", "after"), ("stop_job", "now"), ("resume_queue", "resume")):
            monkeypatch.setattr(window.backend, method, lambda value=label: controls.append(value))
        monkeypatch.setattr(window, "_ask_stop_choice", lambda: choice)
        window._stop()
        assert controls == expected
        window._job_finished()
    finally:
        window.close()
        wait_until(app, lambda: not window.backend.isRunning())
        app.processEvents()


def test_execution_options_are_snapshotted_while_views_stay_usable(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        wait_until(app, lambda: window.backend_ready)
        assert window.concurrency.value() == 10 and window.background.isChecked()
        window.concurrency.setValue(4)
        window.output_root.setText(str(tmp_path))
        window.identities_loaded(IdentityImport([Candidate(2, "黔江区", "演示甲", "110101199001010010")], [], "Sheet1"))
        calls = []
        monkeypatch.setattr(window.backend, "start_job", lambda *args: calls.append(args))
        window._start_job("login")
        assert calls[0][7].concurrency == 4 and calls[0][7].background
        assert window.settings.value("concurrency", type=int) == 4
        assert window.people.isEnabled() and window.result_tabs.isEnabled() and window.log.isEnabled()
        assert not window.concurrency.isEnabled() and not window.background.isEnabled()
        window._job_finished()
        assert window.concurrency.isEnabled() and window.background.isEnabled()
    finally:
        window.close()
        wait_until(app, lambda: not window.backend.isRunning())
        app.processEvents()


def test_test_switch_is_passive_and_preview_is_explicit(tmp_path, monkeypatch, sample_bank):
    from webassistant.mock_exam import ExamScore
    from webassistant.models import BankImport
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        wait_until(app, lambda: window.backend_ready)
        calls = []
        monkeypatch.setattr(window.backend, "start_job", lambda *args: calls.append(args))
        window.test_mode.setChecked(True)
        assert window.test_mode.isChecked() and not window.busy and not calls
        window.preview_test_button.click()
        assert "100" in window.status.text() and not calls and not window.busy
        assert (window.score_min.value(), window.score_max.value()) == (98, 100)
        assert "两次答案校验" in window.fastest.text()
        assert "不额外等待" not in window.fastest.text()
        assert not window.fastest.isChecked()
        assert window.minutes_min.value() == window.minutes_max.value() == 10
        window.fastest.setChecked(True)
        assert not window.minutes_min.isEnabled() and not window.minutes_max.isEnabled()
        candidate = Candidate(2, "黔江区", "演示甲", "110101199001010010")
        window.identities_loaded(IdentityImport([candidate], [], "Sheet1"))
        window.bank_loaded(BankImport(sample_bank, [], 1))
        assert not calls
        window.preview_test_button.click()
        assert calls[0][0] == "preview" and calls[0][5] == sample_bank
        assert not window.test_mode.isEnabled()
        window._job_finished()
        window.test_mode.setChecked(False)
        assert len(calls) == 1 and window.preview_test_button.isEnabled()
        window.preview_test_button.click()
        assert calls[1][0] == "preview" and calls[1][5] == sample_bank
        assert not window.test_mode.isChecked()
        window._job_finished()
        window.test_mode.setChecked(True)
        assert len(calls) == 2
        window.output_root.setText(str(tmp_path))
        window.minimum_score.setValue(99)
        window.score_max.setValue(99)
        window._start_job("login")
        assert "至少需要两个" in window.status.text() and not window.busy
        window.minimum_score.setValue(90)
        window._start_job("login")
        assert calls[-1][0] == "login" and calls[-1][5] == sample_bank
        result = ExamScore(98, 98, 100, 100, (), elapsed_seconds=600)
        window._score_ready(candidate, result)
        assert "演示甲" in window.log.toPlainText() and "98/100" in window.log.toPlainText()
        assert candidate.id_number not in window.log.toPlainText()
        window._job_finished()
    finally:
        window.close()
        wait_until(app, lambda: not window.backend.isRunning())
        app.processEvents()


def test_splitter_resize_keeps_sections_visible_and_restores_proportions(sample_bank, chinese_ui_font):
    from webassistant.models import BankImport
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    try:
        wait_until(app, lambda: window.backend_ready)
        window.bank_loaded(BankImport(sample_bank, [], 1), "演示题库.pdf")
        app.processEvents()
        assert window.preview.y() + window.preview.height() <= window.preview.parentWidget().contentsRect().bottom()
        window.resize(1280, 1280)
        app.processEvents()
        assert window.content_splitter.orientation() == Qt.Vertical
        assert window.info_splitter.orientation() == Qt.Horizontal
        before = window.content_splitter.sizes()
        window.content_splitter.moveSplitter(before[0] + 65, 1)
        window.info_splitter.moveSplitter(620, 1)
        app.processEvents()
        sizes = window.content_splitter.sizes()
        info_sizes = window.info_splitter.sizes()
        assert sizes != before and min(sizes) > 0
        assert all(not window.content_splitter.isCollapsible(i) for i in range(2))
        assert window.result_tabs.maximumHeight() > 90
        assert window.settings.contains("layout/contentSplitter")
        assert window.settings.contains("layout/infoSplitter")
    finally:
        window.close()
        wait_until(app, lambda: not window.backend.isRunning())
        app.processEvents()
    restored = MainWindow()
    restored.resize(1280, 1280)
    restored.show()
    try:
        wait_until(app, lambda: restored.backend_ready)
        restored.bank_loaded(BankImport(sample_bank, [], 1), "演示题库.pdf")
        app.processEvents()
        assert all(abs(a - b) <= 2 for a, b in zip(sizes, restored.content_splitter.sizes()))
        assert all(abs(a - b) <= 2 for a, b in zip(info_sizes, restored.info_splitter.sizes()))
    finally:
        restored.close()
        wait_until(app, lambda: not restored.backend.isRunning())
        app.processEvents()
