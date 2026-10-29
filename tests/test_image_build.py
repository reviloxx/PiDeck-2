"""Tests for the Raspberry Pi image build inputs and session scripts."""

import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from image import build as image_build
from image.imager_repo import write_repository
from pideck.infrastructure.config.parser import YamlConfigurationParser
from pideck.infrastructure.theme.loader import FileThemeRepository

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "image" / "stage-pideck"
FILES = STAGE / "00-pideck" / "files"


def test_github_image_workflow_has_valid_shell_and_artifact_inputs() -> None:
    """Keep the manual ARM64 build and downloadable image bundle wired together."""
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "raspberry-pi-image.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert "workflow_dispatch" in workflow["on"]
    job = workflow["jobs"]["build"]
    assert job["runs-on"] == "ubuntu-24.04-arm"
    steps = {step["name"]: step for step in job["steps"]}
    assert 'image/build.py --work-dir "$RUNNER_TEMP/pideck-build"' in steps["Build image"]["run"]
    assert "source-manifest.json" in steps["Prepare downloadable files"]["run"]
    assert "image/imager_repo.py" in steps["Prepare downloadable files"]["run"]
    upload = steps["Upload image and manifests"]["with"]
    assert upload["path"] == "${{ runner.temp }}/pideck-build/pi-gen/deploy/"
    assert upload["if-no-files-found"] == "error"
    for step in job["steps"]:
        if "run" in step:
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)


def test_github_artifact_preparation_keeps_image_usable(tmp_path: Path) -> None:
    """The workflow bundles the checksum and a generator usable after relocation."""
    workflow = yaml.load(
        (ROOT / ".github" / "workflows" / "raspberry-pi-image.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    steps = workflow["jobs"]["build"]["steps"]
    prepare = next(step["run"] for step in steps if step["name"] == "Prepare downloadable files")
    work = tmp_path / "pideck-build"
    deploy = work / "pi-gen" / "deploy"
    deploy.mkdir(parents=True)
    image = deploy / "pideck.img.xz"
    image.write_bytes(lzma.compress(b"disk image"))
    (deploy / "SHA256SUMS").write_text(f"{hashlib.sha256(image.read_bytes()).hexdigest()}  {image.name}\n")
    (work / "source-manifest.json").write_text("{}\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    sudo = commands / "sudo"
    sudo.write_text('#!/bin/sh\nexec "$@"\n')
    sudo.chmod(0o755)
    subprocess.run(
        ["bash", "-e", "-c", prepare],
        cwd=ROOT,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path), "PATH": f"{commands}:{os.environ['PATH']}"},
        check=True,
        capture_output=True,
    )

    extracted = tmp_path / "downloaded"
    shutil.copytree(deploy, extracted)
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=extracted, check=True)
    subprocess.run(
        [sys.executable, str(extracted / "imager_repo.py"), str(extracted / image.name)],
        check=True,
    )
    entry = json.loads((extracted / "pideck-imager-repository.json").read_text())["os_list"][0]
    assert entry["url"] == (extracted / image.name).as_uri()
    assert (extracted / "source-manifest.json").is_file()


def make_fake_pi_gen(path: Path) -> None:
    """Create the pinned stage paths needed by image preparation."""
    package_dir = path / "stage2" / "01-sys-tweaks"
    package_dir.mkdir(parents=True)
    (package_dir / "00-packages").write_text(
        " ".join(sorted(image_build.EXCLUDED_LITE_PACKAGES)) + " sudo\n"
    )
    (path / "stage2" / "03-accept-mathematica-eula").mkdir()
    (path / "stage2" / "04-cloud-init").mkdir()
    (path / "export-image" / "01-user-rename").mkdir(parents=True)


def test_image_configuration_and_assets(tmp_path: Path) -> None:
    """Image tiles, commands, package names, and asset paths agree."""
    config = YamlConfigurationParser().load(FILES / "pideck.yaml")
    applications = {app.identifier: app for app in config.applications}
    assert set(applications) == {"kodi", "chromium", "firefox", "retroarch"}
    assert applications["firefox"].executable == "firefox-esr"
    assert set(config.home.visible_applications) == set(applications)
    packages = (STAGE / "00-pideck" / "00-packages-nr").read_text().split()
    assert {app.executable for app in applications.values()} <= set(packages)

    shutil.copytree(ROOT / "assets", tmp_path / "assets")
    asset_root = tmp_path / "config"
    asset_root.mkdir()
    repository = FileThemeRepository(config.themes, asset_root, config.themes[0])
    theme = repository.get("default")
    assert theme.wallpaper is not None and theme.wallpaper.is_file()
    assert all(icon.is_file() for icon in theme.icons.values())
    assert all((asset_root / app.icon).is_file() for app in applications.values() if app.icon)


