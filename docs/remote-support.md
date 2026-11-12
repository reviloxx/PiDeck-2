# Remote support SSH (on-device TV debug)

Use this when a real TV/appliance cannot be diagnosed from the developer PC
alone (maximize, focus, CEC-in-apps, Flatpak/SSL, Openbox, session helpers).
Console login is often impossible: the x86 image has **no password** for UID
1000, and a Pi image uses whatever Raspberry Pi Imager set.

## How it works

1. Maintainer workstation has an ed25519 key pair at `~/.ssh/pideck-tv-support`
   (private key never ships in the repo or OTA).
2. The matching **public** key lives in [`assets/support/ssh-authorized-keys`](../assets/support/ssh-authorized-keys).
3. On the TV, **Settings → System → Remote support → Enable** installs
   `openssh-server` via the package catalog/broker, writes
   `~/.ssh/authorized_keys` for the appliance user, and starts `ssh`
   (pubkey only; no PasswordAuthentication).
4. Agent or maintainer SSHs from the LAN with that private key.

Disable removes the support keys and stops `ssh`.

## Agent workflow

1. Confirm the TV is on a released build that includes Remote support
   (v0.5.55+). If not, ship/update first.
2. Ask the operator to: update if needed → **Enable remote support** → send the
   **LAN IP** (Settings → Network or router). Username is normally `pideck`
   (UID 1000 on the x86 image).
3. Connect:

   ```bash
   ssh -i ~/.ssh/pideck-tv-support -o BatchMode=yes pideck@<TV_LAN_IP>
   ```

4. Prefer evidence before more maximize OTAs. Typical dump (DISPLAY is often
   unset over SSH):

   ```bash
   export DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000
   date; timedatectl status || true
   python3 -c 'import pideck; print(pideck.__version__)'
   # Flatpak / SSL (curl may be absent; urllib is fine)
   python3 -c 'import urllib.request; print(urllib.request.urlopen("https://flathub.org/", timeout=15).status)'
   flatpak list
   wmctrl -m; wmctrl -d; wmctrl -lGpx
   ps aux | grep -E 'openbox|picom|pideck|brave|Xorg' | grep -v grep
   # Optional UI screenshot for agent review (ImageMagick)
   mkdir -p /tmp/pideck-ui-review
   import -window root "/tmp/pideck-ui-review/tv-$(date +%Y%m%d-%H%M%S).png"
   # Zombies under bootstrap mean the WM died after release-config restart
   ```

   Copy screenshots off the TV with `scp` when agents need visual evidence.

5. Fix from that evidence only; ship an OTA when code must change. Blind
   maximize releases without SSH evidence have already failed on hardware.

## Root SSH (initramfs / systemd / image files)

Normal Remote support installs the pubkey only for the appliance user
(`pideck`). That is enough for launcher/X debugging. Changing initramfs hooks,
GRUB, or early systemd units needs **root**.

On this TV the support pubkey was also planted in
`/root/.ssh/authorized_keys`, so agents can:

```bash
ssh -i ~/.ssh/pideck-tv-support -o BatchMode=yes root@<TV_LAN_IP>
```

Prefer root SSH for boot-image work; do not enable PasswordAuthentication.

Hibernate host prep (swapfile, GRUB/initramfs resume, polkit, xsession flag)
is documented under [x86 image → Hibernate / Turn off](x86-image.md#hibernate--turn-off).
OTA does not apply those pieces.

### Historical bootstrap (do not reinject)

Before path validation, Samba share enable accepted a path containing a
newline. Writing that into `shares.conf` as `path = …\n  root preexec = …`
made a guest open of the share run arbitrary commands as root (used once to
install the support key under `/root/.ssh`). That hole is **fixed** in
`src/pideck/infrastructure/privileged_shares.py` (rejects `\n` / `\r` / `\t`
in paths). Use root SSH already on the device, or a new image with the key
baked/managed intentionally. Do not reinject via Samba.

## What broke before (so you check it)

- **Openbox/picom zombies** under `python -m pideck.bootstrap`: xsession starts
  them, bootstrap `exec`s in, SIGTERM leaves unreaped zombies, restart aborts →
  **no window manager** → apps stay small and focus/CEC-in-apps fail. See
  `src/pideck/infrastructure/session/openbox.py` and `picom.py`.
- **Flatpak SSL error 60**: usually a wrong clock; check `timedatectl` / time
  before chasing CA certs.
- OTA updates replace `/usr/lib/pideck/current` (Python + assets), not the
  root image updater daemon. Privileged package installs go through the
  catalog/broker path Remote support already uses.

## Keys and assets

See [`assets/support/README.md`](../assets/support/README.md). Do not commit the
private key. Do not enable PasswordAuthentication.
