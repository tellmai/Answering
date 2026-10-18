import asyncio
from types import SimpleNamespace

import pytest

from webassistant import browsers


def test_discovery_priority_and_edge_fallback(tmp_path, monkeypatch):
    paths = []
    for name in ("brave.exe", "msedge.exe", "chrome.exe", "firefox.exe"):
        path = tmp_path / name
        path.touch()
        paths.append(str(path))
    bundled = tmp_path / "chrome/chrome-win64/chrome.exe"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    monkeypatch.setattr(browsers.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(browsers, "_registry_paths", lambda: paths)
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)
    assert [c.priority for c in browsers.discover_browsers()] == [0, 1, 2, 3]
    (tmp_path / "chrome.exe").unlink()
    assert browsers.discover_browsers()[0].name == "Microsoft Edge"
    (tmp_path / "msedge.exe").unlink()
    assert browsers.discover_browsers()[0].name == "Brave"


def test_launch_failure_tries_next_and_explicit_does_not_change_choice(monkeypatch):
    choices = [browsers.BrowserChoice("Chrome", "chrome", 0), browsers.BrowserChoice("Edge", "edge", 1)]
    monkeypatch.setattr(browsers, "discover_browsers", lambda: choices)
    calls = []
    async def launch(**kwargs):
        calls.append(kwargs["executable_path"])
        if calls[-1] != "edge":
            raise RuntimeError("not runnable")
        return object()
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    _, selected = asyncio.run(browsers.launch_browser(playwright))
    assert selected.name == "Edge" and calls == ["chrome", "edge"]
    calls.clear()
    with pytest.raises(ValueError, match="启动失败"):
        asyncio.run(browsers.launch_browser(playwright, "manual.exe"))
    assert calls == ["manual.exe"]


def test_registry_commands_are_parsed_as_data():
    assert browsers.executable_from_command('"C:\\Browser Folder\\chrome.exe" -- "%1"') == 'C:\\Browser Folder\\chrome.exe'
    assert browsers.executable_from_command('C:\\Browser Folder\\msedge.exe --single-argument %1') == 'C:\\Browser Folder\\msedge.exe'
    assert not browsers.executable_from_command("https://example.com")