def test_prepare_stage_copies_snapshot_and_enables_only_custom_export(tmp_path: Path) -> None:
    """Prepare a separate pi-gen stage without mutating the project checkout."""
    pi_gen = tmp_path / "pi-gen"
    make_fake_pi_gen(pi_gen)

    stage = image_build.prepare_stage(ROOT, pi_gen)
    config = image_build.write_build_config(pi_gen)
    manifest = tmp_path / "source-manifest.json"
    image_build.write_source_manifest(stage, manifest)

    assert (pi_gen / "stage2" / "SKIP_IMAGES").is_file()
    assert (pi_gen / "stage2" / "03-accept-mathematica-eula" / "SKIP").is_file()
    assert (pi_gen / "stage2" / "04-cloud-init" / "SKIP").is_file()
    export_script = pi_gen / "export-image" / "01-user-rename" / "02-run-chroot.sh"
    assert "systemctl enable getty@tty1.service" in export_script.read_text()
    assert "dpkg-query" in export_script.read_text()
    assert (pi_gen / "stage2" / "01-sys-tweaks" / "00-packages").read_text() == "sudo\n"
    assert os.access(stage / "prerun.sh", os.X_OK)
    assert os.access(stage / "00-pideck" / "01-run.sh", os.X_OK)
    assert (stage / "00-pideck" / "files" / "project" / "src" / "pideck" / "bootstrap.py").is_file()
    assert not list(stage.rglob("__pycache__"))
    assert "stage0 stage1 stage2 stage-pideck" in config.read_text()
    assert "ENABLE_CLOUD_INIT=0" in config.read_text()
    source_manifest = json.loads(manifest.read_text())
    assert source_manifest["pi_gen_commit"] == image_build.PI_GEN_COMMIT
    assert len(source_manifest["imager_repository_generator_sha256"]) == 64
    assert "00-pideck/files/project/src/pideck/bootstrap.py" in source_manifest["files"]


