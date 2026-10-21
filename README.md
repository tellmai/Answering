# Answering

Windows 10/11 64 位桌面软件。导入身份 Excel 后，按设置的并发数量完成区县选择、考生登录、身份填写及本机验证码识别；可在登录后运行本地生成的 100 题模拟试卷，记录每人的处理状态、分数和用时。

当前版本：**1.4.1**。真实网站操作止于登录；自动答题、分数范围和计时设置作用于软件生成的本地模拟试卷。

## 下载与运行

前往 [v1.4.1 下载页面](https://github.com/tellmai/Answering/releases/tag/v1.4.1)，下载 `Answering-Windows-x64-v1.4.1.zip`，完整解压后双击 `Answering/Answering.exe`。发布附件使用英文文件名，软件界面及说明为中文。

- 完整包包含 EXE、Qt、Playwright 驱动、OCR 模型和备用 Chrome，无需安装 Python，属于解压运行的免安装包。
- 必须保留 EXE 旁的 `_internal` 文件夹。单独提供的 `Answering.exe` 是同一版本的启动程序，不能离开完整包独立运行。
- 软件放在可写目录，身份表和题库由使用者自行导入。发布包不包含个人身份资料、原始题库或运行统计。
- 可使用发布页中的 `SHA256SUMS.txt` 核对下载文件，例如：`Get-FileHash -Algorithm SHA256 -LiteralPath 'Answering-Windows-x64-v1.4.1.zip'`。

从旧版升级时，首次启动会将 `LocalTools/WebLoginAssistant` 中新版尚不存在的设置迁移至 `LocalTools/Answering`，保留旧设置和结果文件。已有新版设置优先。

详细操作见 [使用说明](使用说明.md)，测试结果及未验证范围见 [验证记录](验证记录.md)。私有仓库和附件需登录有权限的 GitHub 账号才能访问。

## 并发与后台执行

- “并发数量”默认 10，范围 1–30；每个身份从登录到本地模拟作答结束占一个名额，完成或失败后自动接入下一人。
- 每批共用一个浏览器，每个身份使用独立 BrowserContext，Cookie、本地存储和试卷彼此隔离。
- 默认勾选“后台执行（隐藏浏览器）”，浏览器不显示窗口、不抢占前台；取消勾选后可观察浏览器流程。设置在下次启动时保留，当前批次使用启动时的配置。
- 运行时可以查看、选择和复制人员记录，切换成绩列表、搜索题库、查看日志及打开结果目录；影响当前流程的设置在批次结束后恢复可编辑。试卷预览按钮始终显示独立预览窗口。
- 停止提供等待所有正在执行人员完成、立即中止所有活动任务、取消三种选择。确认期间暂停接入新人员。

## 界面操作

“测试开关”只切换批次执行模式，不打开试卷；需要查看试卷时点击“重新生成 / 预览试卷”，无需先开启测试开关。最快完成仍进行两次全部答案校验和提交后评分核对。

拖动身份信息与题库之间的竖向分隔条，可调整左右比例；拖动上方信息区与批量进度/成绩之间的横向分隔条，可调整上下比例。布局会自动保存，重启后恢复，分区不会被拖动隐藏。

## 主要功能

- 读取首个包含“所在的区县、姓名、身份证号”三列表头的工作表；验证空值、号码格式及 Excel 数值精度，界面和日志脱敏显示身份证号。
- 优先使用 Chrome，其次 Edge、其他兼容的 Chromium 浏览器，最后使用随附 Chrome；也可手动指定路径。
- 验证码在本机识别，最多三次；失败记录原因后继续下一人。界面展示正在进行、已完成和失败人员。
- 每批生成独立的已完成、未完成 Excel 与人员状态 JSON，可将未完成表重新导入。统计文件包含完整身份信息，应保存在自己的受控目录。
- 测试开关开启时，每人生成不同的 100 题模拟卷，包含单选、多选和判断，每题 1 分，满分 100 分。
- 默认答题用时 10 分钟，可设置分钟范围或最快完成；默认最低分 90、分数范围 98–100。模拟作答两次复核后独立评分，日志和统计中记录实际结果。
- 停止支持等待所有正在执行人员完成、立即停止、取消。停止后保留当前网页和本地服务，关闭软件时释放浏览器资源。

## 从源码运行

安装 Python 3.11 64 位，在 PowerShell 中运行：

```powershell
git clone https://github.com/tellmai/Answering.git
cd Answering
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

源码使用系统已安装的兼容浏览器。需要备用 Chrome 时，可先运行：

```powershell
.\.venv\Scripts\python.exe scripts\fetch_chrome.py
```

## 测试与打包

```powershell
.\.venv\Scripts\python.exe -m pytest --junitxml=build/test-results.xml
.\scripts\build.ps1
.\dist\Answering\Answering.exe --smoke-test build\exe-smoke
.\.venv\Scripts\python.exe scripts\write_validation.py
.\.venv\Scripts\python.exe scripts\package_release.py
```

完整发布前先运行 `scripts/fetch_chrome.py` 准备备用浏览器。发布附件输出到 `dist/releases/v1.4.1`。打包脚本同时生成 `Answering-Source-v1.4.1.zip`、`UserGuide.md`、`Validation.md`、`release-manifest.json` 和 `SHA256SUMS.txt`。

`requirements.in` 声明直接依赖，`requirements.txt` 锁定已验证版本。`dist/Answering` 是完整运行目录，`dist/releases/v1.4.1` 中生成免安装 ZIP、源码 ZIP 和发布附件。打包前下载的 `tools/chrome` 会随软件分发。

离线自检：

```powershell
.\dist\Answering\Answering.exe --smoke-test build\exe-smoke
```

自检使用虚构身份和合成试题，验证界面、OCR、浏览器、统计及本地 100 题作答，不提交真实账号登录。私有参考文件不随源码分发，依赖它们的测试在文件缺失时跳过。

## 源码结构

| 路径 | 用途 |
| --- | --- |
| `main.py` | 程序入口及离线自检 |
| `webassistant/gui.py` | 桌面界面 |
| `webassistant/controller.py` | 工作线程、批次和停止控制 |
| `webassistant/browser.py`、`browsers.py` | 网站适配、浏览器发现与启动 |
| `webassistant/importers.py` | Excel 和 PDF 解析 |
| `webassistant/batch.py` | 人员状态和统计导出 |
| `webassistant/mock_exam.py`、`assets/mock_exam` | 本地试卷、评分及页面 |
| `tests` | 自动化测试 |
| `scripts`、`Answering.spec` | 构建和分发工具 |

项目尚未指定源码开源许可。依赖及随附浏览器的许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。
