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

## Private build

Use a trusted native x86-64 Linux build machine with Python 3.12 or newer,
network access, at least 40 GiB of free space, root privileges and these host
tools. On Debian or Ubuntu, install them with:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends debootstrap gdisk util-linux \
  e2fsprogs dosfstools xz-utils openssl systemd python3 gpgv debian-archive-keyring
```

The installed Debian archive keyring must contain the keys for Debian 13.
The builder requires signature verification and fails if the keyring is
missing or cannot validate the repository metadata.

The build runs package installation scripts as root, including any supplied
third-party package. A chroot is not a security boundary: use a dedicated,
trusted build host and only trusted source checkouts and packages. The working
directory must not exist; failures leave it for inspection.

```bash
sudo python3 image/x86/build.py --work-dir /var/tmp/pideck-x86-build
```

By default this includes Kodi, Chromium, Firefox ESR, RetroArch and mGBA,
plus Steam Link from Flathub. Steam Link adds a Flatpak runtime and requires
a separate Steam host. The x86-specific application definitions are configured in
[`applications.yaml`](../image/x86/applications.yaml). For NoMachine, download the **amd64 NoMachine Player**
`.deb` from the official [NoMachine Player page](https://download.nomachine.com/player/)
and independently verify that it is the package you trust. Supply the package
and its expected SHA-256:

```bash
sudo python3 image/x86/build.py --work-dir /var/tmp/pideck-x86-build \
  --nomachine-deb /path/to/nomachine-player_amd64.deb \
  --nomachine-sha256 EXPECTED_64_CHARACTER_SHA256
```

The builder checks the hash, package name and architecture before creating
the image. A checksum calculated from the same unverified download only
checks consistency, not authenticity. The optional package is not committed
to this repository. Without it, the builder reports the omission and does not
add a broken NoMachine tile. NoMachine Player's server service is masked.
Only build and store this image privately under your applicable licenses.

Release images can also be built with `--release-tag vMAJOR.MINOR.PATCH
--public-key /path/to/update-public.pem`. The tag, checked-out commit and
project versions must agree, the image inputs must be clean, and the key must
be the Ed25519 public key corresponding to the signed PiDeck release. Without
both options the image is a development image and cannot self-update. See
[signed update details](raspberry-pi-image.md#signed-application-release-artifacts);
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
cd /var/tmp/pideck-x86-build
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

The image boots its default GRUB entry without displaying a menu or waiting,
including after an interrupted boot. Use external rescue media if recovery
requires selecting a different boot entry.

## Physical disk

**Flashing destroys all data on the selected disk.** Identify the destination
carefully with `lsblk`; use the whole target disk, not a partition, and never
the disk containing your build or running system. On a machine with enough
privileges, verify `SHA256SUMS`, then replace `/dev/DESTINATION` below with the
verified target disk:

```bash
set -o pipefail
xz -dc pideck-x86_64.img.xz | sudo dd of=/dev/DESTINATION bs=4M conv=fsync status=progress
```

Boot the target from that disk with Secure Boot disabled. On a larger disk,
`pideck-grow-root.service` checks that the mounted root is the labeled GPT
partition 3 before attempting expansion. Failures are recorded in
`journalctl -u pideck-grow-root`; no successful resize is assumed when the
service fails. Do not connect multiple clones of the same image simultaneously:
they retain matching filesystem identifiers.

## First use, network and security

The appliance account is UID 1000 and logs in automatically on the first
console. It has no configured login password or passwordless sudo. SSH is not
installed or enabled, and NoMachine's server is masked if the optional player
is present. Anyone with physical access can operate the appliance and initiate
the restricted package updater; this is not an authenticated multi-user setup.
Use trusted physical and network environments. Recovery requires external
rescue media; there is no password-based administrative login.

Ethernet uses NetworkManager. On the first offline start, PiDeck offers Wi-Fi
setup; it can be skipped and reopened under **Settings > Network**. Select a
visible open or WPA personal network and enter its key using a keyboard,
mouse or controller-operated on-screen keyboard. Keys are masked and sent to
NetworkManager via standard input, not command-line arguments. NetworkManager
persists connection credentials. Hidden networks, enterprise authentication and
Bluetooth pairing are not provided by this interface. A Wi-Fi network key,
Steam pairing, NoMachine host credentials and website logins are not system
passwords and may still be required.

Common non-free firmware is included for hardware compatibility, but no
particular graphics, Wi-Fi, audio or controller chipset is guaranteed.
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
- Every included application, its external window, return to PiDeck, audio,
  video, updates, and persistence across reboot.
- The same boot, display, audio, network and input checks on the target
  physical hardware, including its specific firmware.
- On a release image, signed update staging, reboot activation and rollback.

These are release gates, not claims that the current experimental builder
has already passed them.
