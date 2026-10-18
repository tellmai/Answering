import os
import time
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from webassistant.gui import MainWindow
from webassistant.models import Candidate, IdentityImport


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    # The organization/application overload always uses NativeFormat on Windows.
    # Supply an explicit INI instance so tests cannot change real user preferences.
    monkeypatch.setattr("webassistant.gui.QSettings", lambda *args: QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))


def wait_until(app, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert condition()


def test_invalid_import_clears_old_identity_and_duplicate_start_is_blocked(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
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


def test_mock_switch_requires_bank_previews_and_logs_score(tmp_path, monkeypatch, sample_bank):
    from webassistant.mock_exam import ExamScore
    from webassistant.models import BankImport
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        wait_until(app, lambda: window.backend_ready)
        window.test_mode.setChecked(True)
        assert not window.test_mode.isChecked()
        assert "100" in window.status.text()
        assert not window.fastest.isChecked()
        assert window.minutes_min.value() == window.minutes_max.value() == 10
        window.fastest.setChecked(True)
        assert not window.minutes_min.isEnabled() and not window.minutes_max.isEnabled()
        candidate = Candidate(2, "黔江区", "演示甲", "110101199001010010")
        window.identities_loaded(IdentityImport([candidate], [], "Sheet1"))
        window.bank_loaded(BankImport(sample_bank, [], 1))
        calls = []
        monkeypatch.setattr(window.backend, "start_job", lambda *args: calls.append(args))
        window.test_mode.setChecked(True)
        assert calls[0][0] == "preview" and calls[0][5] == sample_bank
        assert not window.test_mode.isEnabled()
        window._job_finished()
        window.output_root.setText(str(tmp_path))
        window.minimum_score.setValue(99)
        window.score_max.setValue(99)
        window._start_job("login")
        assert "至少需要两个" in window.status.text() and not window.busy
        window.minimum_score.setValue(90)
        window._start_job("login")
        assert calls[-1][5] == sample_bank
        result = ExamScore(98, 98, 100, 100, (), elapsed_seconds=600)
        window._score_ready(candidate, result)
        assert "演示甲" in window.log.toPlainText() and "98/100" in window.log.toPlainText()
        assert candidate.id_number not in window.log.toPlainText()
        window._job_finished()
    finally:
        window.close()
        wait_until(app, lambda: not window.backend.isRunning())
        app.processEvents()
