# Agent notes

PiDeck is a TV-first launcher for Raspberry Pi and x86-64 Linux appliances.
Prefer evidence from a real device over stack another maximize OTA.
Do not require VirtualBox for development or UI review.

## UI/UX review (no VM)

Use this ladder; do not boot VirtualBox for routine reviews:

1. **Logic / regression** — `pytest` (offscreen Qt suite under `tests/`).
2. **Visual / UX** — run the launcher on the host desktop (`PIDECK_IMAGE` unset):
   `pideck --config config/pideck.yaml`, then capture PNGs with
   [`scripts/ui-review-capture.sh`](scripts/ui-review-capture.sh) into
   `/tmp/pideck-ui-review/` and inspect those images.
3. **Appliance fidelity** — when Openbox, picom, maximize, Flatpak, or TV
   geometry matter, use on-device SSH below (screenshot with `DISPLAY=:0`).

## On-device TV debug (SSH)

When the TV cannot be diagnosed from this PC (window size, focus, CEC-in-apps,
Flatpak/SSL, Openbox), use **Settings → System → Remote support** and SSH from
the LAN. Full workflow, key path, and evidence commands:

→ **[docs/remote-support.md](docs/remote-support.md)**

Private key (workstation only): `~/.ssh/pideck-tv-support`. Public key is in
`assets/support/ssh-authorized-keys`. Username is usually `pideck`. On this
TV the same key is also in `/root/.ssh/authorized_keys` for initramfs/systemd
work (`ssh -i ~/.ssh/pideck-tv-support root@<TV_IP>`). See remote-support.md
for the historical Samba bootstrap and why not to reinject it.

## Other docs

- [docs/x86-image.md](docs/x86-image.md) — x86 appliance image (no login password)
- [docs/raspberry-pi-image.md](docs/raspberry-pi-image.md) — Pi image builder
