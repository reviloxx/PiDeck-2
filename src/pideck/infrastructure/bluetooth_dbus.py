"""Bounded asynchronous D-Bus calls for the Bluetooth adapter and agent."""

from PySide6.QtCore import QCoreApplication, QEventLoop, QThread
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher

from pideck.application.ports.bluetooth import BluetoothError


def call_dbus(
    bus: QDBusConnection, service: str, path: str, interface: str, method: str,
    arguments: tuple[object, ...] = (), *, timeout: int = 10000,
) -> list[object]:
    """Wait for a bounded D-Bus reply while continuing to dispatch Qt events."""
    app = QCoreApplication.instance()
    if not bus.isConnected() or app is None:
        raise BluetoothError("The system D-Bus is unavailable")
    if QThread.currentThread() != app.thread():
        raise BluetoothError("Bluetooth operations must run on the Qt event thread")
    message = QDBusMessage.createMethodCall(service, path, interface, method)
    message.setArguments(list(arguments))
    watcher = QDBusPendingCallWatcher(bus.asyncCall(message, timeout))
    loop = QEventLoop()
    watcher.finished.connect(loop.quit)
    if not watcher.isFinished():
        loop.exec()
    if not watcher.isFinished():
        watcher.deleteLater()
        raise BluetoothError(f"Bluetooth service did not complete {method}")
    reply = watcher.reply()
    watcher.deleteLater()
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        raise BluetoothError(reply.errorMessage() or f"Bluetooth service rejected {method}")
    return reply.arguments()
