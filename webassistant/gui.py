import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from .controller import BrowserWorker
from .importers import load_bank, load_identities
from .models import DEFAULT_URL, DEFAULT_SCORE_MIN, DEFAULT_SCORE_MAX, SiteConfig, PracticeOptions, ExecutionOptions
from .privacy import redact
from .batch import LABELS, status_text
from .mock_exam import validate_bank
from . import APP_NAME
from .settings import load_settings


class ImportWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, kind, path, parent=None):
        super().__init__(parent)
        self.kind, self.path = kind, path

    def run(self):
        try:
            def report(message):
                if self.isInterruptionRequested():
                    raise InterruptedError("已停止文件导入")
                self.progress.emit(message)
            result = load_identities(self.path) if self.kind == "identity" else load_bank(self.path, report)
            if self.isInterruptionRequested():
                raise InterruptedError("已停止文件导入")
            self.result.emit(result)
        except Exception as error:
            # Do not expose document cells or parser internals in an exception traceback.
            if isinstance(error, (ValueError, InterruptedError)):
                self.failed.emit(redact(str(error)))
            else:
                self.failed.emit("文件读取失败，请检查格式、访问权限和文件是否完整")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 1040)
        self.setMinimumSize(1100, 880)
        self.settings = load_settings()
        self.candidates, self.questions = [], []
        self.rejected = []
        self.batch_records = []
        self.result_folder = ""
        self.job_kind = ""
        self.identity_source, self.bank_source = "", ""
        self.import_workers = []
        self.busy = False
        self.backend_ready = False
        self._closing = False
        self._allow_close = False
        self._build_ui()
        self.backend = BrowserWorker(self)
        self.backend.ready.connect(self._ready)
        self.backend.progress.connect(self.show_status)
        self.backend.check_result.connect(self._check_result)
        self.backend.completed.connect(self._completed)
        self.backend.failed.connect(lambda message: self.show_status("失败：" + message))
        self.backend.stopped.connect(lambda: self.show_status("已停止自动操作，当前网页保留供检查；未完成的人员记为中止"))
        self.backend.batch_started.connect(self._batch_started)
        self.backend.person_updated.connect(self._person_updated)
        self.backend.batch_finished.connect(self._batch_finished)
        self.backend.score_ready.connect(self._score_ready)
        self.backend.job_finished.connect(self._job_finished)
        self.backend.finished.connect(self._backend_finished)
        self.backend.start()
        self._update_controls()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(5)
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size:20px;font-weight:600;color:#20334c")
        layout.addWidget(title)
        layout.addWidget(QLabel("逐人登录 · 本地 100 题模拟测试，每题 1 分 · 可设置用时、最低分和分数范围 · 停止时保留网页"))

        config = QGroupBox("网站与浏览器")
        form = QFormLayout(config)
        self.url = QLineEdit(self.settings.value("url", DEFAULT_URL))
        self.url.setObjectName("urlInput")
        form.addRow("网站入口", self.url)
        chrome_row = QHBoxLayout()
        self.chrome = QLineEdit(self.settings.value("chrome", ""))
        self.chrome.setPlaceholderText("留空：Chrome → Edge → 其他兼容浏览器 → 随附 Chrome")
        self.chrome.setObjectName("chromePath")
        self.chrome_button = QPushButton("选择浏览器")
        self.chrome_button.clicked.connect(self._select_chrome)
        chrome_row.addWidget(self.chrome, 1)
        chrome_row.addWidget(self.chrome_button)
        form.addRow("浏览器路径", chrome_row)
        output_row = QHBoxLayout()
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        self.output_root = QLineEdit(self.settings.value("output_root", str(base / "results")))
        self.output_button = QPushButton("选择结果目录")
        self.output_button.clicked.connect(self._select_output)
        output_row.addWidget(self.output_root, 1)
        output_row.addWidget(self.output_button)
        form.addRow("统计保存位置", output_row)
        execution_row = QHBoxLayout()
        self.concurrency = QSpinBox()
        self.concurrency.setObjectName("flowConcurrency")
        self.concurrency.setRange(1, 30)
        self.concurrency.setValue(self.settings.value("concurrency", 10, type=int))
        self.concurrency.setSuffix(" 人")
        self.concurrency.setToolTip("每个人从登录到本地模拟测试结束占一个名额，完成或失败后自动补位。")
        self.background = QCheckBox("后台执行（隐藏浏览器）")
        self.background.setObjectName("backgroundExecution")
        self.background.setChecked(self.settings.value("background", True, type=bool))
        self.background.setToolTip("本批次浏览器不显示窗口、不抢占前台；仍可查看成绩、日志和停止任务。预览试卷始终单独显示。")
        execution_row.addWidget(self.concurrency)
        execution_row.addWidget(self.background)
        execution_row.addStretch(1)
        execution_row.addWidget(QLabel("独立身份会话 · 完成后自动补位"))
        form.addRow("并发数量", execution_row)
        test_row = QHBoxLayout()
        self.test_mode = QCheckBox("测试开关：登录成功后进入本地 100 题模拟考试")
        self.test_mode.setObjectName("testModeSwitch")
        self.test_mode.setToolTip("只切换批次流程，不打开试卷。导入不少于 100 题且包含三种题型的题库后，可点击预览试卷；每人登录成功后重新抽题、作答并校验。")
        self.test_mode.toggled.connect(self._test_toggled)
        self.preview_test_button = QPushButton("重新生成 / 预览试卷")
        self.preview_test_button.clicked.connect(lambda: self._start_job("preview"))
        test_row.addWidget(self.test_mode, 1)
        test_row.addWidget(self.preview_test_button)
        form.addRow("本地模拟测试", test_row)
        time_row = QHBoxLayout()
        self.minutes_min, self.minutes_max = QDoubleSpinBox(), QDoubleSpinBox()
        for box, key in ((self.minutes_min, "minutes_min"), (self.minutes_max, "minutes_max")):
            box.setRange(1, 180)
            box.setDecimals(1)
            box.setSuffix(" 分钟")
            box.setValue(self.settings.value(key, 10.0, type=float))
            time_row.addWidget(box)
            if box is self.minutes_min:
                time_row.addWidget(QLabel("至"))
        self.fastest = QCheckBox("最快完成（仍进行两次答案校验）")
        self.fastest.setToolTip("跳过计时等待，仍复核全部答案两次，并核对提交后的实际评分；校验失败不会记为完成。")
        self.fastest.setChecked(self.settings.value("fastest", False, type=bool))
        self.fastest.toggled.connect(self._update_controls)
        time_row.addWidget(self.fastest)
        time_row.addStretch(1)
        time_row.addWidget(QLabel("从进入本地试卷计时，默认 10 分钟"))
        form.addRow("答题用时范围", time_row)
        score_row = QHBoxLayout()
        self.minimum_score, self.score_min, self.score_max = QSpinBox(), QSpinBox(), QSpinBox()
        for box, key, default in ((self.minimum_score, "minimum_score", 90), (self.score_min, "score_min", DEFAULT_SCORE_MIN), (self.score_max, "score_max", DEFAULT_SCORE_MAX)):
            box.setRange(0, 100)
            box.setSuffix(" 分")
            box.setValue(self.settings.value(key, default, type=int))
        score_row.addWidget(self.minimum_score)
        score_row.addWidget(QLabel("得分范围"))
        score_row.addWidget(self.score_min)
        score_row.addWidget(QLabel("至"))
        score_row.addWidget(self.score_max)
        score_row.addStretch(1)
        score_row.addWidget(QLabel("仅本地模拟；批量轮换分数，至少两种整数分"))
        form.addRow("确保最低分", score_row)
        layout.addWidget(config)

        splitter = self.info_splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("identityBankSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        left = QGroupBox("身份信息")
        ll = QVBoxLayout(left)
        self.identity_button = QPushButton("导入身份文件 (.xlsx)")
        self.identity_button.setObjectName("importIdentity")
        self.identity_button.clicked.connect(lambda: self._choose_file("identity"))
        ll.addWidget(self.identity_button)
        self.identity_summary = QLabel("尚未导入；表头：所在的区县、姓名、身份证号")
        self.identity_summary.setWordWrap(True)
        ll.addWidget(self.identity_summary)
        self.people = QTableWidget(0, 6)
        self.people.setHorizontalHeaderLabels(["行号", "区县", "姓名", "身份证号（脱敏）", "状态", "模拟得分"])
        self.people.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.people.setSelectionMode(QAbstractItemView.SingleSelection)
        self.people.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.people.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.people.horizontalHeader().setStretchLastSection(True)
        self.people.verticalHeader().hide()
        self.people.itemSelectionChanged.connect(self._update_controls)
        ll.addWidget(self.people)
        splitter.addWidget(left)

        right = QGroupBox("题库搜索与预览")
        rl = QVBoxLayout(right)
        rl.setSpacing(4)
        self.bank_button = QPushButton("导入题库 (.pdf)")
        self.bank_button.setObjectName("importBank")
        self.bank_button.clicked.connect(lambda: self._choose_file("bank"))
        rl.addWidget(self.bank_button)
        self.bank_summary = QLabel("仅登录时题库可选；本地模拟测试需至少 100 题，包含三种题型")
        self.bank_summary.setWordWrap(True)
        rl.addWidget(self.bank_summary)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题号、题干或选项")
        self.search.textChanged.connect(self._filter_questions)
        self.kind = QComboBox()
        self.kind.addItems(["全部题型", "判断", "单选", "多选"])
        self.kind.currentTextChanged.connect(self._filter_questions)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.kind)
        rl.addLayout(search_row)
        self.question_list = QListWidget()
        self.question_list.setMinimumHeight(40)
        self.question_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.question_list.setMaximumHeight(140)
        self.question_list.currentRowChanged.connect(self._preview_question)
        rl.addWidget(self.question_list)
        self.preview = QPlainTextEdit()
        self.preview.setMinimumHeight(50)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("导入题库后，可在这里查看题干、选项、正确答案和来源页码")
        rl.addWidget(self.preview)
        splitter.addWidget(right)
        splitter.setSizes([520, 620])
        self.content_splitter = QSplitter(Qt.Vertical)
        self.content_splitter.setObjectName("informationResultsSplitter")
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(8)
        self.content_splitter.addWidget(splitter)

        progress_box = QGroupBox("批量处理进度与本地模拟成绩")
        pl = QVBoxLayout(progress_box)
        self.current_person = QLabel("当前人员：尚未开始")
        self.current_person.setTextFormat(Qt.PlainText)
        self.batch_counts = QLabel("已完成 0 · 失败/中止 0 · 未开始 0")
        pl.addWidget(self.current_person)
        pl.addWidget(self.batch_counts)
        self.result_tabs = QTabWidget()
        self.result_tables = {}
        for key, label in (("running", "正在进行"), ("completed", "登录验证完成"), ("failed", "失败 / 中止")):
            table = QTableWidget(0, 6)
            table.setHorizontalHeaderLabels(["行号", "区县", "姓名", "身份证号（脱敏）", "模拟得分", "结果 / 原因"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().hide()
            self.result_tables[key] = table
            self.result_tabs.addTab(table, label)
        self.result_tabs.setMinimumHeight(90)
        pl.addWidget(self.result_tabs)
        result_row = QHBoxLayout()
        self.result_location = QLabel("每批次独立保存已完成、未完成两份 Excel；统计文件含完整身份信息")
        self.result_location.setTextFormat(Qt.PlainText)
        self.result_location.setWordWrap(True)
        self.open_results_button = QPushButton("打开本批次目录")
        self.open_results_button.clicked.connect(self._open_results)
        self.export_button = QPushButton("重新导出结果")
        self.export_button.clicked.connect(lambda: self._start_job("export"))
        result_row.addWidget(self.result_location, 1)
        result_row.addWidget(self.open_results_button)
        result_row.addWidget(self.export_button)
        pl.addLayout(result_row)
        self.content_splitter.addWidget(progress_box)
        self.content_splitter.setSizes([320, 220])
        self.content_splitter.setStretchFactor(0, 3)
        self.content_splitter.setStretchFactor(1, 2)
        for pane, key in ((self.info_splitter, "layout/infoSplitter"), (self.content_splitter, "layout/contentSplitter")):
            state = self.settings.value(key)
            if isinstance(state, QByteArray):
                pane.restoreState(state)
            pane.setChildrenCollapsible(False)
            for index in range(pane.count()):
                pane.setCollapsible(index, False)
            pane.splitterMoved.connect(lambda _pos, _index, pane=pane, key=key: self.settings.setValue(key, pane.saveState()))
        layout.addWidget(self.content_splitter, 1)

        buttons = QHBoxLayout()
        self.inspect_button = QPushButton("环境检测")
        self.inspect_button.setObjectName("inspectEnvironment")
        self.start_button = QPushButton("启动批量登录")
        self.start_button.setObjectName("startLogin")
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stopLogin")
        self.inspect_button.clicked.connect(lambda: self._start_job("inspect"))
        self.start_button.clicked.connect(lambda: self._start_job("login"))
        self.stop_button.clicked.connect(self._stop)
        for button in (self.inspect_button, self.start_button, self.stop_button):
            button.setMinimumHeight(36)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QLabel("正在初始化浏览器控制模块…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("运行记录与导入问题"))
        open_log = QPushButton("打开日志目录")
        open_log.clicked.connect(self._open_log_dir)
        log_header.addWidget(open_log)
        layout.addLayout(log_header)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMaximumHeight(70)
        layout.addWidget(self.log)
        self.setStyleSheet("""
            QMainWindow { background: #f4f6fa; }
            QGroupBox { font-weight: 600; border: 1px solid #d8dfeb; border-radius: 6px; margin-top: 12px; padding: 12px 8px 8px; background: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; }
            QPushButton { padding: 6px 12px; border: 1px solid #bcc9da; border-radius: 4px; background: #f5f8fc; }
            QPushButton:hover { background: #e6eef8; }
            QPushButton:disabled { color: #8995a5; background: #eff1f5; }
            QPushButton#startLogin { background: #2464ac; color: white; border: none; }
            QPushButton#startLogin:disabled { background: #a9bdd4; }
            QSplitter::handle { background: #d8dfeb; border-radius: 3px; }
            QSplitter::handle:hover { background: #8faacb; }
            QLineEdit, QPlainTextEdit, QListWidget, QTableWidget { background: white; padding: 4px; border: 1px solid #ccd5e0; border-radius: 3px; }
        """)
        self._sync_information_height()

    def _sync_information_height(self):
        # Splitter minimums must follow wrapped summaries after data is imported.
        self.info_splitter.setMinimumHeight(self.info_splitter.minimumSizeHint().height())
        self.setMinimumHeight(max(880, self.centralWidget().minimumSizeHint().height()))

    def _ready(self):
        self.backend_ready = True
        self.show_status("就绪：导入身份文件后启动批量登录；也可先检测环境")
        self._update_controls()

    def _backend_finished(self):
        self.backend_ready = False
        self.busy = False
        self._update_controls()

    def _update_controls(self):
        if not hasattr(self, "start_button"):
            return
        locked = self.busy or bool(self.import_workers) or self._closing
        self.start_button.setEnabled(self.backend_ready and not locked and bool(self.candidates or self.rejected))
        self.inspect_button.setEnabled(self.backend_ready and not locked)
        self.stop_button.setEnabled((self.busy or bool(self.import_workers)) and not self._closing)
        self.export_button.setEnabled(self.backend_ready and not locked and bool(self.result_folder))
        self.preview_test_button.setEnabled(self.backend_ready and not locked)
        self.start_button.setText("启动登录与本地模拟测试" if self.test_mode.isChecked() else "启动批量登录")
        self.open_results_button.setEnabled(bool(self.result_folder))
        for widget in (self.url, self.chrome, self.chrome_button, self.output_root, self.output_button, self.identity_button, self.bank_button, self.test_mode, self.fastest, self.minimum_score, self.score_min, self.score_max, self.concurrency, self.background):
            widget.setEnabled(not locked)
        self.minutes_min.setEnabled(not locked and not self.fastest.isChecked())
        self.minutes_max.setEnabled(not locked and not self.fastest.isChecked())

    def show_status(self, message):
        message = redact(message)
        self.status.setText(message)
        self.log.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")
        logging.getLogger("assistant").info(message)

    def _select_chrome(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择兼容的浏览器程序（Chrome / Edge 等）", "", "程序 (*.exe)")
        if path:
            self.chrome.setText(path)

    def _select_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择统计结果保存目录", self.output_root.text())
        if path:
            self.output_root.setText(path)

    def _test_toggled(self, enabled):
        if enabled:
            self.show_status("测试开关已开启：启动批次后执行本地模拟测试；点击「预览试卷」可单独查看")
        else:
            self.show_status("测试开关已关闭：后续启动仅验证登录")
        self._update_controls()

    def _choose_file(self, kind):
        suffix = "Excel (*.xlsx)" if kind == "identity" else "PDF (*.pdf)"
        path, _ = QFileDialog.getOpenFileName(self, "导入身份信息" if kind == "identity" else "导入题库", "", suffix)
        if path:
            self.import_file(kind, path)

    def import_file(self, kind, path):
        worker = ImportWorker(kind, path, self)
        self.import_workers.append(worker)
        worker.progress.connect(self.show_status)
        worker.failed.connect(lambda message: self.show_status("导入：" + message))
        worker.failed.connect(lambda _: (self.identity_summary if kind == "identity" else self.bank_summary).setText("导入失败或已停止，请查看运行记录后重新导入"))
        if kind == "identity":
            self.identity_source = path
            # Invalidate the previous selection immediately, even when the new import fails.
            self.candidates = []
            self.rejected = []
            self.people.setRowCount(0)
            self.identity_summary.setText("正在导入身份文件…")
            worker.result.connect(lambda result: self.identities_loaded(result, Path(path).name))
        else:
            self.bank_source = path
            self.questions = []
            self._filter_questions()
            self.bank_summary.setText("正在解析 PDF…")
            worker.result.connect(lambda result: self.bank_loaded(result, Path(path).name))
        worker.finished.connect(lambda: self._import_finished(worker))
        self._update_controls()
        worker.start()

    def _import_finished(self, worker):
        self.import_workers.remove(worker)
        worker.deleteLater()
        self._update_controls()

    def identities_loaded(self, result, filename=""):
        self.candidates = result.candidates
        self.rejected = result.rejected
        rows = sorted([(c, "未开始") for c in self.candidates] + [(c, "导入校验失败") for c, _ in self.rejected], key=lambda pair: pair[0].row)
        self.people.setRowCount(len(rows))
        self.source_rows = {c.row: row for row, (c, _) in enumerate(rows)}
        for row, (c, state) in enumerate(rows):
            for column, value in enumerate((str(c.row), c.district, c.name, c.masked_id, state, "—")):
                self.people.setItem(row, column, QTableWidgetItem(value))
        if self.candidates:
            self.people.selectRow(0)
        self.identity_summary.setText(f"{filename} · {result.sheet} · 有效 {len(self.candidates)} 人 · 问题 {len(result.issues)} 行")
        self.show_status(f"身份导入完成：有效 {len(self.candidates)} 人，问题 {len(result.issues)} 行")
        for issue in result.issues:
            self.show_status(issue)
        self._sync_information_height()
        self._update_controls()

    def bank_loaded(self, result, filename=""):
        self.questions = result.questions
        counts = Counter(q.kind for q in self.questions)
        self.bank_summary.setText(f"{filename} · {result.page_count} 页 · {len(self.questions)} 题\n判断 {counts['判断']} / 单选 {counts['单选']} / 多选 {counts['多选']} · 异常 {len(result.issues)}")
        self._filter_questions()
        self.show_status(f"题库解析完成：{len(self.questions)} 题，异常 {len(result.issues)} 题")
        for issue in result.issues:
            self.show_status(issue)
        self._sync_information_height()

    def _filter_questions(self):
        keyword = self.search.text().strip().casefold()
        kind = self.kind.currentText()
        self.filtered_questions = [q for q in self.questions if
            (kind == "全部题型" or q.kind == kind) and
            (not keyword or keyword in (str(q.number) + " " + q.stem + " " + " ".join(q.options.values())).casefold())]
        self.question_list.clear()
        for q in self.filtered_questions:
            self.question_list.addItem(f"{q.number} · {q.kind} · {q.stem.replace(chr(10), '')[:65]}")
        if self.filtered_questions:
            self.question_list.setCurrentRow(0)
        else:
            self.preview.clear()

    def _preview_question(self, row):
        if row < 0 or row >= len(self.filtered_questions):
            return
        q = self.filtered_questions[row]
        text = f"第 {q.number} 题 · {q.kind}\n来源：第 {', '.join(map(str, q.pages))} 页\n\n{q.stem}\n\n"
        text += "\n\n".join(f"{key}. {value}" for key, value in q.options.items())
        text += "\n\n正确答案：" + "、".join(q.answers)
        self.preview.setPlainText(text)

    def _start_job(self, kind):
        if self.busy or self.import_workers or not self.backend_ready or self._closing:
            return
        if kind == "export":
            if not self.result_folder:
                return
            self.busy, self.job_kind = True, kind
            self._update_controls()
            self.backend.start_job("export", None)
            return
        try:
            config = SiteConfig(self.url.text().strip(), self.chrome.text().strip())
            config.validate()
            testing = kind == "preview" or (self.test_mode.isChecked() and kind == "login")
            options = PracticeOptions(self.fastest.isChecked(), self.minutes_min.value(), self.minutes_max.value(), self.minimum_score.value(), self.score_min.value(), self.score_max.value())
            execution = ExecutionOptions(self.concurrency.value(), self.background.isChecked())
            execution.validate()
            if testing:
                options.validate()
                validate_bank(self.questions)
            candidate = self.candidates[0] if self.candidates else None
            if kind == "login" and not (self.candidates or self.rejected):
                raise ValueError("请先导入身份文件")
            if kind == "login" and not self.output_root.text().strip():
                raise ValueError("请选择统计保存目录")
        except ValueError as error:
            self.show_status(str(error))
            return
        self.settings.setValue("url", config.url)
        self.settings.setValue("chrome", config.chrome_path)
        self.settings.setValue("output_root", self.output_root.text().strip())
        for key, value in vars(options).items():
            self.settings.setValue(key, value)
        for key, value in vars(execution).items():
            self.settings.setValue(key, value)
        self.busy = True
        self.job_kind = kind
        self.inspection_issues = []
        self._update_controls()
        if kind == "inspect":
            self._check_result("身份文件", candidate is not None, "身份记录已校验" if candidate else "无有效身份，仍可检测浏览器与网站")
            for label, source in (("身份源文件", self.identity_source), ("题库源文件", self.bank_source)):
                if source:
                    try:
                        with open(source, "rb") as stream:
                            stream.read(1)
                        self._check_result(label, True, "文件可读取")
                    except OSError:
                        self._check_result(label, False, "文件已移动、删除或无法读取")
            self.show_status("正在检查模型、浏览器和公开登录页面；不会提交身份信息")
        self.backend.start_job(kind, config, list(self.candidates), self.output_root.text().strip(), list(self.rejected), list(self.questions) if testing else [], options, execution)

    def _check_result(self, label, ok, detail):
        if not ok and label != "身份文件":
            self.inspection_issues.append(label)
        self.show_status(f"{'通过' if ok else '提示/未通过'} · {label}：{detail}")

    def _completed(self, message):
        if message == "环境检测通过" and self.inspection_issues:
            message = "网站与 OCR 检测通过；源文件检测存在问题，请查看运行记录"
        self.show_status(message)

    def _stop(self):
        if self.busy and self.job_kind == "login":
            self.backend.pause_queue()
            try:
                choice = self._ask_stop_choice()
                if choice == "after":
                    self.backend.stop_after_current()
                    self.show_status("已请求所有正在执行的人员完成后停止；不会接入新人员")
                elif choice == "now":
                    self._stop_now()
            finally:
                self.backend.resume_queue()
        else:
            self._stop_now()

    def _ask_stop_choice(self):
        box = QMessageBox(self)
        box.setWindowTitle("停止批量登录")
        box.setText("是否等待所有正在执行的人员完成后停止？")
        box.setInformativeText("等待完成会让所有正在执行的人员完成全部步骤（含本地模拟测试）。确认期间不接入新人员；直接停止会中止所有当前任务。")
        after = box.addButton("是，等待测试完成后停止", QMessageBox.YesRole)
        direct = box.addButton("否，直接停止", QMessageBox.NoRole)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return "after" if box.clickedButton() == after else "now" if box.clickedButton() == direct else "cancel"

    def _stop_now(self):
        for worker in self.import_workers:
            worker.requestInterruption()
        if self.busy:
            self.backend.stop_job()
        self.show_status("正在停止，请稍候…")

    def _job_finished(self):
        self.busy = False
        self.job_kind = ""
        self._update_controls()

    def _batch_started(self, folder, records):
        self.result_folder = folder
        self.batch_records = list(records)
        self.result_tabs.setTabText(1, "已完成（含模拟成绩）" if self.test_mode.isChecked() else "登录验证完成")
        self.result_location.setText(folder)
        self._render_batch()
        self._update_controls()

    def _person_updated(self, record):
        self.batch_records[record.index] = record
        self._render_batch()

    def _render_batch(self):
        counts = Counter(r.status for r in self.batch_records)
        self.batch_counts.setText(f"已完成 {counts['completed']} · 失败/中止 {counts['failed'] + counts['stopped']} · 正在进行 {counts['running']} · 未开始 {counts['pending']}")
        active = [r for r in self.batch_records if r.status == "running"]
        summary = "；".join(f"第 {r.candidate.row} 行 · {r.candidate.name} · {status_text(r)}" for r in active[:3])
        if len(active) > 3:
            summary += f"；另 {len(active) - 3} 人（见正在进行列表）"
        self.current_person.setText(f"正在执行 {len(active)} 人：{summary}" if active else "当前人员：无（等待开始或本批次已结束）")
        for key, table in self.result_tables.items():
            records = [r for r in self.batch_records if r.status == key or (key == "failed" and r.status == "stopped")]
            table.setRowCount(len(records))
            for row, r in enumerate(records):
                c = r.candidate
                score = f"{r.score} / {r.maximum}" if r.score is not None else "—"
                for column, value in enumerate((str(c.row), c.district, c.name, c.masked_id, score, r.reason or status_text(r))):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    table.setItem(row, column, item)
        for r in self.batch_records:
            row = getattr(self, "source_rows", {}).get(r.candidate.row)
            if row is not None:
                self.people.setItem(row, 4, QTableWidgetItem(status_text(r)))
                self.people.setItem(row, 5, QTableWidgetItem(f"{r.score} / {r.maximum}" if r.score is not None else "—"))

    def _score_ready(self, candidate, result):
        self.show_status(f"本地模拟测试成绩 · 第 {candidate.row} 行 · {candidate.name} · {candidate.district} · {candidate.masked_id} · 得分 {result.score}/{result.maximum} 分 · 答对 {result.correct}/{result.total} 题 · 实际用时 {result.elapsed_seconds / 60:.2f} 分钟")

    def _batch_finished(self, counts):
        self._render_batch()
        if counts["completed"]:
            self.result_tabs.setCurrentIndex(1)
        elif counts["failed"] or counts["stopped"]:
            self.result_tabs.setCurrentIndex(2)

    def _open_results(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if self.result_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result_folder))

    def _open_log_dir(self):
        from PySide6.QtCore import QStandardPaths, QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)))

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        self._closing = True
        self._stop_now()
        self.backend.shutdown()
        self._update_controls()
        self.show_status("正在关闭软件创建的浏览器并释放资源…")
        self.close_timer = QTimer(self)
        self.close_timer.timeout.connect(self._finish_close)
        self.close_timer.start(100)

    def _finish_close(self):
        if not self.backend.isRunning() and not self.import_workers:
            self.close_timer.stop()
            self._allow_close = True
            self.close()
