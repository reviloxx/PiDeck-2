"""Forward remote-control actions to a running application.

PiDeck keeps the HDMI-CEC adapter for every launched application and
translates remote actions into ordinary key presses. The same path also
forwards normalized gamepad actions for TV-oriented apps. Overlay and menu
actions are not forwarded; the presentation layer opens the session
overlay instead.
"""

import logging

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.keyboard import KeyInjector, VirtualKey
from pideck.application.session import SessionSnapshot, SessionState
from pideck.domain.configuration import ApplicationDefinition, PreferredInput

_LOGGER = logging.getLogger(__name__)

_KEYS = {
    InputAction.UP: VirtualKey.UP,
    InputAction.DOWN: VirtualKey.DOWN,
    InputAction.LEFT: VirtualKey.LEFT,
    InputAction.RIGHT: VirtualKey.RIGHT,
    InputAction.ACTIVATE: VirtualKey.RETURN,
    InputAction.BACK: VirtualKey.ESCAPE,
    InputAction.PLAY: VirtualKey.MEDIA_PLAY,
    InputAction.PAUSE: VirtualKey.MEDIA_PAUSE,
    InputAction.PLAY_PAUSE: VirtualKey.MEDIA_PLAY_PAUSE,
    InputAction.STOP: VirtualKey.MEDIA_STOP,
    InputAction.REWIND: VirtualKey.MEDIA_REWIND,
    InputAction.FAST_FORWARD: VirtualKey.MEDIA_FAST_FORWARD,
}

# Chromium / Red (YouTube) ignores XF86AudioStop; Escape exits playback.
_ESCAPE_STOP_IDS = frozenset({"youtube"})

_OVERLAY_ACTIONS = frozenset({InputAction.MENU, InputAction.HOME})


def _key_for(application_id: str, action: InputAction) -> VirtualKey | None:
    """Choose the synthesized key for ``action`` in the running application."""
    if action is InputAction.STOP and application_id in _ESCAPE_STOP_IDS:
        return VirtualKey.ESCAPE
    return _KEYS.get(action)


def should_forward_gamepad(application: ApplicationDefinition | None) -> bool:
    """Return whether gamepad actions should be injected as keys for ``application``.

    Apps that prefer a native gamepad (Steam Link, RetroArch) keep exclusive
    controller ownership. Everything else — including YouTube and Kodi — gets
    the same keyboard synthesis used for HDMI-CEC.
    """
    if application is None:
        return False
    return application.preferred_input is not PreferredInput.GAMEPAD


class InputForwardingService:
    """Translate normalized remote actions into key presses for the running app."""

    def __init__(self, injector: KeyInjector) -> None:
        """Use ``injector`` to synthesize keys into the focused application window."""
        self._injector = injector

    def handle(self, event: InputEvent, session: SessionSnapshot, *, pideck_has_focus: bool) -> bool:
        """Forward ``event`` while an application is active; return whether it was consumed.

        Overlay toggles (``MENU`` / ``HOME``) are left for the presentation layer.
        Keys are synthesized only while the application is running and PiDeck has
        no focus, so a remote press cannot activate PiDeck's own controls by accident.
        """
        application = session.application
        if application is None or session.state is SessionState.IDLE:
            return False
        if event.action in _OVERLAY_ACTIONS:
            return False
        if not event.pressed:
            return True
        if session.state is not SessionState.RUNNING or pideck_has_focus:
            return True
        key = _key_for(application.identifier, event.action)
        if key is not None:
            self._injector.tap(key)
        return True
