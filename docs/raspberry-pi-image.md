# Raspberry Pi 5 image

The local image builder targets Raspberry Pi OS Lite 64-bit. It adds a minimal
X11 session that starts PiDeck after Raspberry Pi Imager provisions the user.
The image has been built successfully on a GitHub-hosted ARM64 runner, but
has not yet been tested on a Raspberry Pi 5. Treat it as experimental until
the hardware acceptance checklist below passes.

## Build

### GitHub Actions

The manually triggered **Build Raspberry Pi image** workflow uses a GitHub-hosted
ARM64 Ubuntu runner. Once the image-builder changes and workflow have been
committed and pushed, enable Actions if necessary. The workflow file must be
present on the default branch for manual runs to be available. In the
repository's **Actions** tab, select the workflow, choose **Run workflow**,
select the branch to build, and start it. Only committed files on the selected
branch are built; local uncommitted changes are not included.

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

## Signed application release artifacts

The `Publish signed PiDeck update` workflow creates application update
artifacts for stable tags of the form `vMAJOR.MINOR.PATCH`. The tag must match
both version declarations in `pyproject.toml` and `src/pideck/__init__.py`.
Provision an Ed25519 PEM private key as the GitHub Actions secret
`PIDECK_RELEASE_SIGNING_KEY` and its matching PEM public key as the repository
variable `PIDECK_RELEASE_PUBLIC_KEY`. Protect the private key outside the
repository and never commit it. The workflow verifies the signature against the
configured public key before publishing.

To build an update-enabled image, select the matching release tag when starting
the image workflow. A branch build is a development image and cannot
self-update. For a native build, use a clean checkout of the matching tag and
pass the same public key used by the release workflow:

```bash
sudo python3 image/build.py --work-dir /var/tmp/pideck-release-build \
  --release-tag v0.1.0 --public-key /path/to/release-public.pem
```

Replace the example tag with the actual release tag. The builder requires the
tag, both project version declarations and the checked-out commit to agree.
Release inputs must have no local modifications. Existing images do not contain
the updater; reflash them with an update-enabled release image.

On a release image, PiDeck checks for stable releases at startup and every six
hours while running. The check interval can be set with
`settings.update_check_interval_hours` in the user YAML (a whole number from
1 to 596). In **Settings > Updates**, select the PiDeck row to download and
verify an available update. The update is activated at the next boot; user
configuration and data are not replaced. If the new launcher does not reach
readiness, the previous release is restored on the following boot and the
failure is shown in Settings. Failures after readiness are not automatically
rolled back. For recovery, use a second console or an explicitly enabled SSH
connection and inspect `journalctl -u pideck-updater -u pideck-activate`.

The image includes a root-owned update service. It accepts requests only from
the Imager-provisioned UID 1000 account and verifies PiDeck release signatures
against the public key embedded in the image. PiDeck and supported third-party
application updates need no password. For apt, the service permits upgrading
any installed package, including required dependencies, without allowing
arbitrary sudo commands. Package maintainer scripts run as root, so anyone with
access to the appliance account can initiate privileged package upgrades. User
Flatpak updates run as that user; system Flatpak updates use the service.
Package-manager failures are reported in Settings and the journal.

Keep the signing private key offline or in the protected GitHub Actions secret.
The installed public key and updater protocol are not themselves self-updated.
If the key is lost or compromised, or an incompatible updater protocol is
needed, provision a new trusted key and reflash a new release image; changing
the GitHub variable alone does not rotate existing devices. These application
updates do not update the operating system or image bootstrap components.

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
The shutdown button requests system power-off; an authorization or systemd
failure is displayed in PiDeck.

## Application inventory

The following are package-availability findings, not proof that the apps work
with PiDeck on a Raspberry Pi 5:

| Application | Findings | Remaining work |
| --- | --- | --- |
| Kodi | Debian publishes an [ARM64 package](https://packages.debian.org/trixie/arm64/kodi). | Verify the Raspberry Pi OS package, graphics, audio and process/window lifecycle. The checked-in configuration currently specifies an x86_64 Flatpak and is not suitable for this image. |
| Chromium | Debian publishes an [ARM64 package](https://packages.debian.org/trixie/arm64/chromium). | Verify the Raspberry Pi OS package and fullscreen launch/return behavior. |
| Firefox | Debian publishes an [ARM64 Firefox ESR package](https://packages.debian.org/trixie/arm64/firefox-esr). | Verify launch behavior and map `firefox-esr` to its package for updates. |
| RetroArch | Debian publishes an [ARM64 package](https://packages.debian.org/trixie/arm64/retroarch) and [mGBA core](https://packages.debian.org/trixie/arm64/libretro-mgba). | Verify graphics, controllers and core loading. Users must supply any legally obtained games and system files. |
| Steam Link | The [Raspberry Pi OS ARM64 package index](https://archive.raspberrypi.com/debian/dists/trixie/main/binary-arm64/Packages.gz) lists `steamlink` version 1.0.16 as architecture `all`; its package documentation says it downloads the actual program at runtime. | Omitted until Pi 5 compatibility, runtime dependencies, redistribution terms and the process/window lifecycle are verified. It also requires a Steam host. |
| NoMachine | The [NoMachine license](https://www.nomachine.com/licensing/nomachine-end-user-license-agreement) prohibits redistribution of its software without written permission. | Omitted without that permission. Verify an appropriate user-installed ARM64 client separately. |

The image-specific launcher configuration omits the excluded applications and
replaces the development configuration's x86_64 Kodi command.

## Hardware acceptance checklist

After a native build, inspect its partitions, boot files, package inventory,
Imager hooks and default accounts before flashing. Record the image size and
minimum storage capacity. On a Pi 5, verify:

- Customized first boot, filesystem expansion, subsequent boot and absence of
  auto-login without an Imager-provisioned account.
- HDMI video and audio; Ethernet or configured Wi-Fi; keyboard and controller
  navigation and hot-plug behavior.
- Kodi, Chromium, Firefox ESR and RetroArch including mGBA core loading, with
  external-window detection, closing the app and returning to PiDeck.
- Settings persistence, passwordless application and signed PiDeck updates,
  reboot activation and rollback, shutdown, error handling and console recovery.

No Pi 5 hardware acceptance has yet been performed. PiDeck's application
readiness detector currently uses X11 and `wmctrl`; compatibility with each
application's real window and process lifecycle remains a release gate.

HDMI-CEC input and full system network, Bluetooth, audio and display settings
are not currently implemented by PiDeck; installing their dependencies alone
would not add those features. The configured Home key binding is not currently
implemented as a global shortcut while another application has focus.
