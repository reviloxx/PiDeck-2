"""BlueZ pairing agent that delegates every authorization decision to the UI."""

from uuid import uuid4
from collections.abc import Callable

from PySide6.QtCore import ClassInfo, QEventLoop, QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusObjectPath

from pideck.application.ports.bluetooth import BluetoothError, PairingRequest
from pideck.infrastructure.bluetooth_dbus import call_dbus


@ClassInfo({"D-Bus Interface": "org.bluez.Agent1"})  # pyright: ignore[reportCallIssue]
class BluezAgent(QObject):
    """Export a temporary agent and reject unrequested or unauthorized calls."""

    requested = Signal(object)
    notice = Signal(object)
    cancelled = Signal()

    def __init__(self, bus: QDBusConnection | None = None, parent: QObject | None = None) -> None:
        """Register a private agent object with the system D-Bus connection."""
        QObject.__init__(self, parent)
        self._bus = bus or QDBusConnection.systemBus()
        if not self._bus.isConnected():
            raise BluetoothError("The system D-Bus is unavailable")
        try:
            owner = call_dbus(
                self._bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetNameOwner", ("org.bluez",),
            )
        except BluetoothError as error:
            raise BluetoothError("The Bluetooth service is unavailable") from error
        if len(owner) != 1 or not isinstance(owner[0], str) or not owner[0]:
            raise BluetoothError("The Bluetooth service is unavailable")
        self._bluez_owner = owner[0]
        self.path = f"/org/pideck/agent/a{uuid4().hex}"
        if not self._bus.registerObject(
            self.path, self, QDBusConnection.RegisterOption.ExportAllSlots,
        ):
            raise BluetoothError("Unable to register the Bluetooth pairing agent")
        self._target: str | None = None
        self._loop: QEventLoop | None = None
        self._response: bool | str | int | None = None

    def on_request(self, callback: Callable[[PairingRequest], None]) -> None:
        """Subscribe to pairing requests from the active device."""
        self.requested.connect(callback)

    def on_notice(self, callback: Callable[[PairingRequest], None]) -> None:
        """Subscribe to codes that must be displayed to the user."""
        self.notice.connect(callback)

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Subscribe to pairing cancellation by BlueZ."""
        self.cancelled.connect(callback)

    def set_target(self, path: str | None) -> None:
        """Restrict prompts to the device explicitly selected for pairing."""
        self._target = path

    def respond(self, value: bool | str | int | None) -> None:
        """Complete the current prompt with a user response or cancellation."""
        if self._loop is None:
            return
        self._response = value
        self._loop.quit()

    def close(self) -> None:
        """Reject a pending prompt and unregister the local D-Bus object."""
        self.respond(None)
        self._bus.unregisterObject(self.path)

    def _reject(self, message: QDBusMessage, reason: str) -> None:
        message.setDelayedReply(True)
        self._bus.send(message.createErrorReply("org.bluez.Error.Rejected", reason))

    def _allowed(self, message: QDBusMessage, device: QDBusObjectPath | None = None) -> bool:
        if (
            message.service() != self._bluez_owner
            or (device is not None and (self._target is None or device.path() != self._target))
        ):
            self._reject(message, "Pairing request was not authorized")
            return False
        return True

    def _ask(self, message: QDBusMessage, request: PairingRequest) -> bool | str | int | None:
        if self._loop is not None:
            self._reject(message, "Another pairing request is active")
            return None
        self._response = None
        loop = QEventLoop(self)
        self._loop = loop
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.respond(None))
        timer.start(60000)
        QTimer.singleShot(0, lambda: self.requested.emit(request))
        loop.exec()
        timer.stop()
        timer.deleteLater()
        self._loop = None
        response = self._response
        self._response = None
        if response is None or response is False:
            self._reject(message, "Pairing was cancelled or rejected")
            self.cancelled.emit()
            return None
        return response

    @Slot(QDBusObjectPath, QDBusMessage, result=str)
    def RequestPinCode(self, device: QDBusObjectPath, message: QDBusMessage) -> str:
        """Request a user-supplied Bluetooth PIN."""
        if not self._allowed(message, device):
            return ""
        result = self._ask(message, PairingRequest("pin"))
        if not isinstance(result, str) or not result.isprintable() or not 1 <= len(result.encode("utf-8")) <= 16:
            if result is not None:
                self._reject(message, "Invalid Bluetooth PIN")
            return ""
        return result

    @Slot(QDBusObjectPath, QDBusMessage, result="uint")
    def RequestPasskey(self, device: QDBusObjectPath, message: QDBusMessage) -> int:
        """Request a user-supplied numeric Bluetooth passkey."""
        if not self._allowed(message, device):
            return 0
        result = self._ask(message, PairingRequest("passkey"))
        if type(result) is not int or not 0 <= result <= 999999:
            if result is not None:
                self._reject(message, "Invalid Bluetooth passkey")
            return 0
        return result

    @Slot(QDBusObjectPath, "uint", QDBusMessage)
    def RequestConfirmation(self, device: QDBusObjectPath, passkey: int, message: QDBusMessage) -> None:
        """Ask the user to verify the displayed pairing passkey."""
        if self._allowed(message, device):
            response = self._ask(message, PairingRequest("confirmation", f"{passkey:06d}"))
            if response is not None and response is not True:
                self._reject(message, "Pairing was not confirmed")

    @Slot(QDBusObjectPath, QDBusMessage)
    def RequestAuthorization(self, device: QDBusObjectPath, message: QDBusMessage) -> None:
        """Ask the user to authorize a pairing request explicitly."""
        if self._allowed(message, device):
            response = self._ask(message, PairingRequest("authorization"))
            if response is not None and response is not True:
                self._reject(message, "Pairing was not authorized")

    @Slot(QDBusObjectPath, str, QDBusMessage)
    def DisplayPinCode(self, device: QDBusObjectPath, pin: str, message: QDBusMessage) -> None:
        """Show a PIN that must be entered on the device."""
        if self._allowed(message, device):
            self.notice.emit(PairingRequest("display_pin", pin))

    @Slot(QDBusObjectPath, "uint", "ushort", QDBusMessage)
    def DisplayPasskey(self, device: QDBusObjectPath, passkey: int, entered: int, message: QDBusMessage) -> None:
        """Show a passkey that must be entered on the device."""
        if self._allowed(message, device):
            self.notice.emit(PairingRequest("display_passkey", f"{passkey:06d}"))

    @Slot(QDBusObjectPath, str, QDBusMessage)
    def AuthorizeService(self, device: QDBusObjectPath, uuid: str, message: QDBusMessage) -> None:
        """Reject unrelated service authorization requests."""
        if self._allowed(message, device):
            self._reject(message, "Service authorization is not supported")

    @Slot(QDBusMessage)
    def Cancel(self, message: QDBusMessage) -> None:
        """Cancel the active pairing prompt when BlueZ abandons it."""
        if self._allowed(message):
            self.respond(None)
            self.cancelled.emit()

    @Slot(QDBusMessage)
    def Release(self, message: QDBusMessage) -> None:
        """Reject pending interaction when BlueZ releases the agent."""
        if self._allowed(message):
            self.respond(None)
            self.cancelled.emit()
