from PySide6.QtCore import QSettings

from webassistant.settings import load_settings


def stores(tmp_path, monkeypatch):
    def factory(organization, application):
        assert organization == "LocalTools"
        return QSettings(str(tmp_path / f"{application}.ini"), QSettings.IniFormat)
    monkeypatch.setattr("webassistant.settings.QSettings", factory)
    return factory("LocalTools", "WebLoginAssistant"), factory("LocalTools", "Answering")


def test_migration_preserves_old_preferences_and_existing_new_values(tmp_path, monkeypatch):
    legacy, current = stores(tmp_path, monkeypatch)
    values = {"url": "https://example.com", "chrome": "C:/Browser/chrome.exe",
              "output_root": "E:/ExistingResults", "minutes_min": 12.5, "fastest": True}
    for key, value in values.items():
        legacy.setValue(key, value)
    current.setValue("url", "https://new.example.com")
    legacy.sync()
    current.sync()
    migrated = load_settings()
    assert migrated.value("url") == "https://new.example.com"
    for key, value in values.items():
        assert legacy.value(key) == value
        if key != "url":
            assert migrated.value(key) == value
    # A subsequent launch must not import later edits to the old application.
    legacy.setValue("later", "old-only")
    legacy.sync()
    assert not load_settings().contains("later")


def test_clean_install_has_no_legacy_preferences(tmp_path, monkeypatch):
    stores(tmp_path, monkeypatch)
    settings = load_settings()
    assert not settings.contains("url")
    assert not settings.contains("output_root")
