"""Discover installed Chromium browsers without invoking a shell or user profiles."""
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserChoice:
    name: str
    path: str
    priority: int


KNOWN = {
    "chrome.exe": ("Google Chrome", 0),
    "msedge.exe": ("Microsoft Edge", 1),
    "brave.exe": ("Brave", 2),
    "vivaldi.exe": ("Vivaldi", 2),
    "opera.exe": ("Opera", 2),
    "360chrome.exe": ("360 极速浏览器", 2),
    "360se.exe": ("360 安全浏览器", 2),
    "qqbrowser.exe": ("QQ 浏览器", 2),
    "chromium.exe": ("Chromium", 2),
}


def executable_from_command(command):
    command = os.path.expandvars(str(command)).strip()
    match = re.match(r'^"([^"]+\.exe)"|^(.+?\.exe)(?:\s|$)', command, re.I)
    return (match[1] or match[2]) if match else ""


def _registry_paths():
    paths = []
    try:
        import winreg
    except ImportError:
        return paths
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for exe in KNOWN:
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}", 0, winreg.KEY_READ | view) as key:
                        paths.append(winreg.QueryValue(key, None))
                except OSError:
                    pass
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Clients\StartMenuInternet", 0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        name = winreg.EnumKey(root, index)
                        try:
                            with winreg.OpenKey(root, name + r"\shell\open\command") as key:
                                paths.append(winreg.QueryValue(key, None))
                        except OSError:
                            pass
            except OSError:
                pass
    return paths


def discover_browsers():
    raw = list(_registry_paths())
    locations = (
        "Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe",
        "BraveSoftware/Brave-Browser/Application/brave.exe", "Vivaldi/Application/vivaldi.exe",
        "Programs/Opera/opera.exe", "Opera/opera.exe", "Chromium/Application/chrome.exe",
        "360/360Chrome/Chrome/Application/360chrome.exe", "360/360se6/Application/360se.exe",
        "Tencent/QQBrowser/QQBrowser.exe",
    )
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(variable):
            raw.extend(str(Path(os.environ[variable]) / path) for path in locations)
    choices, seen = [], set()
    for value in raw:
        path = executable_from_command(value)
        key = path.casefold()
        name = Path(path).name.lower()
        if path and name in KNOWN and key not in seen and Path(path).is_file():
            label, priority = KNOWN[name]
            choices.append(BrowserChoice(label, path, priority))
            seen.add(key)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    for relative in ("chrome/chrome-win64/chrome.exe", "tools/chrome/chrome-win64/chrome.exe"):
        path = base / relative
        if path.is_file() and str(path).casefold() not in seen:
            choices.append(BrowserChoice("随附 Chrome for Testing", str(path), 3))
            seen.add(str(path).casefold())
    return sorted(choices, key=lambda item: item.priority)


async def launch_browser(playwright, explicit="", headless=False, progress=lambda _: None):
    choices = [BrowserChoice("手动指定浏览器", explicit, -1)] if explicit else discover_browsers()
    if not choices:
        raise ValueError("未找到兼容浏览器，请选择 Chrome / Edge 程序或检查随附浏览器文件")
    for choice in choices:
        try:
            browser = await playwright.chromium.launch(executable_path=choice.path, headless=headless)
        except Exception:
            progress(f"{choice.name} 启动失败，正在检查其他可用浏览器" if not explicit else "手动指定的浏览器启动失败")
            continue
        progress(f"使用浏览器：{choice.name}")
        return browser, choice
    raise ValueError("可用浏览器均启动失败，请检查软件文件或手动选择兼容浏览器程序")
