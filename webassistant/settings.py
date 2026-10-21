"""Preserve user preferences when upgrading from WebLoginAssistant."""

from PySide6.QtCore import QSettings

from . import APP_NAME


def load_settings():
    settings = QSettings("LocalTools", APP_NAME)
    marker = "migration/WebLoginAssistant"
    if not settings.value(marker, False, type=bool):
        legacy = QSettings("LocalTools", "WebLoginAssistant")
        for key in legacy.allKeys():
            if not settings.contains(key):
                settings.setValue(key, legacy.value(key))
        settings.setValue(marker, True)
        settings.sync()
    return settings
