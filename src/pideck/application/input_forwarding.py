"""Forward remote-control actions to an application that keeps PiDeck's CEC input.

Applications with ``cec: keyboard`` do not open the CEC adapter themselves.
PiDeck keeps reading the TV remote and translates its actions into ordinary
key presses, while the remote's Home key returns to the launcher.
"""

from collections.abc import Callable
import logging

from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.keyboard import KeyInjector, VirtualKey
from pideck.application.session import SessionSnapshot, SessionState
from pideck.domain.configuration import ApplicationDefinition, CecMode

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


class InputForwardingService:
    """Translate normalized remote actions into key presses for the running app."""

    def __init__(
        self,
        injector: KeyInjector,
        return_to_launcher: Callable[[], None],
        cec_mode: Callable[[ApplicationDefinition], CecMode],
    ) -> None:
        """Use ``injector`` for keys, ``return_to_launcher`` for Home, ``cec_mode`` per app."""
        self._injector = injector
        self._return_to_launcher = return_to_launcher
        self._cec_mode = cec_mode

    def handle(self, event: InputEvent, session: SessionSnapshot, *, pideck_has_focus: bool) -> bool:
        """Forward ``event`` if the active application keeps CEC; return whether it was consumed.

        Unconsumed events belong to PiDeck's own windows. Keys are synthesized only
        while the application is running and PiDeck has no focus, so a remote press
        can never activate PiDeck's own controls by accident. Home always returns to
        the launcher, also while the application is still starting.
        """
        application = session.application
        if application is None or session.state is SessionState.IDLE:
            return False
        if self._cec_mode(application) is not CecMode.KEYBOARD:
            return False
        if not event.pressed:
            return True
        if event.action is InputAction.HOME:
            _LOGGER.info("Remote Home pressed; returning to the launcher from %s", application.identifier)
            self._return_to_launcher()
            return True
        if session.state is not SessionState.RUNNING or pideck_has_focus:
            return True
        key = _KEYS.get(event.action)
        if key is not None:
            self._injector.tap(key)
        return True
