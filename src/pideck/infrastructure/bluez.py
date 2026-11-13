"""BlueZ adapter and device operations over the system D-Bus."""

from enum import Enum
import logging
import re
import shutil
import subprocess
from threading import Event
import xml.etree.ElementTree as ET

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtDBus import QDBusConnection, QDBusObjectPath, QDBusVariant

from pideck.application.ports.bluetooth import BluetoothDevice, BluetoothError, BluetoothState
from pideck.infrastructure.bluetooth_dbus import call_dbus

_LOGGER = logging.getLogger(__name__)


_SERVICE = "org.bluez"
_ADAPTER = "org.bluez.Adapter1"
_DEVICE = "org.bluez.Device1"
_AGENT_MANAGER = "org.bluez.AgentManager1"
_PROPERTIES = "org.freedesktop.DBus.Properties"
_INTROSPECT = "org.freedesktop.DBus.Introspectable"
_ADAPTER_NAME = re.compile(r"hci[0-9]+\Z")
_DEVICE_NAME = re.compile(r"dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}\Z")
_DEVICE_PATH = re.compile(r"/org/bluez/hci[0-9]+/dev_(?:[0-9A-F]{2}_){5}[0-9A-F]{2}\Z")
def is_reconnect_gamepad(alias: str, icon: str = "") -> bool:
    """Return whether a BlueZ device should be auto-reconnected as a controller."""
    if icon == "input-gaming":
        return True
    folded = (alias or "").casefold()
    return (
        "nintendo" in folded
        or "joy-con" in folded
        or "joycon" in folded
        or folded.strip() == "pro controller"
        or folded.startswith("pro controller ")
        or any(word in folded for word in ("gamepad", "controller", "joystick"))
    )


class ConnectAttempt(Enum):
    """Outcome of one auto-reconnect Device1.Connect call."""

    OK = "ok"
    FAILED = "failed"
    ASLEEP = "asleep"
    TIMEOUT = "timeout"
    BUSY = "busy"


