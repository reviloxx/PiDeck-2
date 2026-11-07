"""Verify TV remote forwarding into applications that keep PiDeck's CEC input."""

import ctypes

import pytest

from pideck.application.input_forwarding import InputForwardingService
from pideck.application.ports.input import InputAction, InputEvent
from pideck.application.ports.keyboard import VirtualKey
from pideck.application.session import SessionSnapshot, SessionState
from pideck.domain.configuration import ApplicationDefinition, CecMode
from pideck.infrastructure.x11_key_injector import X11KeyInjector


class RecordingInjector:
    """Collect synthesized keys."""

    def __init__(self) -> None:
        self.keys: list[VirtualKey] = []

    def tap(self, key: VirtualKey) -> None:
        self.keys.append(key)

    def close(self) -> None:
        pass


YOUTUBE = ApplicationDefinition("youtube", "YouTube", "flatpak", cec_mode=CecMode.KEYBOARD)
KODI = ApplicationDefinition("kodi", "Kodi", "kodi", cec_mode=CecMode.KEYBOARD)
RELEASING = ApplicationDefinition("steam-link", "Steam Link", "flatpak")
RUNNING = SessionSnapshot(SessionState.RUNNING, YOUTUBE)


def _service() -> tuple[InputForwardingService, RecordingInjector, list[str]]:
    injector = RecordingInjector()
    returns: list[str] = []
    service = InputForwardingService(
        injector, lambda: returns.append("home"), lambda app: app.cec_mode or CecMode.RELEASE,
    )
    return service, injector, returns


def test_remote_actions_become_keys_for_the_focused_application() -> None:
    """Navigation, OK, Back and transport keys map to ordinary key presses."""
    service, injector, returns = _service()
    for action in (
        InputAction.UP, InputAction.DOWN, InputAction.LEFT, InputAction.RIGHT,
        InputAction.ACTIVATE, InputAction.BACK, InputAction.PLAY, InputAction.PAUSE,
        InputAction.PLAY_PAUSE, InputAction.STOP, InputAction.REWIND, InputAction.FAST_FORWARD,
    ):
        assert service.handle(InputEvent(action, "cec"), RUNNING, pideck_has_focus=False)
    assert injector.keys == [
        VirtualKey.UP, VirtualKey.DOWN, VirtualKey.LEFT, VirtualKey.RIGHT,
        VirtualKey.RETURN, VirtualKey.ESCAPE, VirtualKey.MEDIA_PLAY, VirtualKey.MEDIA_PAUSE,
        VirtualKey.MEDIA_PLAY_PAUSE, VirtualKey.MEDIA_STOP, VirtualKey.MEDIA_REWIND,
        VirtualKey.MEDIA_FAST_FORWARD,
    ]
    assert returns == []


def test_home_returns_to_launcher_even_while_starting() -> None:
    """Home is never forwarded and always leaves the application."""
    service, injector, returns = _service()
    starting = SessionSnapshot(SessionState.STARTING, YOUTUBE)
    assert service.handle(InputEvent(InputAction.HOME, "cec"), starting, pideck_has_focus=True)
    assert service.handle(InputEvent(InputAction.HOME, "cec"), RUNNING, pideck_has_focus=False)
    assert returns == ["home", "home"]
    assert injector.keys == []


@pytest.mark.parametrize(
    ("session", "pideck_has_focus", "pressed"),
    [
        (SessionSnapshot(SessionState.STARTING, YOUTUBE), False, True),
        (SessionSnapshot(SessionState.STOPPING, YOUTUBE), False, True),
        (RUNNING, True, True),
        (RUNNING, False, False),
    ],
)
def test_keys_are_consumed_but_not_synthesized_when_unsafe(
    session: SessionSnapshot, pideck_has_focus: bool, pressed: bool,
) -> None:
    """PiDeck's own windows never receive synthesized keys or launcher input."""
    service, injector, returns = _service()
    event = InputEvent(InputAction.ACTIVATE, "cec", pressed=pressed)
    assert service.handle(event, session, pideck_has_focus=pideck_has_focus)
    assert injector.keys == [] and returns == []


