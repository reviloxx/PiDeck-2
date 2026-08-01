"""Small runtime translation catalog for PiDeck's Qt presentation."""

from datetime import datetime

from PySide6.QtCore import QDate, QDateTime, QLocale, QTime


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "choose_application": "Choose an application",
        "settings": "Settings",
        "shutdown": "Shutdown",
        "no_applications": "No applications configured",
        "running": "Running {name}",
        "launching": "Launching {name}...",
        "waiting": "Waiting for {name}...",
        "exited": "{name} exited ({code})",
        "could_not_start": "Could not start {name}: {message}",
        "appearance": "Appearance",
        "home_screen": "Home screen",
        "updates": "Updates",
        "back": "Back",
        "launcher_settings": "Launcher settings",
        "navigation_hint": "Use the arrow keys or controller to navigate",
        "appearance_kicker": "PIDECK / SETTINGS",
        "appearance_description": "Personalize the look and motion of your launcher.",
        "home_description": "Choose which applications are available from the home screen.",
        "updates_description": "Check and install updates for configured applications.",
        "theme": "Theme",
        "theme_hint": "Choose the visual style used throughout PiDeck.",
        "reduced_motion": "Reduced motion",
        "reduced_motion_hint": "Use shorter focus animations for a calmer interface.",
        "clock": "Clock",
        "clock_hint": "Show the current date and time on the home screen.",
        "on": "On",
        "home_apps_hint": "Select the applications shown on the home screen.",
        "update_hint": "Check and install updates for configured applications.",
        "checking": "Checking...",
        "up_to_date": "Up to date",
        "update_available": "Update available",
        "update": "Update",
        "updating": "Updating...",
        "updated": "Updated",
        "authentication_required": "Authentication required",
        "not_supported": "Not supported",
        "update_failed": "Update failed",
        "cancelled": "Cancelled",
        "system_update": "System update",
        "password_message": "Enter your password to update {name}.",
        "password_placeholder": "Password",
        "cancel": "Cancel",
        "continue": "Continue",
        "application_required": "Application required",
        "at_least_one": "Select at least one application for the home screen.",
        "replacement_title": "Application already running",
        "replacement_message": "Stop the current application and start {name}?",
        "settings_not_saved": "Settings not saved",
        "language": "Language",
        "language_hint": "Choose the language used by PiDeck.",
        "english": "English",
        "german": "German",
        "choose_profile": "Choose profile",
        "profile_label": "Application profile:",
    },
    "de": {
        "choose_application": "Anwendung auswählen",
        "settings": "Einstellungen",
        "shutdown": "Herunterfahren",
        "no_applications": "Keine Anwendungen konfiguriert",
        "running": "{name} läuft",
        "launching": "{name} wird gestartet...",
        "waiting": "Warte auf {name}...",
        "exited": "{name} beendet ({code})",
        "could_not_start": "{name} konnte nicht gestartet werden: {message}",
        "appearance": "Darstellung",
        "home_screen": "Startbildschirm",
        "updates": "Aktualisierungen",
        "back": "Zurück",
        "launcher_settings": "Launcher-Einstellungen",
        "navigation_hint": "Mit Pfeiltasten oder Controller navigieren",
        "appearance_kicker": "PIDECK / EINSTELLUNGEN",
        "appearance_description": "Darstellung und Bewegung des Launchers anpassen.",
        "home_description": "Anwendungen für den Startbildschirm auswählen.",
        "updates_description": "Aktualisierungen für konfigurierte Anwendungen prüfen und installieren.",
        "theme": "Design",
        "theme_hint": "Das visuelle Erscheinungsbild von PiDeck auswählen.",
        "reduced_motion": "Weniger Bewegung",
        "reduced_motion_hint": "Kürzere Fokus-Animationen für eine ruhigere Oberfläche verwenden.",
        "clock": "Uhr",
        "clock_hint": "Datum und Uhrzeit auf dem Startbildschirm anzeigen.",
        "on": "Ein",
        "home_apps_hint": "Anwendungen für den Startbildschirm auswählen.",
        "update_hint": "Aktualisierungen für konfigurierte Anwendungen prüfen und installieren.",
        "checking": "Prüfe...",
        "up_to_date": "Aktuell",
        "update_available": "Aktualisierung verfügbar",
        "update": "Aktualisieren",
        "updating": "Wird aktualisiert...",
        "updated": "Aktualisiert",
        "authentication_required": "Authentifizierung erforderlich",
        "not_supported": "Nicht unterstützt",
        "update_failed": "Aktualisierung fehlgeschlagen",
        "cancelled": "Abgebrochen",
        "system_update": "Systemaktualisierung",
        "password_message": "Passwort eingeben, um {name} zu aktualisieren.",
        "password_placeholder": "Passwort",
        "cancel": "Abbrechen",
        "continue": "Weiter",
        "application_required": "Anwendung erforderlich",
        "at_least_one": "Mindestens eine Anwendung für den Startbildschirm auswählen.",
        "replacement_title": "Anwendung läuft bereits",
        "replacement_message": "Aktuelle Anwendung beenden und {name} starten?",
        "settings_not_saved": "Einstellungen nicht gespeichert",
        "language": "Sprache",
        "language_hint": "Die von PiDeck verwendete Sprache auswählen.",
        "english": "Englisch",
        "german": "Deutsch",
        "choose_profile": "Profil auswählen",
        "profile_label": "Anwendungsprofil:",
    },
}


def tr(language: str, key: str, **values: object) -> str:
    """Return a translated presentation string with safe English fallback."""
    text = TRANSLATIONS.get(language, TRANSLATIONS["en"]).get(key, key)
    return text.format(**values)


def clock_text(language: str, value: datetime | None = None) -> str:
    """Format the current date and time according to the selected language."""
    now = value or datetime.now()
    locale = QLocale(QLocale.Language.German if language == "de" else QLocale.Language.English)
    pattern = "ddd, dd. MMM yyyy  HH:mm" if language == "de" else "ddd, dd MMM yyyy  HH:mm"
    qt_value = QDateTime(
        QDate(now.year, now.month, now.day),
        QTime(now.hour, now.minute, now.second),
    )
    return locale.toString(qt_value, pattern)
