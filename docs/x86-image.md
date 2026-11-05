# Experimental x86-64 image

PiDeck has an experimental private-use x86-64 image builder in
[`image/x86`](../image/x86). It uses Debian 13's minimal root filesystem,
an X11/Openbox session and explicit packages installed without recommended
packages. A development build has booted in QEMU and nested VirtualBox using
BIOS and UEFI; **physical hardware remains unverified**. WPA2 connection and
reconnection have also been exercised with simulated Wi-Fi radios. Do not use
it as your only system until the acceptance checks below pass. Debian packages and the
Steam Link Flatpak runtime change over time, so the builder does not claim
reproducible or globally minimal size.

The raw image is 12 GiB and requires a disk at least that large. It contains a
GPT BIOS boot partition, an EFI system partition and an ext4 root partition.
It is intended for BIOS and 64-bit UEFI systems with Secure Boot disabled.
Larger destinations should expand the root partition and filesystem at boot.
The installed size and package inventories are recorded by the build.
One private test build including NoMachine used approximately 5.3 GiB in the
root filesystem and compressed to approximately 2.4 GiB; later builds may vary.

## HDMI-CEC

Connect the Pulse-Eight adapter between the computer's HDMI output and the TV
and connect its USB cable. Enable HDMI-CEC on the TV. New images install
`cec-utils`, `inputattach`, a hotplug service and local-seat device permissions.
The service attaches the USB adapter to the Linux CEC driver, whose default
`persistent_config=0` avoids writing PiDeck's settings to adapter EEPROM.
PiDeck refuses to connect if that driver option is enabled.

In **Settings → Input**, choose the TV's HDMI input for a direct connection or
automatic EDID detection. Auto-connect is on and TV input switching is off by
default; the latter can be enabled explicitly. Changes apply immediately. With
auto-connect off, use Connect and Disconnect: a manual connection resumes after
an application exits, but not after restarting PiDeck. A disconnect stops
automatic reconnection until Connect or a launcher restart.

PiDeck releases its client before starting applications, except for
applications with `cec: keyboard` such as YouTube: PiDeck then forwards the
remote's keys to the application, and the remote's Home key returns to PiDeck.
Kodi configures its
own CEC behavior under **Settings → System → Input → Peripherals**: select the
Linux CEC adapter if available, set the correct physical address or HDMI input,
and disable Kodi's source activation if it should not switch the TV. Disable a
duplicate USB CEC entry if Kodi tries to open the serial adapter already owned
by the kernel driver. PiDeck does not rewrite Kodi's configuration. The kernel
physical address is shared by applications, so check the address in both.

The Debian libCEC Linux backend opens only `/dev/cec0`; if that device is a GPU
or another CEC adapter instead of Pulse-Eight, PiDeck reports an error rather
than selecting it. Automatic detection requires a single unambiguous connected
display EDID. Inspect the Input status and PiDeck journal, check HDMI cabling,
TV CEC and `/dev/cec0` permissions, and use `cec-client -l` only when PiDeck
and Kodi have released CEC. This integration and Kodi handoff still require
physical-hardware validation.

### Existing release installations

The application update includes the new settings but cannot install packages
or system rules. After updating a release image, install the kernel attachment
support once, then reboot:

```bash
sudo apt-get update
sudo apt-get install cec-utils inputattach
CEC_SUPPORT=/usr/lib/pideck/current/python/pideck/infrastructure/cec_support
sudo install -m 644 "$CEC_SUPPORT/71-pideck-cec-attach.rules" /etc/udev/rules.d/
sudo install -m 644 "$CEC_SUPPORT/pideck-cec-attach@.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
sudo reboot
```

Untagged images built before v0.3.10 cannot self-update; they need a new
build or a separately installed application update before these files are
available.
`input.yaml` is initialized once with CEC enabled even if the old main YAML
disabled it. Subsequent choices are preserved; use Settings → Input to disable
CEC. Older releases do not read this file when rolling back. On reboot into an
older release the attachment service does not start, so that release can use
the Pulse-Eight USB adapter directly; its own CEC configuration still applies.

## Private build

### Without a password (Docker)

If your account can use Docker (for example, as a member of the `docker`
group), build without `sudo` from the repository root:

```bash
python3 image/x86/container_build.py
```

