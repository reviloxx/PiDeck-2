"""NetworkManager CLI adapter for the appliance network setup."""

import os
import string
import subprocess

from pideck.application.ports.network import NetworkError, WirelessNetwork


def _fields(line: str) -> list[str]:
    """Decode nmcli's backslash-escaped, colon-separated output."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current.clear()
        else:
            current.append(character)
    if escaped:
        raise NetworkError("NetworkManager returned invalid escaped output")
    fields.append("".join(current))
    return fields


class NetworkManagerGateway:
    """Operate NetworkManager using noninteractive commands and private stdin."""

    def _run(
        self, arguments: list[str], *, secret: str | None = None, timeout: int = 20,
    ) -> str:
        try:
            result = subprocess.run(
                ["nmcli", *arguments],
                # nmcli preloads a saved key into its readline prompt on retries.
                input=None if secret is None else "\x15" + secret + "\n",
                stdin=subprocess.DEVNULL if secret is None else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise NetworkError("NetworkManager is unavailable or timed out") from error
        if result.returncode:
            # Never include command output: nmcli can echo prompts containing secrets.
            raise NetworkError("NetworkManager could not complete the request")
        return result.stdout

    def connected(self) -> bool:
        """Check for an active connection without requiring internet access."""
        state = self._run(["--terse", "--fields", "STATE", "general", "status"]).strip()
        if state not in {"connected", "connected (local only)", "connected (site only)", "connecting", "disconnected",
                         "disconnecting", "asleep", "unknown"}:
            raise NetworkError("NetworkManager returned an unknown connection state")
        if state == "connecting":
            try:
                result = subprocess.run(
                    ["nm-online", "--quiet", "--timeout=10"],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=12, check=False, env={**os.environ, "LC_ALL": "C"},
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise NetworkError("NetworkManager is unavailable or timed out") from error
            if result.returncode not in (0, 1):
                raise NetworkError("NetworkManager could not check the connection")
            return result.returncode == 0
        return state.startswith("connected")

    def scan(self) -> tuple[WirelessNetwork, ...]:
        """Return named access points, retaining the strongest duplicate."""
        output = self._run([
            "--terse", "--escape", "yes", "--fields", "SSID,DEVICE,SECURITY,SIGNAL,IN-USE",
            "device", "wifi", "list", "--rescan", "yes",
        ], timeout=30)
        networks: dict[tuple[str, str, str], WirelessNetwork] = {}
        for line in output.splitlines():
            if not line.strip():
                continue
            fields = _fields(line)
            if len(fields) != 5:
                raise NetworkError("NetworkManager returned invalid access point data")
            ssid, device, security, signal, in_use = fields
            if not ssid:
                continue
            try:
                strength = int(signal)
            except ValueError as error:
                raise NetworkError("NetworkManager returned invalid signal strength") from error
            if not 0 <= strength <= 100 or not device:
                raise NetworkError("NetworkManager returned invalid access point data")
            network = WirelessNetwork(ssid, device, security, strength, in_use == "*")
            identity = (ssid, device, security)
            previous = networks.get(identity)
            if previous is None or (network.connected, network.signal) > (previous.connected, previous.signal):
                networks[identity] = network
        return tuple(sorted(networks.values(), key=lambda item: (-int(item.connected), -item.signal, item.ssid)))

    def connect(self, network: WirelessNetwork, key: str | None) -> None:
        """Connect without putting the Wi-Fi key in process arguments or logs."""
        if not network.supported:
            raise NetworkError("This wireless security type is not supported")
        if network.requires_key:
            valid_passphrase = key is not None and 8 <= len(key) <= 63 and all(
                character in string.printable and character not in "\r\n\t\x0b\x0c" for character in key
            )
            valid_hex = key is not None and len(key) == 64 and all(
                character in string.hexdigits for character in key
            )
            if not valid_passphrase and not valid_hex:
                raise NetworkError("The Wi-Fi key must be 8 to 63 printable characters or 64 hexadecimal digits")
        elif key is not None:
            raise NetworkError("Open networks do not use a Wi-Fi key")
        arguments = [
            "--wait", "45", "--ask", "device", "wifi", "connect", network.ssid,
            "ifname", network.device,
        ]
        self._run(arguments, secret=key, timeout=60)
