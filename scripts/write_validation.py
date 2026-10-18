import json
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
cases = {}
reports = [ROOT / "build/test-results-v4.xml", ROOT / "build/controller-tests-v4.xml"]
for report in sorted(reports, key=lambda p: p.stat().st_mtime):
    for case in ET.parse(report).iter("testcase"):
        key = (case.get("classname"), case.get("name"))
        cases[key] = not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
if not cases or not all(cases.values()):
    raise RuntimeError("Tests are incomplete or have failures")
public = json.loads((ROOT / "build/public-check.json").read_text(encoding="utf-8"))
frozen = json.loads((ROOT / "build/exe-smoke/result.json").read_text(encoding="utf-8"))
if (not public["ok"] or not frozen["ok"] or not frozen["frozen"] or not frozen.get("batch_statistics")
        or not frozen.get("mock_exam") or frozen.get("mock_total") != 100
        or frozen.get("mock_score") != frozen.get("mock_target") or not 91 <= frozen.get("mock_score", 0) <= 99):
    raise RuntimeError("Public or frozen checks did not pass")
text = f"""# 验证记录

验证日期：2026-10-18。平台：Windows 64 位，Python 3.11 构建。

## 已完成

- {len(cases)} 项不同的自动化测试通过：身份格式、精度与无效行保留、参考文件解析、跨页题目、多选答案、网址限制、日志脱敏、批量状态界面、重复启动、登录成功、验证码三次失败后继续下一人、新验证码延迟刷新、浏览器选择与启动回退、立即停止、等待当前人结束、取消停止、确认框期间队列等待、未开始人员保留、统计文本格式及导出失败恢复、关闭浏览器、前端脚本延迟加载、本地 OCR 推理。旧的单会话人工补输接口仍有回归测试，批量界面不使用该接口。
- 本地模拟验证：100 题、每题 1 分、三种题型、无重复抽题、题型不足补足、答案匹配、重复选项处理、独立评分、多选少选/多选不得分、阻止未答完提交、预览开关和成绩日志。提供的 1021 题均验证可匹配原始答案。
- 本轮新增验证：默认 10 分钟、分钟范围、最快完成、目标分数轮换、无效分数交集拦截、100 题两次复核与选项变化修复、可控时钟的 10 分钟调度、真实 1 分钟计时提交、连续两人分别获得 99 与 100 分、停止后真实浏览器页面及本地服务保留、关闭软件后资源释放、作答时立即停止及等待完成后停止。
- 真实公开网站检查通过：浏览器启动、HTTPS 访问、区县列表、黔江区考生登录切换、验证码图片加载。未填写身份、未识别真实验证码、未提交真实账号登录。
- 正式 EXE 离线自检通过：界面及中文字体、文档库、本地 OCR、Playwright、浏览器、网页资源、100 题自动作答、两次复核、独立评分 94 分及成绩/用时导出。结果中 frozen=true，运行的是 EXE 内置 Python。
- EXE 验证时清空了 PYTHONHOME、PYTHONPATH、VIRTUAL_ENV，并将 PATH 限制为 Windows 系统目录。
- 打包配置排除了误从其他工具目录收集的同名 ICU 库，使用 Windows 系统 ICU。

## 验证边界

尚未在另一台完全未安装 Python 的 Windows 虚拟机上验证。未提交真实账号登录，真实验证码识别率未做统计。批量登录和三次失败后继续下一人已在本地模拟网站验证。浏览器顺序和启动失败回退使用模拟发现结果测试；本机可用浏览器已实际启动，未逐一安装所有品牌浏览器联调。

当前交付实现本地模拟的 100 题、用时和得分范围设置。未运行完整 10 分钟长测，使用可控时钟验证默认 10 分钟调度，并完成真实 1 分钟浏览器提交测试；电脑负载及评分请求可能造成少量时间偏差。真实网站考试作答不在本轮范围。

软件及源码压缩包不包含用户身份表、原始题库、账号日志或真实验证码截图。
"""
(ROOT / "验证记录.md").write_text(text, encoding="utf-8")
target = ROOT / "dist/WebLoginAssistant/验证记录.md"
target.write_text(text, encoding="utf-8")
print(f"Validation report ready: {len(cases)} distinct tests passed")