This builds a Debian trixie container from
[`Dockerfile`](../image/x86/Dockerfile) with the required host tools, then runs
the builder described below as root in a privileged container with the host's
`/dev` and network. Docker group membership already gives root-equivalent
access to the host. The wrapper therefore adds no new privilege, but it is not a
security boundary. The image is built in `image-output/x86.new`. Only after a
successful build does that directory replace `image-output/x86`, deleting the
previous image. A failed build leaves `x86.new` for inspection; remove it
before trying again. Use `--output DIR` for another output directory.
`--release-tag`, `--nomachine-deb` and `--nomachine-sha256` work as described
below, and the player package is mounted read-only into the container. The
build needs about 40 GiB of free space while both the old and new images exist.

### With sudo

Use a trusted native x86-64 Linux build machine with Python 3.12 or newer,
network access, at least 40 GiB of free space, root privileges and these host
tools. On Debian or Ubuntu, install them with:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends debootstrap gdisk util-linux \
  e2fsprogs dosfstools xz-utils openssl systemd udev python3 gpgv debian-archive-keyring
```

The installed Debian archive keyring must contain the keys for Debian 13.
The builder requires signature verification and fails if the keyring is
missing or cannot validate the repository metadata.

The build runs package installation scripts as root, including any supplied
third-party package. A chroot is not a security boundary: use a dedicated,
trusted build host and only trusted source checkouts and packages. The working
directory must not exist; failures leave it for inspection. Run the following
commands from the repository root. Image output is kept in the ignored
`image-output/x86` directory. If it already exists, preserve any image you
need and remove or move the existing build directory before starting another
build; the builder will not overwrite it. Paths containing spaces are supported:
the builder temporarily bind-mounts the image filesystem under `/run` for
Debian bootstrap without storing the image there.

```bash
mkdir -p image-output
sudo python3 image/x86/build.py --work-dir "$PWD/image-output/x86"
```

By default this includes Kodi, Chromium, Firefox ESR, RetroArch and mGBA,
plus Steam Link from Flathub. Steam Link adds a Flatpak runtime and requires
a separate Steam host. The x86-specific application definitions are configured in
[`applications.yaml`](../image/x86/applications.yaml). For NoMachine, download the **amd64 NoMachine Player**
`.deb` from the official [NoMachine Player page](https://download.nomachine.com/player/)
and independently verify that it is the package you trust. Supply the package
and its expected SHA-256:

```bash
sudo python3 image/x86/build.py --work-dir "$PWD/image-output/x86" \
  --nomachine-deb /path/to/nomachine-player_amd64.deb \
  --nomachine-sha256 EXPECTED_64_CHARACTER_SHA256
```

The builder checks the hash, package name and architecture before creating
the image. A checksum calculated from the same unverified download only
checks consistency, not authenticity. The optional package is not committed
to this repository. Without it, the builder reports the omission and does not
add a broken NoMachine tile. NoMachine Player's server service is masked.
Only build and store this image privately under your applicable licenses.
The builder needs root for disk, mount and installation operations. When
invoked with `sudo` or `pkexec`, it gives the completed output directory and
its top-level files to the invoking user, retaining private directory
permissions. It also does this after a failed build if its mounts were
successfully released. A direct root invocation without an invoking-user
identity leaves the output owned by root.

Every image supports PiDeck application updates. Without a tag, the image
installs the version declared in `pyproject.toml` and `src/pideck/__init__.py`
(both must agree) and later receives any newer published release; local
changes are included as built. Release images can be built with
`--release-tag vMAJOR.MINOR.PATCH`; the tag, checked-out commit and project
versions must then agree, and the image inputs must be clean. See
[unsigned update details](raspberry-pi-image.md#application-release-artifacts);
the x86 image does not use Raspberry Pi Imager provisioning.

The build directory contains the raw `.img`, compressed `.img.xz`,
`SHA256SUMS`, `source-manifest.json`, `package-manifest.tsv` and
`flatpak-manifest.tsv`. The source manifest records the root filesystem
usage, raw image size, source hashes and optional player checksum. Package
versions and Flatpak references are live; retain the manifests with the image.
Only the compressed image is covered by `SHA256SUMS`.

## VirtualBox

Verify the compressed image, then create a separate virtual disk:

```bash
cd image-output/x86
sha256sum -c SHA256SUMS
xz -dk pideck-x86_64.img.xz
VBoxManage convertfromraw pideck-x86_64.img pideck-x86_64.vdi --format VDI
```

If the raw image from the build is already present, omit `xz -dk`. Attach the
VDI to a new 64-bit Linux VM with a SATA controller, at least 4 GiB RAM,
adequate video memory and a NAT or bridged network adapter. Test separately
with VirtualBox EFI disabled and enabled. Do not attach the same raw image as
a writable disk to two machines simultaneously. Configure the SATA controller
with only as many ports as attached devices; unused virtual ports can
significantly delay boot while the firmware and kernel probe them.
For a faster VirtualBox BIOS boot, the firmware splash fades and delay can be
reduced while the VM is powered off:

```bash
VBoxManage modifyvm PiDeck --firmware-logo-fade-in off \
  --firmware-logo-fade-out off --firmware-logo-display-time 1
