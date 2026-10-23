import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTableWidgetItem, QTabWidget,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from .controller import BrowserWorker
from .importers import load_bank, load_identities
from .models import DEFAULT_URL, DEFAULT_SCORE_MIN, DEFAULT_SCORE_MAX, SiteConfig, PracticeOptions, ExecutionOptions
from .privacy import redact
from .batch import status_text
from .mock_exam import validate_bank
from . import APP_NAME, __version__
from .settings import load_settings
from .ui import ACCENT, STYLESHEET, ElidedLabel, EmptyTable, WorkspaceSplitter, icon


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
        self.setWindowIcon(icon("brand"))
        self.resize(1360, 920)
        self.setMinimumSize(1000, 700)
        if QApplication.platformName() != "offscreen":
            available = self.screen().availableGeometry()
            self.resize(min(1360, available.width() - 40), min(920, available.height() - 40))
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
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 16, 22, 16)
        layout.setSpacing(10)

        def label(text, role="muted"):
            widget = QLabel(text)
            widget.setProperty("role", role)
            return widget

        def card(title, count=None, action=None):
            frame = QFrame()
            frame.setProperty("role", "card")
            box = QVBoxLayout(frame)
            box.setContentsMargins(14, 12, 14, 12)
            box.setSpacing(8)
            heading = QHBoxLayout()
            heading.setSpacing(8)
            heading.addWidget(label(title, "heading"))
            if count is not None:
                heading.addWidget(count, 0, Qt.AlignVCenter)
            heading.addStretch(1)
            if action is not None:
                heading.addWidget(action)
            box.addLayout(heading)
            return frame, box

        header = QHBoxLayout()
        header.setSpacing(12)
        brand = QLabel()
        brand.setPixmap(icon("brand").pixmap(QSize(44, 44)))
        header.addWidget(brand)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("brandTitle")
        caption = QLabel("批量登录与本地模拟工作台")
        caption.setObjectName("brandCaption")
        brand_text.addWidget(title)
        brand_text.addWidget(caption)
        header.addLayout(brand_text)
        header.addStretch(1)
        version = QLabel(f"v{__version__}")
        version.setObjectName("versionBadge")
        header.addWidget(version, 0, Qt.AlignVCenter)
        self.activity_badge = QLabel("●  正在初始化")
        self.activity_badge.setObjectName("activityBadge")
        header.addWidget(self.activity_badge, 0, Qt.AlignVCenter)
        layout.addLayout(header)

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setObjectName("settingsTabs")
        self.settings_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        execution_page = QWidget()
        execution_layout = QHBoxLayout(execution_page)
        execution_layout.setContentsMargins(16, 12, 16, 12)
        execution_layout.setSpacing(20)
        run_options = QVBoxLayout()
        run_options.setSpacing(9)
        self.concurrency = QSpinBox()
        self.concurrency.setObjectName("flowConcurrency")
        self.concurrency.setRange(1, 30)
        self.concurrency.setValue(self.settings.value("concurrency", 10, type=int))
        self.concurrency.setSuffix(" 人")
        self.concurrency.setFixedWidth(95)
        self.concurrency.setAccessibleName("并发数量")
        self.concurrency.setToolTip("每个人从登录到本地模拟测试结束占一个名额，完成或失败后自动补位。")
        concurrent_row = QHBoxLayout()
        concurrent_row.addWidget(label("并发数量", "eyebrow"))
        concurrent_row.addStretch(1)
        concurrent_row.addWidget(self.concurrency)
        run_options.addLayout(concurrent_row)
        self.background = QCheckBox("后台执行（隐藏浏览器）")
        self.background.setObjectName("backgroundExecution")
        self.background.setChecked(self.settings.value("background", True, type=bool))
        self.background.setToolTip("本批次浏览器不显示窗口、不抢占前台；仍可查看成绩、日志和停止任务。预览试卷始终单独显示。")
        run_options.addWidget(self.background)
        run_options.addWidget(label("独立身份会话 · 完成后自动补位"))
        run_options.addStretch(1)
        execution_layout.addLayout(run_options, 1)
        divider = QFrame()
        divider.setObjectName("settingsDivider")
        divider.setFixedWidth(1)
        execution_layout.addWidget(divider)
        practice = QVBoxLayout()
        practice.setSpacing(8)
        self.test_mode = QCheckBox("测试开关 · 登录后进行本地模拟")
        self.test_mode.setObjectName("testModeSwitch")
        self.test_mode.setToolTip("只切换批次流程，不打开试卷。导入不少于 100 题且包含三种题型的题库后，可点击预览试卷；每人登录成功后重新抽题、作答并校验。")
        self.test_mode.toggled.connect(self._test_toggled)
        self.preview_test_button = QPushButton("预览试卷")
        self.preview_test_button.setIcon(icon("log", ACCENT))
        self.preview_test_button.setToolTip("重新抽取 100 题并打开预览；测试开关关闭时也可查看。")
        self.preview_test_button.clicked.connect(lambda: self._start_job("preview"))
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_mode)
        test_row.addStretch(1)
        test_row.addWidget(label("每卷 100 题 / 每题 1 分"))
        test_row.addWidget(self.preview_test_button)
        practice.addLayout(test_row)
        time_row = QHBoxLayout()
        time_row.setSpacing(8)
        time_row.addWidget(label("答题用时", "eyebrow"))
        self.minutes_min, self.minutes_max = QDoubleSpinBox(), QDoubleSpinBox()
        for box, key in ((self.minutes_min, "minutes_min"), (self.minutes_max, "minutes_max")):
            box.setRange(1, 180)
            box.setDecimals(1)
            box.setSuffix(" 分钟")
            box.setFixedWidth(109)
            box.setAccessibleName("最短答题用时" if key == "minutes_min" else "最长答题用时")
            box.setValue(self.settings.value(key, 10.0, type=float))
            time_row.addWidget(box)
            if box is self.minutes_min:
                time_row.addWidget(label("至"))
        self.fastest = QCheckBox("最快完成（仍进行两次答案校验）")
        self.fastest.setToolTip("跳过计时等待，仍复核全部答案两次，并核对提交后的实际评分；校验失败不会记为完成。")
        self.fastest.setChecked(self.settings.value("fastest", False, type=bool))
        self.fastest.toggled.connect(self._update_controls)
        time_row.addSpacing(8)
        time_row.addWidget(self.fastest)
        time_row.addStretch(1)
        practice.addLayout(time_row)
        score_row = QHBoxLayout()
        score_row.setSpacing(8)
        self.minimum_score, self.score_min, self.score_max = QSpinBox(), QSpinBox(), QSpinBox()
        for box, key, default in ((self.minimum_score, "minimum_score", 90), (self.score_min, "score_min", DEFAULT_SCORE_MIN), (self.score_max, "score_max", DEFAULT_SCORE_MAX)):
            box.setRange(0, 100)
            box.setSuffix(" 分")
            box.setFixedWidth(87)
            box.setValue(self.settings.value(key, default, type=int))
            box.setAccessibleName({"minimum_score": "确保最低分", "score_min": "得分下限", "score_max": "得分上限"}[key])
        score_row.addWidget(label("确保最低分", "eyebrow"))
        score_row.addWidget(self.minimum_score)
        score_row.addSpacing(8)
        score_row.addWidget(label("得分范围", "eyebrow"))
        score_row.addWidget(self.score_min)
        score_row.addWidget(label("至"))
        score_row.addWidget(self.score_max)
        score_row.addStretch(1)
        score_row.addWidget(label("仅用于本地模拟"))
        practice.addLayout(score_row)
        execution_layout.addLayout(practice, 3)
        self.settings_tabs.addTab(execution_page, icon("sliders", ACCENT), "执行与模拟")

        connection_page = QWidget()
        form = QFormLayout(connection_page)
        form.setContentsMargins(16, 12, 16, 12)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.url = QLineEdit(self.settings.value("url", DEFAULT_URL))
        self.url.setObjectName("urlInput")
        self.url.setAccessibleName("网站入口")
        form.addRow(label("网站入口", "eyebrow"), self.url)
        chrome_row = QHBoxLayout()
        self.chrome = QLineEdit(self.settings.value("chrome", ""))
        self.chrome.setPlaceholderText("自动选择 Chrome / Edge，未安装时使用随附 Chrome")
        self.chrome.setObjectName("chromePath")
        self.chrome.setAccessibleName("浏览器路径")
        self.chrome_button = QPushButton("选择浏览器")
        self.chrome_button.setIcon(icon("folder"))
        self.chrome_button.clicked.connect(self._select_chrome)
        chrome_row.addWidget(self.chrome, 1)
        chrome_row.addWidget(self.chrome_button)
        form.addRow(label("浏览器路径", "eyebrow"), chrome_row)
        output_row = QHBoxLayout()
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        self.output_root = QLineEdit(self.settings.value("output_root", str(base / "results")))
        self.output_root.setAccessibleName("统计保存位置")
        self.output_button = QPushButton("选择结果目录")
        self.output_button.setIcon(icon("folder"))
        self.output_button.clicked.connect(self._select_output)
        output_row.addWidget(self.output_root, 1)
        output_row.addWidget(self.output_button)
        form.addRow(label("统计保存位置", "eyebrow"), output_row)
        self.settings_tabs.addTab(connection_page, icon("connection"), "连接与保存")
        layout.addWidget(self.settings_tabs)

        self.info_splitter = WorkspaceSplitter(Qt.Horizontal)
        self.info_splitter.setObjectName("identityBankSplitter")
        self.info_splitter.setHandleWidth(12)
        self.identity_button = QPushButton("导入身份 .xlsx")
        self.identity_button.setProperty("role", "import")
        self.identity_button.setObjectName("importIdentity")
        self.identity_button.setIcon(icon("upload", ACCENT))
        self.identity_button.clicked.connect(lambda: self._choose_file("identity"))
        self.identity_count = label("0 人", "count")
        left, ll = card("身份信息", self.identity_count, self.identity_button)
        self.identity_summary = ElidedLabel("导入 Excel 后查看人员；身份证号始终脱敏显示")
        self.identity_summary.setProperty("role", "muted")
        ll.addWidget(self.identity_summary)
        self.people = EmptyTable("尚未导入身份\n点击右上角「导入身份」开始")
        self.people.setHorizontalHeaderLabels(["行号", "区县", "姓名", "身份证号（脱敏）", "状态", "模拟得分"])
        self._style_table(self.people)
        self.people.itemSelectionChanged.connect(self._update_controls)
        ll.addWidget(self.people, 1)
        self.info_splitter.addWidget(left)

        self.bank_button = QPushButton("导入题库 .pdf")
        self.bank_button.setProperty("role", "import")
        self.bank_button.setObjectName("importBank")
        self.bank_button.setIcon(icon("upload", ACCENT))
        self.bank_button.clicked.connect(lambda: self._choose_file("bank"))
        self.bank_count = label("0 题", "count")
        right, rl = card("题库搜索与预览", self.bank_count, self.bank_button)
        self.bank_summary = ElidedLabel("本地模拟需至少 100 题，包含判断、单选和多选")
        self.bank_summary.setProperty("role", "muted")
        rl.addWidget(self.bank_summary)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索题号、题干或选项…")
        self.search.setAccessibleName("搜索题库")
        self.search.textChanged.connect(self._filter_questions)
        self.kind = QComboBox()
        self.kind.setAccessibleName("筛选题型")
        self.kind.addItems(["全部题型", "判断", "单选", "多选"])
        self.kind.setMinimumWidth(105)
        self.kind.currentTextChanged.connect(self._filter_questions)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.kind)
        rl.addLayout(search_row)
        self.question_splitter = WorkspaceSplitter(Qt.Horizontal)
        self.question_splitter.setHandleWidth(10)
        self.question_list = QListWidget()
        self.question_list.setAccessibleName("题库题目列表")
        self.question_list.setMinimumSize(110, 68)
        self.question_list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.question_list.setTextElideMode(Qt.ElideRight)
        self.question_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.question_list.currentRowChanged.connect(self._preview_question)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("questionPreview")
        self.preview.setAccessibleName("题目内容与答案预览")
        self.preview.setMinimumSize(150, 68)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("题目预览\n\n导入题库并选择题目，\n查看题干、选项、答案与来源。")
        self.question_splitter.addWidget(self.question_list)
        self.question_splitter.addWidget(self.preview)
        self.question_splitter.setSizes([240, 330])
        rl.addWidget(self.question_splitter, 1)
        self.info_splitter.addWidget(right)
        self.info_splitter.setSizes([540, 670])
        self.content_splitter = WorkspaceSplitter(Qt.Vertical)
        self.content_splitter.setObjectName("informationResultsSplitter")
        self.content_splitter.setHandleWidth(12)
        self.content_splitter.addWidget(self.info_splitter)

        progress_box, pl = card("批量进度与本地模拟成绩")
        metrics = pl.itemAt(0).layout()
        metrics.setSpacing(18)
        self.metric_labels = {}
        for key, caption, color in (("running", "正在进行", "#4679a8"), ("completed", "已完成", ACCENT), ("failed", "失败 / 中止", "#b06760"), ("pending", "未开始", "#7a8d91")):
            metric = QLabel()
            metric.setTextFormat(Qt.RichText)
            metric.setProperty("role", "muted")
            self.metric_labels[key] = (metric, caption, color)
            metrics.addWidget(metric)
        self.progress_caption = label("等待开始")
        self.batch_progress = QProgressBar()
        self.batch_progress.setAccessibleName("批次完成进度")
        self.batch_progress.setTextVisible(False)
        self.batch_progress.setRange(0, 1)
        self.batch_progress.setValue(0)
        pl.addWidget(self.batch_progress)
        self.current_person = ElidedLabel("导入身份并启动批次后，在这里查看实时进度")
        self.current_person.setObjectName("currentPerson")
        self.current_person.setTextFormat(Qt.PlainText)
        current_row = QHBoxLayout()
        current_row.addWidget(self.current_person, 1)
        current_row.addWidget(self.progress_caption)
        pl.addLayout(current_row)
        self.result_tabs = QTabWidget()
        self.result_tabs.setObjectName("resultTabs")
        self.result_tables = {}
        for key, caption in (("running", "正在进行"), ("completed", "登录验证完成"), ("failed", "失败 / 中止")):
            table = EmptyTable({"running": "暂无正在执行的任务", "completed": "完成的人员与模拟成绩将显示在这里", "failed": "暂无失败或中止的记录"}[key])
            table.setHorizontalHeaderLabels(["行号", "区县", "姓名", "身份证号（脱敏）", "模拟得分", "结果 / 原因"])
            self._style_table(table)
            self.result_tables[key] = table
            self.result_tabs.addTab(table, caption)
        self.result_tabs.setMinimumHeight(110)
        pl.addWidget(self.result_tabs, 1)
        result_row = QHBoxLayout()
        self.result_location = ElidedLabel("每批次自动保存已完成、未完成两份 Excel")
        self.result_location.setProperty("role", "muted")
        self.result_location.setTextFormat(Qt.PlainText)
        self.open_results_button = QPushButton("打开结果目录")
        self.open_results_button.setIcon(icon("folder"))
        self.open_results_button.clicked.connect(self._open_results)
        self.export_button = QPushButton("重新导出")
        self.export_button.setIcon(icon("export"))
        self.export_button.clicked.connect(lambda: self._start_job("export"))
        result_row.addWidget(self.result_location, 1)
        result_row.addWidget(self.open_results_button)
        result_row.addWidget(self.export_button)
        pl.addLayout(result_row)
        self.content_splitter.addWidget(progress_box)
        self.content_splitter.setSizes([270, 290])
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 1)
        for pane, key in ((self.info_splitter, "layout/infoSplitter"), (self.content_splitter, "layout/contentSplitter"), (self.question_splitter, "layout/questionSplitter")):
            state = self.settings.value(key)
            if isinstance(state, QByteArray):
                pane.restoreState(state)
            pane.setChildrenCollapsible(False)
            for index in range(pane.count()):
                pane.setCollapsible(index, False)
            pane.splitterMoved.connect(lambda _pos, _index, pane=pane, key=key: self.settings.setValue(key, pane.saveState()))
        self.workspace_scroll = QScrollArea()
        self.workspace_scroll.setObjectName("workspaceScroll")
        self.workspace_scroll.setFrameShape(QFrame.NoFrame)
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.workspace_scroll.setMinimumHeight(180)
        self.workspace_scroll.setWidget(self.content_splitter)
        layout.addWidget(self.workspace_scroll, 1)

        action_card = QFrame()
        action_card.setObjectName("actionCard")
        actions = QHBoxLayout(action_card)
        actions.setContentsMargins(14, 10, 14, 10)
        actions.setSpacing(8)
        self.status = ElidedLabel("正在初始化浏览器控制模块…")
        self.status.setObjectName("statusMessage")
        self.status.setTextFormat(Qt.PlainText)
        actions.addWidget(self.status, 1)
        self.log_toggle = QPushButton("运行记录")
        self.log_toggle.setObjectName("toggleLog")
        self.log_toggle.setIcon(icon("log"))
        self.log_toggle.setCheckable(True)
        self.log_toggle.setToolTip("展开 / 收起运行记录与导入问题")
        actions.addWidget(self.log_toggle)
        self.inspect_button = QPushButton("环境检测")
        self.inspect_button.setObjectName("inspectEnvironment")
        self.inspect_button.setIcon(icon("inspect"))
        self.inspect_button.clicked.connect(lambda: self._start_job("inspect"))
        actions.addWidget(self.inspect_button)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stopLogin")
        self.stop_button.setIcon(icon("stop", "#b25252"))
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.stop_button)
        self.start_button = QPushButton("启动批量登录")
        self.start_button.setObjectName("startLogin")
        self.start_button.setIcon(icon("play", "#ffffff"))
        self.start_button.setMinimumWidth(215)
        self.start_button.clicked.connect(lambda: self._start_job("login"))
        actions.addWidget(self.start_button)
        layout.addWidget(action_card)
        self.log_drawer = QWidget()
        drawer = QVBoxLayout(self.log_drawer)
        drawer.setContentsMargins(0, 0, 0, 0)
        drawer.setSpacing(6)
        log_header = QHBoxLayout()
        log_header.addWidget(label("运行记录与导入问题", "eyebrow"))
        log_header.addStretch(1)
        open_log = QPushButton("打开日志目录")
        open_log.setIcon(icon("folder"))
        open_log.clicked.connect(self._open_log_dir)
        log_header.addWidget(open_log)
        drawer.addLayout(log_header)
        self.log = QPlainTextEdit()
        self.log.setObjectName("runLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(84)
        drawer.addWidget(self.log)
        self.log_drawer.hide()
        self.log_toggle.toggled.connect(self.log_drawer.setVisible)
        layout.addWidget(self.log_drawer)
        self.setStyleSheet(STYLESHEET)
        self._render_batch()
        self._sync_information_height()
        QTimer.singleShot(0, self._sync_information_height)
        # Tab order follows the primary workflow even though connection fields are on a second tab.
        tab_order = (self.concurrency, self.background, self.test_mode, self.preview_test_button,
                     self.minutes_min, self.minutes_max, self.fastest, self.minimum_score,
                     self.score_min, self.score_max, self.identity_button, self.people,
                     self.bank_button, self.search, self.kind, self.question_list,
                     self.preview, self.result_tabs, self.open_results_button,
                     self.export_button, self.log_toggle, self.inspect_button,
                     self.stop_button, self.start_button)
        for first, second in zip(tab_order, tab_order[1:]):
            QWidget.setTabOrder(first, second)

    @staticmethod
    def _style_table(table):
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setMinimumHeight(50)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setMinimumSectionSize(42)
        table.verticalHeader().setDefaultSectionSize(34)
        table.verticalHeader().hide()
        table.setWordWrap(False)

    def _sync_information_height(self):
        self.ensurePolished()
        self.info_splitter.setMinimumHeight(self.info_splitter.minimumSizeHint().height())
        results = self.content_splitter.widget(1)
        results.setMinimumHeight(results.minimumSizeHint().height())
        self.content_splitter.setMinimumHeight(self.info_splitter.minimumHeight() + results.minimumHeight() + self.content_splitter.handleWidth())

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
        if self._closing:
            state, caption = "waiting", "●  正在关闭"
        elif self.import_workers:
            state, caption = "busy", "●  正在导入"
        elif self.busy:
            state, caption = "busy", "●  正在执行"
        elif self.backend_ready:
            state, caption = "ready", "●  就绪"
        else:
            state, caption = "waiting", "●  正在初始化"
        self.activity_badge.setText(caption)
        if self.activity_badge.property("state") != state:
            self.activity_badge.setProperty("state", state)
            self.activity_badge.style().unpolish(self.activity_badge)
            self.activity_badge.style().polish(self.activity_badge)

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
            self.identity_count.setText("0 人")
            self.identity_summary.setText("正在导入身份文件…")
            worker.result.connect(lambda result: self.identities_loaded(result, Path(path).name))
        else:
            self.bank_source = path
            self.questions = []
            self.bank_count.setText("0 题")
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
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.people.setItem(row, column, item)
        if self.candidates:
            self.people.selectRow(0)
        self.identity_summary.setText(f"{filename} · {result.sheet} · 有效 {len(self.candidates)} 人 · 问题 {len(result.issues)} 行")
        self.identity_count.setText(f"{len(self.candidates)} 人")
        self.show_status(f"身份导入完成：有效 {len(self.candidates)} 人，问题 {len(result.issues)} 行")
        for issue in result.issues:
            self.show_status(issue)
        self._sync_information_height()
        self._update_controls()

    def bank_loaded(self, result, filename=""):
        self.questions = result.questions
        counts = Counter(q.kind for q in self.questions)
        self.bank_summary.setText(f"{filename} · {result.page_count} 页 · 判断 {counts['判断']} / 单选 {counts['单选']} / 多选 {counts['多选']} · 异常 {len(result.issues)}")
        self.bank_count.setText(f"{len(self.questions)} 题")
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
            self.question_list.item(self.question_list.count() - 1).setToolTip(f"第 {q.number} 题 · {q.kind}\n{q.stem}")
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
        batch_summary = f"已完成 {counts['completed']} · 失败/中止 {counts['failed'] + counts['stopped']} · 正在进行 {counts['running']} · 未开始 {counts['pending']}"
        total = len(self.batch_records)
        finished = counts['completed'] + counts['failed'] + counts['stopped']
        self.batch_progress.setRange(0, max(1, total))
        self.batch_progress.setValue(finished)
        self.batch_progress.setAccessibleDescription(batch_summary)
        self.progress_caption.setText(f"{finished} / {total} 人已处理" if total else "等待开始")
        for key, (metric, caption, color) in self.metric_labels.items():
            number = counts[key] + (counts['stopped'] if key == "failed" else 0)
            metric.setText(f'<span style="color:{color}; font-size:18px; font-weight:600">{number}</span>'
                           f'&nbsp;&nbsp;{caption}')
        active = [r for r in self.batch_records if r.status == "running"]
        summary = "；".join(f"第 {r.candidate.row} 行 · {r.candidate.name} · {status_text(r)}" for r in active[:3])
        if len(active) > 3:
            summary += f"；另 {len(active) - 3} 人（见正在进行列表）"
        self.current_person.setText(f"正在执行 {len(active)} 人：{summary}" if active else ("本批次暂无正在执行的人员" if total else "导入身份并启动批次后，在这里查看实时进度"))
        for key, table in self.result_tables.items():
            records = [r for r in self.batch_records if r.status == key or (key == "failed" and r.status == "stopped")]
            table.setRowCount(len(records))
            for row, r in enumerate(records):
                c = r.candidate
                score = f"{r.score} / {r.maximum}" if r.score is not None else "—"
                for column, value in enumerate((str(c.row), c.district, c.name, c.masked_id, score, r.reason or status_text(r))):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 4 and r.score is not None:
                        item.setForeground(QColor(ACCENT))
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                    if column == 5:
                        item.setForeground(QColor({"running": "#4679a8", "completed": ACCENT, "failed": "#b06760", "stopped": "#b06760"}.get(r.status, "#6b8287")))
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