def test_build_rejects_non_native_host_before_creating_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail safely rather than silently using unsupported cross-architecture emulation."""
    monkeypatch.setattr(image_build.platform, "machine", lambda: "x86_64")
    work = tmp_path / "work"

    with pytest.raises(RuntimeError, match="native ARM64"):
        image_build.build(work)
    assert not work.exists()


def test_build_rejects_spaces_before_creating_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject paths pi-gen cannot handle without touching them."""
    monkeypatch.setattr(image_build.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(image_build.platform, "system", lambda: "Linux")
    monkeypatch.setattr(image_build.os, "geteuid", lambda: 0)
    work = tmp_path / "space in path"

    with pytest.raises(ValueError, match="whitespace"):
        image_build.build(work)
    assert not work.exists()


def test_build_stages_inputs_and_checksums_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise build orchestration with a fake pi-gen, without running a build."""
    monkeypatch.setattr(image_build.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(image_build.platform, "system", lambda: "Linux")
    monkeypatch.setattr(image_build.os, "geteuid", lambda: 0)
    commands: list[list[str]] = []

    def run(
        command: list[str],
        *,
        check: bool,
        env: dict[str, str],
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert check
        assert env["HOME"] == str(tmp_path / "build")
        assert env["TMPDIR"] == "/tmp"
        assert "WORK_DIR" not in env
        assert "DEPLOY_DIR" not in env
        if command[:2] == ["git", "clone"]:
            make_fake_pi_gen(Path(command[-1]))
        elif command[0] == "./build.sh":
            assert cwd is not None
            deploy = cwd / "deploy"
            deploy.mkdir()
            (deploy / "test-pideck.img.xz").write_bytes(lzma.compress(b"test image"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(image_build.subprocess, "run", run)
    image = image_build.build(tmp_path / "build")

    assert image.name == "test-pideck.img.xz"
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    assert (image.parent / "SHA256SUMS").read_text() == f"{digest}  {image.name}\n"
    repo = json.loads((image.parent / "pideck-imager-repository.json").read_text())
    assert repo["os_list"][0]["extract_sha256"] == hashlib.sha256(b"test image").hexdigest()
    assert repo["os_list"][0]["init_format"] == "systemd"
    assert (tmp_path / "build" / "source-manifest.json").is_file()
    assert image_build.PI_GEN_COMMIT in commands[1]


def test_imager_repository_tracks_relocated_image(tmp_path: Path) -> None:
    """Generate accurate customization metadata, including a safe local URL."""
    image = tmp_path / "image with spaces.img.xz"
    image.write_bytes(lzma.compress(b"disk contents"))

    entry = json.loads(write_repository(image).read_text())["os_list"][0]

    assert entry["url"] == image.as_uri()
    assert entry["extract_size"] == len(b"disk contents")
    assert entry["extract_sha256"] == hashlib.sha256(b"disk contents").hexdigest()
    assert entry["image_download_size"] == image.stat().st_size
    assert entry["devices"] == ["pi5"]
    assert entry["init_format"] == "systemd"
    assert entry["architecture"] == "armv8"


def test_imager_repository_rejects_empty_image(tmp_path: Path) -> None:
    """Do not generate success-shaped metadata for an empty disk image."""
    image = tmp_path / "empty.img.xz"
    image.write_bytes(lzma.compress(b""))

    with pytest.raises(ValueError, match="empty"):
        write_repository(image)
    assert not (tmp_path / "pideck-imager-repository.json").exists()


def test_stage_installs_files_into_rootfs(tmp_path: Path) -> None:
    """The host-side stage installs the source, assets and session files."""
    pi_gen = tmp_path / "pi-gen"
    make_fake_pi_gen(pi_gen)
    stage = image_build.prepare_stage(ROOT, pi_gen) / "00-pideck"
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    stub = commands / "on_chroot"
    stub.write_text("#!/bin/sh\ncat > \"$CHROOT_COMMANDS\"\n")
    stub.chmod(0o755)
    env = {
        **os.environ,
        "ROOTFS_DIR": str(rootfs),
        "CHROOT_COMMANDS": str(tmp_path / "chroot-commands"),
        "PATH": f"{commands}:{os.environ['PATH']}",
    }

    subprocess.run(["bash", "01-run.sh"], cwd=stage, env=env, check=True)

    assert (rootfs / "usr/lib/pideck/python/pideck/bootstrap.py").is_file()
    assert (rootfs / "usr/share/pideck/assets/icons/kodi.svg").is_file()
    assert (rootfs / "usr/share/pideck/config/pideck.yaml").is_file()
    assert os.access(rootfs / "usr/lib/pideck/pideck-session", os.X_OK)
    assert (rootfs / "etc/systemd/system/getty@tty1.service.d/pideck.conf").is_file()
    assert (rootfs / "etc/systemd/system/userconfig.service.d/pideck.conf").is_file()
    assert "After=userconfig.service" in (FILES / "getty-override.conf").read_text()
    assert "systemctl enable NetworkManager" in (tmp_path / "chroot-commands").read_text()
    assert (rootfs / "usr/lib/pideck-updater/python/pideck/infrastructure/image_update_daemon.py").is_file()
    assert (rootfs / "etc/systemd/system/pideck-updater.service").is_file()
    assert (rootfs / "etc/systemd/system/pideck-activate.service").is_file()


def test_release_image_installs_versioned_tree_and_trusted_key(tmp_path: Path) -> None:
    """Release image inputs install a pinned key and one atomic release tree."""
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
    pi_gen = tmp_path / "pi-gen"
    make_fake_pi_gen(pi_gen)
    stage = image_build.prepare_stage(ROOT, pi_gen, f"v{version}", public) / "00-pideck"
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    stub = commands / "on_chroot"
    stub.write_text("#!/bin/sh\ncat > /dev/null\n")
    stub.chmod(0o755)
    subprocess.run(
        ["bash", "01-run.sh"], cwd=stage, check=True,
        env={**os.environ, "ROOTFS_DIR": str(rootfs), "PATH": f"{commands}:{os.environ['PATH']}"},
    )
    root = rootfs / "usr/lib/pideck"
    assert os.readlink(root / "current") == f"releases/{version}"
    assert (root / "current/python/pideck/bootstrap.py").is_file()
    assert (root / "current/assets/icons/settings.svg").is_file()
    assert (root / "current/config").is_dir()
    assert (rootfs / "usr/share/pideck/update-public.pem").read_bytes() == public.read_bytes()


def test_release_image_requires_both_tag_and_key(tmp_path: Path) -> None:
    """A partial release configuration must not silently build an untrusted image."""
    pi_gen = tmp_path / "pi-gen"
    make_fake_pi_gen(pi_gen)
    with pytest.raises(ValueError, match="supplied together"):
        image_build.prepare_stage(ROOT, pi_gen, release_tag="v0.1.0")


def test_release_image_requires_clean_matching_tag(tmp_path: Path) -> None:
    """Release image sources must exactly match their tagged checkout."""
    project = tmp_path / "project"
    (project / "src/pideck").mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (project / "src/pideck/__init__.py").write_text('__version__ = "1.0.0"\n')

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)

    git("init", "-q")
    git("add", "pyproject.toml", "src")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-q",
        "-m", "Test release\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>")
    git("tag", "v1.0.0")
    image_build._validate_release_checkout(project, "v1.0.0")
    (project / "src/pideck/__init__.py").write_text('__version__ = "1.0.0"\n# modified\n')
    with pytest.raises(ValueError, match="clean tagged checkout"):
        image_build._validate_release_checkout(project, "v1.0.0")


@pytest.mark.parametrize(
    ("script", "shell"),
    [
        (STAGE / "prerun.sh", "bash"),
        (STAGE / "00-pideck" / "01-run.sh", "bash"),
        (ROOT / "image" / "export" / "02-run-chroot.sh", "bash"),
        (FILES / "pideck-getty", "sh"),
        (FILES / "pideck-profile.sh", "sh"),
        (FILES / "pideck-session", "sh"),
        (FILES / "pideck-xsession", "sh"),
    ],
)
def test_session_scripts_have_valid_syntax(script: Path, shell: str) -> None:
    """All image shell scripts parse using their intended shell."""
    subprocess.run([shell, "-n", str(script)], check=True)


def test_session_preserves_configuration_and_stops_after_x_failure(tmp_path: Path) -> None:
    """Initialize configuration once and avoid restarting a failed X session."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, body in {
        "dbus-run-session": 'echo "x session failed" >&2; exit 1',
        "sleep": 'printf "sleep %s\\n" "$1"',
    }.items():
        path = commands / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    template = tmp_path / "template.yaml"
    template.write_text("default\n")
    script = tmp_path / "session"
    script.write_text(
        (FILES / "pideck-session").read_text().replace(
            "/usr/share/pideck/config/pideck.yaml", str(template)
        )
    )
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "PATH": f"{commands}:{os.environ['PATH']}",
    }

    result = subprocess.run(["sh", str(script)], env=env, text=True, capture_output=True, check=True)
    configuration = tmp_path / "config" / "pideck" / "pideck.yaml"
    assert configuration.read_text() == "default\n"
    assert "session failed" in result.stderr
    assert result.stdout.strip() == "sleep infinity"

    configuration.write_text("user changes\n")
    subprocess.run(["sh", str(script)], env=env, capture_output=True, check=True)
    assert configuration.read_text() == "user changes\n"