```

The shorter firmware display leaves less time to use its boot-selection menu.

The image boots its default GRUB entry without displaying a menu or waiting,
including after an interrupted boot. Use external rescue media if recovery
requires selecting a different boot entry. GRUB uses a text console to avoid an
extra display mode change, which can be slow on TVs.

To shorten boots from USB media, new builds keep GPU drivers and GPU firmware
(NVIDIA, AMD and Intel) out of the initramfs; the Plymouth hook would
otherwise add over 100 MiB. The drivers load from the root filesystem, and
the first console waits for udev (up to 20 seconds) before starting X, so the
session does not start before the graphics driver is ready. The initramfs
is compressed with zstd, which decompresses faster than the gzip fallback
used when `zstd` is missing, and it skips the resume lookup because the image
has no swap. Steam Link's Flatpak runtime is installed with only the default
GL drivers. Otherwise the build host's proprietary NVIDIA driver could add an
unused GL extension to the image.

New builds use a PiDeck Plymouth splash while Linux starts. Firmware and
VirtualBox screens before Linux starts are outside its control. Because GPU
drivers load after the initramfs, the splash may appear later; when booting
in BIOS mode it may not appear until the graphics driver loads. Press Escape
to switch to boot details, and use the system journal when diagnosing failures;
the splash does not remove journal entries. Graphics-driver availability may
affect the splash on physical hardware. The splash uses the default theme's
colors because it starts before user theme settings are available. Boot
components are not updated by PiDeck's application updater: an existing
installation needs a separately applied system update or a newly built image.

The launcher wallpaper fills the window while preserving its aspect ratio;
edges are cropped on screens with a different shape. The image's default
window policies are in `/usr/share/pideck/config/window-modes.yaml`.
Chromium, Firefox, NoMachine and RetroArch are set to `maximized`; fullscreen
windows remain fullscreen. To override a policy without editing system files,
create `~/.config/pideck/window-modes.yaml` with, for example,
`firefox: normal`. Policies apply to detected main windows launched by PiDeck,
not dialog windows. They are stored separately from launcher settings so older
releases can still read those settings after a rollback.

## Physical disk

**Flashing destroys all data on the selected disk.** Identify the destination
carefully with `lsblk`; use the whole target disk, not a partition, and never
the disk containing your build or running system. Unmount all mounted
partitions on the target disk first. Verify `SHA256SUMS` and run the following
from the repository root. The example overwrites `/dev/sda`; change it to the
verified target disk:

```bash
(cd image-output/x86 && sha256sum -c SHA256SUMS)
bash -o pipefail -c 'xz -dc ./pideck-x86_64.img.xz |
  sudo dd of=/dev/sda bs=4M conv=fsync status=progress'
