"""PulseAudio-compatible audio operations for Linux desktop sessions."""

import json
import os
import subprocess

from pideck.application.ports.audio import AudioError, AudioOutput, AudioState


class PulseAudioGateway:
    """Operate a PulseAudio or PipeWire PulseAudio-compatible server."""

    def _run(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ["pactl", *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AudioError("The audio service is unavailable or timed out") from error
        if result.returncode:
            raise AudioError(result.stderr.strip() or "The audio service rejected the request")
        return result.stdout

    def inspect(self) -> AudioState:
        """Read output devices and the active output's volume."""
        try:
            info = json.loads(self._run("--format=json", "info"))
            sinks = json.loads(self._run("--format=json", "list", "sinks"))
        except json.JSONDecodeError as error:
            raise AudioError("The audio service returned invalid data") from error
        if not isinstance(info, dict) or not isinstance(sinks, list):
            raise AudioError("The audio service returned invalid data")
        default = info.get("default_sink_name")
        outputs: list[AudioOutput] = []
        current: int | None = None
        current_volume: int | None = None
        for sink in sinks:
            if not isinstance(sink, dict):
                raise AudioError("The audio service returned invalid output data")
            identifier = sink.get("index")
            name = sink.get("name")
            description = sink.get("description")
            if type(identifier) is not int or identifier < 0 or not isinstance(name, str) or not name:
                raise AudioError("The audio service returned invalid output data")
            if not isinstance(description, str) or not description:
                description = name
            outputs.append(AudioOutput(identifier, name, description))
            if name == default:
                volume = sink.get("volume")
                if not isinstance(volume, dict) or not volume:
                    raise AudioError("The audio service returned invalid volume data")
                channels: list[int] = []
                for item in volume.values():
                    if not isinstance(item, dict):
                        raise AudioError("The audio service returned invalid volume data")
                    value = item.get("value")
                    if type(value) is not int or value < 0:
                        raise AudioError("The audio service returned invalid volume data")
                    channels.append(value)
                current = identifier
                current_volume = min(100, round(sum(channels) / len(channels) * 100 / 65536))
        if current is None or current_volume is None:
            raise AudioError("No active audio output is available")
        return AudioState(tuple(outputs), current, current_volume)

    def set_output(self, identifier: int) -> None:
        """Change the default output by its server-provided numeric identifier."""
        if type(identifier) is not int or identifier < 0:
            raise AudioError("Invalid audio output")
        self._run("set-default-sink", str(identifier))

    def set_volume(self, volume: int) -> None:
        """Set the default output volume without raising it above 100 percent."""
        if type(volume) is not int or not 0 <= volume <= 100:
            raise AudioError("Invalid audio volume")
        self._run("set-sink-volume", "@DEFAULT_SINK@", f"{volume}%")
