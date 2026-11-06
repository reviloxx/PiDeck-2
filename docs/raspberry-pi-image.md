# Raspberry Pi 5 image

The local image builder targets Raspberry Pi OS Lite 64-bit. It adds a minimal
X11 session that starts PiDeck after Raspberry Pi Imager provisions the user.
The image has been built successfully on a GitHub-hosted ARM64 runner, but
has not yet been tested on a Raspberry Pi 5. Treat it as experimental until
the hardware acceptance checklist below passes.

New builds configure a PiDeck Plymouth splash during Linux startup. Press
Escape for boot details; kernel and service logs remain in the journal.
Firmware output before Linux and display-driver behavior are platform-specific.
The splash and its hardware compatibility have not yet been verified on a Pi.
It uses the default theme's colors because user settings are unavailable early
in boot.
Existing installations need a separately applied system update or a newly
built image, since PiDeck application updates do not replace boot components.

## HDMI-CEC

Connect the Pi directly to a CEC-capable TV and enable the TV's CEC setting.
New images provide `cec-utils` and local-seat access to the built-in Linux CEC
device. In **Settings → Input**, CEC and auto-connect default to on, and TV
input switching defaults to off. Select automatic detection or the direct-TV
HDMI input. Some Pi drivers determine the address from the HDMI connection and
do not allow overriding it; if the selected address differs, PiDeck reports
an error instead of silently choosing a different port.

Manual Connect and Disconnect are available when auto-connect is off. A manual
connection resumes after an application exits, but starts disconnected after
a PiDeck restart. PiDeck releases CEC before starting an application, except
for applications with `cec: keyboard` such as YouTube: PiDeck then forwards the
remote's keys to the application, and the remote's Home key returns to PiDeck.
Configure
Kodi independently under **Settings → System → Input → Peripherals**, including
its HDMI address and source-activation setting. The kernel physical address is
shared; verify both applications on the TV.