class BluezGateway:
    """Use BlueZ's standard adapter and device interfaces without shell commands."""

    def __init__(self, agent_path: str, bus: QDBusConnection | None = None) -> None:
        """Create an adapter bound to the pairing agent's D-Bus object path."""
        self._bus = bus or QDBusConnection.systemBus()
        self._agent_path = agent_path
        self._discovery_loop: QEventLoop | None = None

    @property
    def bus(self) -> QDBusConnection:
        """Return the system bus used for BlueZ method calls and watches."""
        return self._bus

    def _call(self, path: str, interface: str, method: str, *arguments: object, timeout: int = 10000) -> list[object]:
        return call_dbus(self._bus, _SERVICE, path, interface, method, arguments, timeout=timeout)

    def _children(self, path: str) -> tuple[str, ...]:
        result = self._call(path, _INTROSPECT, "Introspect")
        if len(result) != 1 or not isinstance(result[0], str) or len(result[0]) > 1_000_000:
            raise BluetoothError("BlueZ returned invalid device information")
        try:
            root = ET.fromstring(result[0])
        except ET.ParseError as error:
            raise BluetoothError("BlueZ returned invalid device information") from error
        return tuple(name for node in root.findall("node") if (name := node.get("name")) is not None)

    def _adapter(self) -> str:
        adapters = sorted(name for name in self._children("/org/bluez") if _ADAPTER_NAME.fullmatch(name))
        if not adapters:
            raise BluetoothError("No Bluetooth adapter is available")
        return f"/org/bluez/{adapters[0]}"

    def _property(self, path: str, interface: str, name: str) -> object:
        result = self._call(path, _PROPERTIES, "Get", interface, name)
        if len(result) != 1 or not isinstance(result[0], QDBusVariant):
            raise BluetoothError("BlueZ returned an invalid property")
        return result[0].variant()

    def _device_path(self, path: str) -> str:
        if not _DEVICE_PATH.fullmatch(path):
            raise BluetoothError("Invalid Bluetooth device")
        adapter = self._adapter()
        if not path.startswith(adapter + "/") or path.rsplit("/", 1)[-1] not in self._children(adapter):
            raise BluetoothError("The Bluetooth device is no longer available")
        return path

    def inspect(self) -> BluetoothState:
        """Read the first available adapter and its currently known devices."""
        adapter = self._adapter()
        powered = self._property(adapter, _ADAPTER, "Powered")
        if type(powered) is not bool:
            raise BluetoothError("BlueZ returned invalid adapter state")
        devices: list[BluetoothDevice] = []
        for name in self._children(adapter):
            if not _DEVICE_NAME.fullmatch(name):
                continue
            path = f"{adapter}/{name}"
            alias = self._property(path, _DEVICE, "Alias")
            paired = self._property(path, _DEVICE, "Paired")
            connected = self._property(path, _DEVICE, "Connected")
            if not isinstance(alias, str) or type(paired) is not bool or type(connected) is not bool:
                raise BluetoothError("BlueZ returned invalid device state")
            devices.append(BluetoothDevice(path, alias, paired, connected))
        return BluetoothState(powered, tuple(sorted(devices, key=lambda item: (not item.connected, item.name.casefold()))))

    def set_power(self, enabled: bool) -> BluetoothState:
        """Change adapter power using its standard D-Bus property."""
        if type(enabled) is not bool:
            raise BluetoothError("Invalid Bluetooth power state")
        adapter = self._adapter()
        self._call(adapter, _PROPERTIES, "Set", _ADAPTER, "Powered", QDBusVariant(enabled))
        return self.inspect()

    def discover(self, cancelled: Event) -> BluetoothState:
        """Run bounded discovery and release the discovery session on exit."""
        adapter = self._adapter()
        self._call(adapter, _ADAPTER, "StartDiscovery")
        try:
            if not cancelled.is_set():
                loop = QEventLoop()
                self._discovery_loop = loop
                QTimer.singleShot(8000, loop.quit)
                loop.exec()
                self._discovery_loop = None
        finally:
            self._discovery_loop = None
            self._call(adapter, _ADAPTER, "StopDiscovery")
        return self.inspect()

    def cancel_discovery(self) -> None:
        """Interrupt the bounded discovery wait without polling."""
        if self._discovery_loop is not None:
            self._discovery_loop.quit()

    def pair(self, path: str) -> BluetoothState:
        """Pair, trust, and attempt to connect so controllers become usable."""
        path = self._device_path(path)
        agent = QDBusObjectPath(self._agent_path)
        self._call("/org/bluez", _AGENT_MANAGER, "RegisterAgent", agent, "KeyboardDisplay")
        failure: BluetoothError | None = None
        try:
            self._call(path, _DEVICE, "Pair", timeout=90000)
        except BluetoothError as error:
            failure = error
        try:
            self._call("/org/bluez", _AGENT_MANAGER, "UnregisterAgent", agent)
        except BluetoothError as error:
            if failure is not None:
                raise BluetoothError(f"{failure}; unable to unregister pairing agent: {error}") from failure
            raise
        if failure is not None:
            raise failure
        self._call(path, _PROPERTIES, "Set", _DEVICE, "Trusted", QDBusVariant(True))
        try:
            self._call(path, _DEVICE, "Connect", timeout=30000)
        except BluetoothError:
            # Pairing and trust must stick even when the device is asleep.
            pass
        return self.inspect()

    def connect(self, path: str) -> BluetoothState:
        """Trust and connect one currently known device."""
        path = self._device_path(path)
        self._call(path, _PROPERTIES, "Set", _DEVICE, "Trusted", QDBusVariant(True))
        self._call(path, _DEVICE, "Connect", timeout=30000)
        return self.inspect()

    def trusted_gamepad_paths(self, *, connected: bool | None = None) -> tuple[str, ...]:
        """Return trusted paired gamepad paths, optionally filtered by Connected."""
        adapter = self._adapter()
        powered = self._property(adapter, _ADAPTER, "Powered")
        if powered is not True:
            return ()
        paths: list[str] = []
        for name in self._children(adapter):
            if not _DEVICE_NAME.fullmatch(name):
                continue
            path = f"{adapter}/{name}"
            alias = self._property(path, _DEVICE, "Alias")
            paired = self._property(path, _DEVICE, "Paired")
            trusted = self._property(path, _DEVICE, "Trusted")
            device_connected = self._property(path, _DEVICE, "Connected")
            if (
                not isinstance(alias, str)
                or paired is not True
                or trusted is not True
                or type(device_connected) is not bool
            ):
                continue
            if connected is not None and device_connected is not connected:
                continue
            icon = ""
            try:
                raw_icon = self._property(path, _DEVICE, "Icon")
            except BluetoothError:
                raw_icon = ""
            if isinstance(raw_icon, str):
                icon = raw_icon
            if not is_reconnect_gamepad(alias, icon):
                continue
            paths.append(path)
        return tuple(paths)

    def reconnect_candidates(self) -> tuple[str, ...]:
        """Return trusted paired gamepads that are not currently Connected."""
        return self.trusted_gamepad_paths(connected=False)

    def device_is_connected(self, path: str) -> bool:
        """Return whether BlueZ currently reports Connected for one device path."""
        try:
            path = self._device_path(path)
            connected = self._property(path, _DEVICE, "Connected")
        except BluetoothError:
            return False
        return connected is True

    def path_for_address(self, address: str) -> str | None:
        """Return the BlueZ device path for a Bluetooth MAC, if still present."""
        normalized = address.strip().upper().replace("-", ":")
        if not normalized:
            return None
        try:
            adapter = self._adapter()
            return self._device_path(f"{adapter}/dev_{normalized.replace(':', '_')}")
        except BluetoothError:
            return None

    def ensure_accepts_connections(self) -> None:
        """Turn on page scan / fast-connectable so paired controllers page in.

        CSR USB adapters often leave ``Connectable`` false after boot. BlueZ 5.82
        also omits the D-Bus ``FastConnectable`` property even though the
        controller supports it; enable that via ``btmgmt fast-conn on``.
        """
        try:
            adapter = self._adapter()
        except BluetoothError as error:
            _LOGGER.debug("Bluetooth page scan skipped: %s", error)
            return
        try:
            current = self._property(adapter, _ADAPTER, "Connectable")
        except BluetoothError:
            current = None
        if current is not True:
            try:
                self._call(adapter, _PROPERTIES, "Set", _ADAPTER, "Connectable", QDBusVariant(True))
            except BluetoothError as error:
                _LOGGER.debug("Unable to set adapter Connectable: %s", error)
        fast_set = False
        try:
            fast = self._property(adapter, _ADAPTER, "FastConnectable")
        except BluetoothError:
            fast = None
        else:
            if fast is True:
                fast_set = True
            elif fast is False:
                try:
                    self._call(
                        adapter, _PROPERTIES, "Set", _ADAPTER, "FastConnectable", QDBusVariant(True),
                    )
                    fast_set = True
                except BluetoothError as error:
                    _LOGGER.debug("Unable to set adapter FastConnectable: %s", error)
        if not fast_set:
            self._enable_fast_connectable_btmgmt(adapter)

    def _enable_fast_connectable_btmgmt(self, adapter_path: str) -> None:
        """Enable interlaced page scan when D-Bus FastConnectable is unavailable."""
        match = re.search(r"hci(\d+)\Z", adapter_path)
        index = match.group(1) if match else "0"
        btmgmt = shutil.which("btmgmt")
        if btmgmt is None:
            _LOGGER.debug("btmgmt unavailable; FastConnectable not enabled")
            return
        try:
            result = subprocess.run(
                [btmgmt, "--index", index, "fast-conn", "on"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            _LOGGER.debug("btmgmt fast-conn failed: %s", error)
            return
        if result.returncode != 0:
            _LOGGER.debug(
                "btmgmt fast-conn rejected: %s",
                (result.stderr or result.stdout or "").strip(),
            )

    def try_connect(self, path: str, *, timeout: int = 10000) -> ConnectAttempt:
        """Best-effort Connect for auto-reconnect.

        Does not Disconnect first: when ``Connected`` is false, Disconnect does not
        cancel an in-flight BR connect, and retrying Connect causes ``InProgress``.
        """
        try:
            path = self._device_path(path)
        except BluetoothError:
            return ConnectAttempt.FAILED
        try:
            self._call(path, _PROPERTIES, "Set", _DEVICE, "Trusted", QDBusVariant(True))
            self._call(path, _DEVICE, "Connect", timeout=timeout)
        except BluetoothError as error:
            message = str(error).casefold()
            if "did not complete" in message:
                return ConnectAttempt.TIMEOUT
            if "in progress" in message or "inprogress" in message or "br-connection-busy" in message:
                return ConnectAttempt.BUSY
            if (
                "host is down" in message
                or "not available" in message
                or "connection refused" in message
                or "no route" in message
            ):
                return ConnectAttempt.ASLEEP
            return ConnectAttempt.FAILED
        return ConnectAttempt.OK

    def disconnect_address(self, address: str) -> None:
        """Best-effort Disconnect for a Bluetooth MAC from a gamepad loss event."""
        normalized = address.strip().upper().replace("-", ":")
        if not normalized:
            return
        try:
            adapter = self._adapter()
            path = self._device_path(f"{adapter}/dev_{normalized.replace(':', '_')}")
            self._call(path, _DEVICE, "Disconnect", timeout=2000)
        except BluetoothError:
            return

    def cancel_pairing(self, path: str) -> None:
        """Ask BlueZ to cancel an active pairing operation."""
        self._call(self._device_path(path), _DEVICE, "CancelPairing")

    def disconnect(self, path: str) -> BluetoothState:
        """Disconnect one currently known device."""
        self._call(self._device_path(path), _DEVICE, "Disconnect", timeout=30000)
        return self.inspect()