def test_xsession_reports_window_manager_failure(tmp_path: Path) -> None:
    """Do not start PiDeck without its required X11 window manager."""
    for name, body in {"xset": "exit 0", "wmctrl": "exit 1", "openbox": "exit 1"}.items():
        path = tmp_path / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    result = subprocess.run(
        ["sh", str(FILES / "pideck-xsession")],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode != 0
    assert "Openbox failed to start" in result.stderr


@pytest.mark.parametrize(
    ("account", "status", "expected"),
    [
        ("alice:x:1000:1000::/home/alice:/bin/bash", "alice P 01/01/2026 0 99999 7 -1", True),
        ("pi:x:1000:1000::/home/pi:/bin/bash", "pi L 01/01/2026 0 99999 7 -1", False),
        ("", "", False),
    ],
)
def test_getty_requires_provisioned_unlocked_user(
    tmp_path: Path, account: str, status: str, expected: bool
) -> None:
    """Autologin follows UID 1000 after rename and refuses a locked account."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, body in {
        "getent": 'printf "%s\\n" "$TEST_ACCOUNT"',
        "passwd": 'printf "%s\\n" "$TEST_STATUS"',
        "agetty": 'printf "%s\\n" "$@"',
    }.items():
        path = commands / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    script = tmp_path / "getty"
    script.write_text((FILES / "pideck-getty").read_text().replace("/sbin/agetty", str(commands / "agetty")))
    result = subprocess.run(
        ["sh", str(script), "tty1"],
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TEST_ACCOUNT": account,
            "TEST_STATUS": status,
            "TERM": "linux",
        },
        text=True,
        capture_output=True,
        check=True,
    )
    arguments = result.stdout.splitlines()
    assert ("--autologin" in arguments) is expected
    if expected:
        assert arguments == ["--autologin", "alice", "--noclear", "tty1", "linux"]
    else:
        assert "requires a user" in result.stderr