The libCEC Linux backend uses `/dev/cec0`, so check which HDMI connector maps
to that device and that the local user can access it. Check Input status, the
PiDeck journal and the HDMI cable; do not run another CEC client concurrently.
Existing releases need the system setup described in the
[x86 guide](x86-image.md#existing-release-installations); an application
update alone cannot install udev rules or packages. On first use, the separate
input preferences enable CEC even when the previous main configuration omitted
or disabled it; subsequent user choices are preserved. Hardware testing,
including Kodi handoff, remains necessary.

Wallpaper scales proportionally to fill the window, cropping edges when
necessary. The default policies in `/usr/share/pideck/config/window-modes.yaml`
request maximized main windows for Chromium, Chrome, Brave, Firefox, RetroArch
and NoMachine; the application catalog declares the same defaults for releases
that know newer applications. Existing fullscreen windows stay fullscreen. User overrides can
be placed in `~/.config/pideck/window-modes.yaml`, for example `firefox: normal`.
Dialogs are not maximized, and the separate policy file keeps the existing
launcher settings compatible with older releases.

## Build

### GitHub Actions

The manually triggered **Build Raspberry Pi image** workflow uses a GitHub-hosted
ARM64 Ubuntu runner. Once the image-builder changes and workflow have been
committed and pushed, enable Actions if necessary. The workflow file must be
present on the default branch for manual runs to be available. In the
repository's **Actions** tab, select the workflow, choose **Run workflow**,
select the branch to build, and start it. Only committed files on the selected
branch are built; local uncommitted changes are not included.

To trigger an update-enabled release build from Git, an `image-vMAJOR.MINOR.PATCH`
tag can also be pushed at the same commit as the matching `vMAJOR.MINOR.PATCH`
release tag. The builder validates the release tag and source checkout before
building; an image tag alone does not publish an application release.

After a successful run, download and extract the
`pideck-raspberry-pi-image` artifact. It contains the `.img.xz`, `SHA256SUMS`,
the pi-gen package information, the source manifest, and `imager_repo.py`.
Artifacts expire after seven days. If the build fails, check the Actions log and
the `pideck-build-log` artifact when available; no image artifact is published
unless the build succeeds.

The standard ARM64 runner has limited disk space. Although one build has
succeeded, future space or timeout failures may require a larger runner or a
separate native ARM64 build host. Private repositories may incur Actions usage
charges; see [GitHub's runner specifications and pricing information](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

On the machine running Imager, verify and prepare the extracted artifact:

```bash
cd /path/to/extracted-artifact
sha256sum -c SHA256SUMS
python3 imager_repo.py /absolute/path/to/pideck.img.xz
rpi-imager --repo "$PWD/pideck-imager-repository.json"
```

Replace the example image path with the downloaded image's actual filename.
The included repository JSON references the GitHub runner's filesystem and
must be regenerated as shown before flashing. Then follow the customization
steps under **Flash and first boot**.

### Local native ARM64 build

Use a native ARM64 Linux build host with Python 3.12 or newer, Git, network
access, enough free space for multiple root filesystem copies and the
[pi-gen host dependencies](https://github.com/RPi-Distro/pi-gen/blob/c851f00178c64b87a86c06c2be216bd89d62e55b/README.md#dependencies).
The build requires root privileges for pi-gen's chroots, mounts and loop
devices. It runs host-side build scripts as root; use a trusted checkout on a
dedicated build machine. The target directory must not already exist and
neither it nor its parent path may contain whitespace:

```bash
sudo python3 image/build.py --work-dir /var/tmp/pideck-build
```

The repository itself may be in a path containing spaces: the script copies
the required inputs to the separate build directory. It clones the pinned
pi-gen ARM64 revision, starts from the Lite stages, excludes a small set of
development/diagnostic packages from that base, adds the appliance stage and
exports a compressed `.img.xz`. The image and `SHA256SUMS` are under
`/var/tmp/pideck-build/pi-gen/deploy/`; the build's source manifest is in
`/var/tmp/pideck-build/source-manifest.json`. The image includes
`/usr/share/pideck/package-manifest.tsv` and pi-gen also emits an `.info`
file listing packages. The deploy directory also contains
`pideck-imager-repository.json`, a local Raspberry Pi Imager OS-list entry.

The pi-gen revision and local source hashes are recorded, but apt repositories
are live and package versions can change. Builds are not guaranteed to be
bit-for-bit identical or to resolve the same package versions. The Lite base
still contains Raspberry Pi OS utilities beyond the explicitly added
application dependencies. Failures leave the dedicated working directory in
place for investigation; choose another new directory for the next build.

## Application release artifacts

The `Publish PiDeck update` workflow creates application update
artifacts for stable tags of the form `vMAJOR.MINOR.PATCH`. The tag must match
both version declarations in `pyproject.toml` and `src/pideck/__init__.py`.
Releases contain a bundle and metadata, not a signature or signing key.
Downloads use HTTPS and check the bundle's size and SHA-256 against the
downloaded metadata. These checks detect corruption but do not authenticate
the publisher: anyone able to replace both files can distribute altered code.

Every image supports application updates. A branch build installs the
project version declared in `pyproject.toml` and `src/pideck/__init__.py` and
updates to any newer published release. To build an exact release image,
select the matching release tag when starting the image workflow. For a native
release build, use a clean checkout of the matching tag:

```bash
sudo python3 image/build.py --work-dir /var/tmp/pideck-release-build \
  --release-tag v0.3.0
```

Replace the example tag with the actual release tag. The builder requires the
tag, both project version declarations and the checked-out commit to agree.
Release inputs must have no local modifications. Older signed-update images
cannot accept this unsigned protocol, and untagged images built before
v0.3.10 cannot self-update. Reflash with a current image or perform a separate
system migration.

New image and application builds record the PiDeck application build
time in UTC. Settings shows the timestamp for the running application, not the
OS image; older builds without this metadata show it as unknown. Update
artifacts are reproducible for identical source and build timestamp inputs;
different build times produce different artifacts. The bundle builder accepts
`--build-timestamp` with the original timezone-aware ISO 8601 timestamp when
reproducing an artifact.

New images include the system-settings dependencies for X11 display control,
PulseAudio-compatible audio, NetworkManager and BlueZ pairing. See
[Settings](../README.md#settings) for supported operations and limitations.
Validate display rollback, audio routing and Bluetooth pairing on the target
hardware; these image changes have not been hardware-certified.

On a release image, PiDeck checks for stable releases at startup and every six
hours while running. The check interval can be set with
`settings.update_check_interval_hours` in the user YAML (a whole number from
1 to 596). In **Settings > About**, select the PiDeck row to download and
verify an available update. The update is activated at the next boot; user
configuration and data are not replaced. If the new launcher does not reach
readiness, the previous release is restored on the following boot and the
failure is shown in Settings. Failures after readiness are not automatically
rolled back. For recovery, use a second console or an explicitly enabled SSH
connection and inspect `journalctl -u pideck-updater -u pideck-activate`.

The image includes a root-owned update service. It accepts requests only from
the Imager-provisioned UID 1000 account and validates release metadata, sizes,
hashes and archive contents, but does not verify a signature. PiDeck and supported third-party
application updates need no password. For apt, the service permits upgrading
any installed package, including required dependencies, without allowing
arbitrary sudo commands. Package maintainer scripts run as root, so anyone with
access to the appliance account can initiate privileged package upgrades. User
Flatpak updates run as that user; system Flatpak updates use the service.
Package-manager failures are reported in Settings and the journal.

The installed privileged updater and its protocol are not themselves updated by
application releases. These application updates do not update the operating
system or image bootstrap components.

## Flash and first boot

1. Verify the checksum. On the machine running Raspberry Pi Imager, start it
   with the generated local repository:

   ```bash
   rpi-imager --repo /var/tmp/pideck-build/pi-gen/deploy/pideck-imager-repository.json
   ```

   Select PiDeck and the target storage device. The Imager version must
   support `--repo` with a local repository. Current Imager versions do not
   offer OS customization when selecting a file through **Use custom**, so do
   not use that option for this image.
2. In Imager's customization settings, configure a username and password.
   Configure Wi-Fi, country, timezone and keyboard as needed; enable SSH only
   if you need it. Autologin is enabled only for the provisioned, unlocked
   UID 1000 account. No default password or passwordless sudo is configured.
3. Flash the image and connect the Pi 5 to a display and input devices. The
   image retains the Raspberry Pi OS first-boot filesystem expansion.
4. PiDeck creates its user configuration once at
   `~/.config/pideck/pideck.yaml` and preserves subsequent edits. Its assets
   are installed under `/usr/share/pideck/`.

The repository entry contains an absolute `file://` image path. If the image
is moved or copied to another machine, regenerate the entry there before
opening Imager:

```bash
python3 image/imager_repo.py /path/to/pideck.img.xz
```

This scans the compressed image to calculate the uncompressed size and hash;
the generated repository is written next to the image. Copy the generator
script along with the image if the project checkout is not available on the
flashing machine.

If Imager does not provide a user, the image does not automatically log in and
the console reports that customization is required. If the graphical session
fails, it does not restart continuously. Use a second console (Ctrl+Alt+F2) or
an explicitly enabled SSH connection for recovery and inspect `journalctl`.
After confirmation, the shutdown button requests system power-off; an
authorization or systemd failure is displayed in PiDeck.

## Drives and keyboard layout

Images built from v0.4.0 or later include `udisks2`, a polkit rule
(`/etc/polkit-1/rules.d/50-pideck-udisks.rules`) that lets the session user's
`plugdev` group mount, unmount and power off data drives, and an Xorg snippet
(`/etc/X11/xorg.conf.d/00-pideck-keyboard.conf`) that makes German QWERTZ the
default for every keyboard. The pi-gen build also sets the console keymap
to German; a keyboard chosen in Raspberry Pi Imager still applies to the
console. The v0.4.0 application update adds the Drives
window and the keyboard layout setting to older images too, but mounting
drives there reports that a newer image is required, because application
updates do not install packages or polkit rules. Reflash to enable it.

## Applications and language

Images from v0.5.0 preinstall only Kodi (Raspberry Pi OS package) and Red as
YouTube (system Flatpak from Flathub). Everything else is installed and removed
from **Settings > Applications**; see [Settings](../README.md#settings). The
image contains what those applications need: Flatpak with the system Flathub
remote, the Google Chrome and Brave apt repositories with signing keys in
`/usr/share/pideck/keyrings`, Steam Link's udev rule
(`/usr/lib/udev/rules.d/56-steamlink.rules`) and `uinput` module so its launcher
never asks for `sudo`, and a masked `nxserver.service`.

The pi-gen build sets the locale to `de_DE.UTF-8` and the time zone to
Europe/Berlin; a time zone chosen in Raspberry Pi Imager still applies. Flatpak
installs German translations, Firefox ESR uses its German language pack and the
`RequestedLocales` policy in `/etc/firefox/policies/policies.json`, and Kodi's
German language add-on is bundled in `/usr/share/kodi/addons` after the build
verifies its pinned SHA-256 from `kodi-language.env`.

The following are package-availability findings, not proof that the apps work
with PiDeck on a Raspberry Pi 5:

| Application | Source on the Pi | Remaining work |
| --- | --- | --- |
| Kodi | Raspberry Pi OS `kodi` (preinstalled). | Verify graphics, audio and process/window lifecycle. |
| YouTube (Red) | Flathub `com.ktechpit.red` for aarch64 (preinstalled). | Verify playback performance and remote control. |
| Steam Link | Raspberry Pi OS `steamlink`, which downloads Valve's program at first start; the catalog installs its runtime libraries. | Verify Pi 5 streaming and the process/window lifecycle. Requires a Steam host. |
| Chromium | Raspberry Pi OS `chromium`, `chromium-l10n` and `rpi-chromium-mods`. | Verify fullscreen launch/return behavior. |
| Chrome | Google's arm64 `google-chrome-stable`. | Verify launch behavior and video playback. |
| Brave | Brave's arm64 `brave-browser`. | Verify launch behavior and video playback. |
| Firefox | Debian `firefox-esr` and `firefox-esr-l10n-de`. | Verify launch behavior. |
| RetroArch | Debian `retroarch` and `libretro-mgba`. | Verify graphics, controllers and core loading. Users must supply any legally obtained games and system files. |
| NoMachine | NoMachine Player arm64 `.deb`, downloaded from NoMachine on the device and checked against the catalog's SHA-256. The image does not redistribute it. | Verify the client and its window lifecycle. |

## Hardware acceptance checklist

After a native build, inspect its partitions, boot files, package inventory,
Imager hooks and default accounts before flashing. Record the image size and
minimum storage capacity. On a Pi 5, verify:

- Customized first boot, filesystem expansion, subsequent boot and absence of
  auto-login without an Imager-provisioned account.
- HDMI video and audio; Ethernet or configured Wi-Fi; keyboard and controller
  navigation and hot-plug behavior.
- Kodi and YouTube, then installing, launching and removing every other
  catalog application including RetroArch with mGBA core loading, with
  external-window detection, closing the app, returning to PiDeck and a German
  user interface.
- Built-in CEC discovery and local-seat access; remote navigation and repeats
  in PiDeck and settings; Kodi remote operation; release/reacquisition on
  launch, failure, replacement and exit; unplug/reconnect and no unwanted
  TV power or input changes. Check keyboard and controller operation too.
- On HDMI2, verify automatic and manual address selection, unsupported
  overrides, both input-switching modes, Kodi startup and return, manual
  connection, launcher restart, and hotplug.
- Settings persistence, passwordless application and unsigned PiDeck updates,
  reboot activation and rollback, shutdown, error handling and console recovery.

No Pi 5 hardware acceptance has yet been performed. PiDeck's application
readiness detector currently uses X11 and `wmctrl`; compatibility with each
application's real window and process lifecycle remains a release gate.

The configured Home key binding is not currently implemented as a global
shortcut while another application has focus.
