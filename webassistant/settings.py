"""Preserve user preferences when upgrading from WebLoginAssistant."""

from PySide6.QtCore import QSettings

from . import APP_NAME
from .models import DEFAULT_SCORE_MIN, DEFAULT_SCORE_MAX


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
    score_marker = "migration/score-range-98-100"
    if not settings.value(score_marker, False, type=bool):
        if (settings.value("score_min", 91, type=int), settings.value("score_max", 99, type=int)) == (91, 99):
            settings.setValue("score_min", DEFAULT_SCORE_MIN)
            settings.setValue("score_max", DEFAULT_SCORE_MAX)
        settings.setValue(score_marker, True)
        settings.sync()
    return settings