@pytest.mark.parametrize(
    "session",
    [SessionSnapshot(SessionState.IDLE), SessionSnapshot(SessionState.RUNNING, RELEASING)],
)
def test_launcher_and_releasing_applications_are_not_forwarded(session: SessionSnapshot) -> None:
    """Idle launcher input and apps that own the adapter are left to PiDeck."""
    service, injector, returns = _service()
    for action in (InputAction.ACTIVATE, InputAction.HOME):
        assert not service.handle(InputEvent(action, "cec"), session, pideck_has_focus=False)
    assert injector.keys == [] and returns == []


def test_kodi_receives_forwarded_remote_keys() -> None:
    """Kodi keeps PiDeck's CEC connection and gets ordinary key presses."""
    service, injector, returns = _service()
    running = SessionSnapshot(SessionState.RUNNING, KODI)
    assert service.handle(InputEvent(InputAction.ACTIVATE, "cec"), running, pideck_has_focus=False)
    assert service.handle(InputEvent(InputAction.HOME, "cec"), running, pideck_has_focus=False)
    assert injector.keys == [VirtualKey.RETURN]
    assert returns == ["home"]


class FakeLibraries:
    """Stand-in for the ctypes Xlib/XTest bindings."""

    def __init__(self, *, display: int = 7, xtest: bool = True) -> None:
        self.display = display
        self.xtest = xtest
        self.events: list[tuple[int, int]] = []
        self.flushes = 0
        self.closed: list[int] = []
        self.opened = 0

    def open_display(self, name: bytes | None) -> int:
        self.opened += 1
        return self.display

    def close_display(self, display: int) -> None:
        self.closed.append(display)

    def query_extension(self, display: int, *values: object) -> int:
        return int(self.xtest)

    def string_to_keysym(self, name: bytes) -> int:
        return {b"Return": 0xFF0D, b"XF86AudioPlay": 0x1008FF14}.get(name, 0)

    def keysym_to_keycode(self, display: int, keysym: int) -> int:
        return {0xFF0D: 36, 0x1008FF14: 172}.get(keysym, 0)

    def fake_key_event(self, display: int, keycode: int, press: int, delay: int) -> int:
        self.events.append((keycode, press))
        return 1

    def flush(self, display: int) -> None:
        self.flushes += 1


def test_x11_injector_presses_and_releases_mapped_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each tap is a press and release; unmapped keys are skipped and logged."""
    monkeypatch.setenv("DISPLAY", ":0")
    libraries = FakeLibraries()
    injector = X11KeyInjector(libraries)  # type: ignore[arg-type]
    injector.tap(VirtualKey.RETURN)
    injector.tap(VirtualKey.MEDIA_PLAY_PAUSE)
    injector.tap(VirtualKey.MEDIA_STOP)
    assert libraries.events == [(36, 1), (36, 0), (172, 1), (172, 0)]
    assert libraries.opened == 1 and libraries.flushes == 2
    injector.close()
    assert libraries.closed == [7]


@pytest.mark.parametrize("libraries", [FakeLibraries(display=0), FakeLibraries(xtest=False)])
def test_x11_injector_disables_itself_without_display_or_xtest(
    monkeypatch: pytest.MonkeyPatch, libraries: FakeLibraries,
) -> None:
    """A missing X server or extension is reported once and never retried."""
    monkeypatch.setenv("DISPLAY", ":0")
    injector = X11KeyInjector(libraries)  # type: ignore[arg-type]
    injector.tap(VirtualKey.RETURN)
    injector.tap(VirtualKey.RETURN)
    assert libraries.events == [] and libraries.opened == 1


def test_x11_injector_without_display_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without DISPLAY no library is loaded."""
    monkeypatch.delenv("DISPLAY", raising=False)
    libraries = FakeLibraries()
    X11KeyInjector(libraries).tap(VirtualKey.RETURN)  # type: ignore[arg-type]
    assert libraries.opened == 0


def test_x11_libraries_are_available_on_this_system() -> None:
    """The real bindings load from the sonames that PiDeck images provide."""
    from pideck.infrastructure.x11_key_injector import _XLibraries

    try:
        libraries = _XLibraries()
    except OSError:
        pytest.skip("libX11/libXtst are not installed")
    assert isinstance(libraries.fake_key_event, ctypes._CFuncPtr)  # type: ignore[attr-defined]