```

Boot the target from that disk with Secure Boot disabled. On a larger disk,
`pideck-grow-root.service` checks that the mounted root is the labeled GPT
partition 3 before attempting expansion. Failures are recorded in
`journalctl -u pideck-grow-root`; no successful resize is assumed when the
service fails. Do not connect multiple clones of the same image simultaneously:
they retain matching filesystem identifiers.

### NVIDIA graphics recovery

Some NVIDIA GPUs require `firmware-nvidia-graphics` for the Nouveau kernel
driver. Without it, the kernel may report missing `nvidia/...` firmware and
X may fail with `no screens found`. New builds include this package; older
images need the package installed and their initramfs regenerated. Images
that keep GPU drivers out of the initramfs only need the package; the
regeneration step below is harmless for them.

To repair an existing image, boot another trusted Linux system, connect the
image disk, and mount its ext4 root partition. Identify it with `lsblk` and
set `ROOT` to its absolute mount point below. The host needs internet access;
the image's `/etc/resolv.conf` must contain a working DNS server reachable
from the host. This procedure modifies the mounted image, not the host.

```bash
ROOT=/path/to/mounted/pideck-root
sudo bash -s -- "$ROOT" <<'EOF'
set -euo pipefail
root=$1
mountpoint -q "$root"
mounts=()
cleanup() {
    for ((i=${#mounts[@]}-1; i>=0; i--)); do
        umount "${mounts[i]}"
    done
}
trap cleanup EXIT
for name in dev proc sys; do
    mount --bind "/$name" "$root/$name"
    mounts+=("$root/$name")
done
chroot "$root" apt-get update
chroot "$root" apt-get install -y --no-install-recommends firmware-nvidia-graphics
chroot "$root" update-initramfs -u -k all
EOF
```

After a successful repair, unmount the root partition and boot the image
again. If graphics still fail, inspect the kernel journal for Nouveau and
firmware errors; support for a particular GPU is not guaranteed.

## First use, network and security

The appliance account is UID 1000 and logs in automatically on the first
console. It has no configured login password or passwordless sudo. SSH is not
installed or enabled, and NoMachine's server is masked if the optional player
is present. Anyone with physical access can operate the appliance and initiate
the restricted package updater; this is not an authenticated multi-user setup.
Use trusted physical and network environments. Recovery requires external
rescue media; there is no password-based administrative login.

Ethernet uses NetworkManager. On the first offline start, PiDeck offers Wi-Fi
setup; it can be skipped and reopened under **Settings > System > Network**. Select a
visible open or WPA personal network and enter its key using a keyboard,
mouse or controller-operated on-screen keyboard. Keys are masked and sent to
NetworkManager via standard input, not command-line arguments. NetworkManager
persists connection credentials. Hidden networks and enterprise authentication
are not provided by this interface. A Wi-Fi network key,
Steam pairing, NoMachine host credentials and website logins are not system
passwords and may still be required.

Common non-free firmware is included for hardware compatibility, but no
particular graphics, Wi-Fi, audio or controller chipset is guaranteed.
Settings includes X11 display modes with timed rollback, PulseAudio-compatible
audio controls and BlueZ Bluetooth pairing; these require compatible hardware
and an authorized active session. See [Settings](../README.md#settings) for
backend limitations and behavior.
Network setup is authorized only for the active local appliance account.
Third-party app updates use the existing apt/Flatpak integration when
available; the locally supplied NoMachine package has no configured update
repository and is reported as unsupported. Signed PiDeck application updates
do not upgrade the underlying OS or the image's boot components.
NoMachine Player installation warns that printer forwarding requires CUPS
configuration; printing is not included in this minimal image.

## Acceptance checks

Before relying on an image, record the build and compressed sizes, inspect the
partition and package manifests, and test:

- VirtualBox BIOS and UEFI first boot, subsequent boot, login, shutdown and
  root expansion on a larger virtual disk.
- Offline Wi-Fi prompt, skip, Settings entry, WPA connection and reconnect;
  Ethernet; keyboard, mouse and controller navigation.
- Display mode confirmation, timeout rollback, and restoration after reboot;
  audio device selection and volume; Bluetooth discovery, pairing confirmation,
  connect/disconnect and cancellation; restart and shutdown authorization.
- Every included application, its external window, return to PiDeck, audio,
  video, updates, and persistence across reboot.
- The same boot, display, audio, network and input checks on the target
  physical hardware, including its specific firmware.
- Pulse-Eight detection and local-seat access; remote navigation and repeats
  in PiDeck and settings; Kodi remote operation; release/reacquisition on
  launch, failure, replacement and exit; unplug/reconnect and no unwanted
  TV power or input changes. Check keyboard and controller operation too.
- On HDMI2, verify automatic and manual port selection before and during
  connection, both TV switching modes, Kodi startup and exit, manual
  connect/disconnect and restart, missing-device errors, and hotplug.
- On a release image, unsigned update staging, reboot activation and rollback.

These are release gates, not claims that the current experimental builder
has already passed them.
