#!/bin/bash -e

install -d "${ROOTFS_DIR}/usr/lib/pideck/python" \
    "${ROOTFS_DIR}/usr/lib/pideck-updater/python" \
    "${ROOTFS_DIR}/usr/share/pideck/config" \
    "${ROOTFS_DIR}/usr/share/pideck/openbox" \
    "${ROOTFS_DIR}/usr/share/pideck/assets" \
    "${ROOTFS_DIR}/etc/profile.d" \
    "${ROOTFS_DIR}/etc/udev/rules.d" \
    "${ROOTFS_DIR}/etc/polkit-1/rules.d" \
    "${ROOTFS_DIR}/etc/X11/xorg.conf.d" \
    "${ROOTFS_DIR}/usr/share/plymouth/themes/pideck" \
    "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d" \
    "${ROOTFS_DIR}/etc/systemd/system/userconfig.service.d" \
    "${ROOTFS_DIR}/usr/share/pideck/keyrings" \
    "${ROOTFS_DIR}/etc/apt/sources.list.d" \
    "${ROOTFS_DIR}/etc/default" \
    "${ROOTFS_DIR}/etc/firefox/policies" \
    "${ROOTFS_DIR}/etc/modules-load.d" \
    "${ROOTFS_DIR}/usr/lib/udev/rules.d" \
    "${ROOTFS_DIR}/usr/share/kodi/addons"

cp -a files/project/src/pideck "${ROOTFS_DIR}/usr/lib/pideck/python/"
cp -a files/project/src/pideck "${ROOTFS_DIR}/usr/lib/pideck-updater/python/"
cp -a files/project/assets/. "${ROOTFS_DIR}/usr/share/pideck/assets/"
install -m 644 files/pideck.yaml "${ROOTFS_DIR}/usr/share/pideck/config/pideck.yaml"
install -m 644 files/70-pideck-cec.rules "${ROOTFS_DIR}/etc/udev/rules.d/70-pideck-cec.rules"
install -m 644 files/50-pideck-udisks.rules "${ROOTFS_DIR}/etc/polkit-1/rules.d/50-pideck-udisks.rules"
install -m 644 files/51-pideck-timezone.rules "${ROOTFS_DIR}/etc/polkit-1/rules.d/51-pideck-timezone.rules"
install -m 644 files/53-pideck-ssh.rules "${ROOTFS_DIR}/etc/polkit-1/rules.d/53-pideck-ssh.rules"
install -d "${ROOTFS_DIR}/etc/samba" "${ROOTFS_DIR}/var/lib/pideck/samba"
install -m 644 files/smb.conf "${ROOTFS_DIR}/etc/samba/smb.conf"
printf '%s\n' '# Managed by PiDeck. Do not edit; Settings > Drives rewrites this file.' \
    > "${ROOTFS_DIR}/var/lib/pideck/samba/shares.conf"
install -m 644 files/00-pideck-keyboard.conf "${ROOTFS_DIR}/etc/X11/xorg.conf.d/00-pideck-keyboard.conf"
install -m 644 files/99-pideck-vc4.conf "${ROOTFS_DIR}/etc/X11/xorg.conf.d/99-pideck-vc4.conf"
install -m 644 files/71-pideck-cec-attach.rules \
    "${ROOTFS_DIR}/etc/udev/rules.d/71-pideck-cec-attach.rules"
install -m 644 files/pideck-cec-attach@.service \
    "${ROOTFS_DIR}/etc/systemd/system/pideck-cec-attach@.service"
install -d "${ROOTFS_DIR}/usr/share/pideck/cec_support"
install -m 644 files/71-pideck-cec-attach.rules \
    "${ROOTFS_DIR}/usr/share/pideck/cec_support/71-pideck-cec-attach.rules"
install -m 644 files/pideck-cec-attach@.service \
    "${ROOTFS_DIR}/usr/share/pideck/cec_support/pideck-cec-attach@.service"
install -m 644 files/window-modes.yaml "${ROOTFS_DIR}/usr/share/pideck/config/window-modes.yaml"
install -m 644 files/openbox/rc.xml "${ROOTFS_DIR}/usr/share/pideck/openbox/rc.xml"
install -m 755 files/pideck-getty files/pideck-session files/pideck-xsession \
    "${ROOTFS_DIR}/usr/lib/pideck/"
# VM-safe fallback for recovery shells and images without a current release tree.
install -m 644 files/project/src/pideck/infrastructure/session/picom-vm.conf \
    "${ROOTFS_DIR}/usr/lib/pideck/picom.conf"
install -m 644 files/pideck-profile.sh "${ROOTFS_DIR}/etc/profile.d/pideck.sh"
install -m 644 files/pideck.plymouth files/pideck.script \
    "${ROOTFS_DIR}/usr/share/plymouth/themes/pideck/"
install -m 644 files/getty-override.conf \
    "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d/pideck.conf"
install -m 644 files/userconfig-override.conf \
    "${ROOTFS_DIR}/etc/systemd/system/userconfig.service.d/pideck.conf"
install -m 644 files/pideck-updater.service files/pideck-activate.service \
    "${ROOTFS_DIR}/etc/systemd/system/"

# Image-level prerequisites of applications installed later from Settings.
install -m 644 files/keyrings/google-chrome.gpg files/keyrings/brave-browser.gpg \
    files/keyrings/moonlight-qt.gpg \
    "${ROOTFS_DIR}/usr/share/pideck/keyrings/"
install -m 644 files/google-chrome.sources files/brave-browser.sources \
    files/moonlight-qt.sources \
    "${ROOTFS_DIR}/etc/apt/sources.list.d/"
install -m 644 files/google-chrome.default "${ROOTFS_DIR}/etc/default/google-chrome"
install -m 644 files/firefox-policies.json "${ROOTFS_DIR}/etc/firefox/policies/policies.json"
install -m 644 files/56-steamlink.rules "${ROOTFS_DIR}/usr/lib/udev/rules.d/56-steamlink.rules"
install -m 644 files/uinput.conf "${ROOTFS_DIR}/etc/modules-load.d/pideck-uinput.conf"
install -m 644 files/bluetooth-input.conf "${ROOTFS_DIR}/etc/bluetooth/input.conf"
install -m 644 files/bluetooth-main.conf "${ROOTFS_DIR}/etc/bluetooth/main.conf"
install -m 644 files/pideck-blacklist-hid-nintendo.conf \
    "${ROOTFS_DIR}/etc/modprobe.d/pideck-blacklist-hid-nintendo.conf"
install -m 644 files/99-pideck-switch-pro-hidraw.rules \
    "${ROOTFS_DIR}/etc/udev/rules.d/99-pideck-switch-pro-hidraw.rules"
install -d "${ROOTFS_DIR}/usr/lib/pideck"
install -m 755 files/pideck-switch-pro-led \
    "${ROOTFS_DIR}/usr/lib/pideck/pideck-switch-pro-led"

# Kodi's German language add-on, pinned and verified, so Kodi starts in German offline.
. files/kodi-language.env
kodi_language="$(mktemp)"
curl -fsSL --proto '=https' --proto-redir '=https' -o "$kodi_language" "$KODI_LANGUAGE_URL"
echo "$KODI_LANGUAGE_SHA256  $kodi_language" | sha256sum -c -
bsdtar -xf "$kodi_language" -C "${ROOTFS_DIR}/usr/share/kodi/addons"
rm -f "$kodi_language"

# Every image installs an updatable release tree; the builder always writes release-version.
version="$(cat files/project/release-version)"
release="${ROOTFS_DIR}/usr/lib/pideck/releases/$version"
install -d "$release/python" "$release/assets" "$release/config"
cp -a files/project/src/pideck "$release/python/"
cp -a files/project/assets/. "$release/assets/"
ln -s "releases/$version" "${ROOTFS_DIR}/usr/lib/pideck/current"

on_chroot <<'EOF'
plymouth-set-default-theme pideck
update-initramfs -u -k all
systemctl enable NetworkManager
systemctl enable bluetooth.service
systemctl enable smbd.service
systemctl enable nmbd.service
systemctl enable pideck-updater.service
# Red (YouTube) is preinstalled system-wide; German runtime translations are included.
flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak config --system --set languages "de;en"
flatpak install --system --noninteractive --assumeyes flathub com.ktechpit.red
# PiDeck injects controller keys; keep GPU but block native gamepad in Red.
flatpak override --system --nodevice=all --device=dri --device=shm com.ktechpit.red
EOF

cmdline="${ROOTFS_DIR}/boot/firmware/cmdline.txt"
if [ ! -f "$cmdline" ]; then
    echo "Missing Raspberry Pi kernel command line: $cmdline" >&2
    exit 1
fi
for parameter in quiet splash; do
    case " $(cat "$cmdline") " in
        *" $parameter "*) ;;
        *) sed -i "1s/\$/ $parameter/" "$cmdline" ;;
    esac
done

# Argon ONE V5 HDMI-USB2-Audio board: front USB 2.0 hub and 3.5mm jack.
# usb_max_current_enable is required for the FE1.1S hub to enumerate on dwc2.
config_txt="${ROOTFS_DIR}/boot/firmware/config.txt"
if [ ! -f "$config_txt" ]; then
    echo "Missing Raspberry Pi firmware config: $config_txt" >&2
    exit 1
fi
argon_lines='dtoverlay=dwc2,dr_mode=host
usb_max_current_enable=1'
printf '%s\n' "$argon_lines" | while IFS= read -r line; do
    grep -qxF "$line" "$config_txt" && continue
    case "$line" in
        dtoverlay=dwc2*)
            printf '\n# Argon ONE V5 USB hub / 3.5mm audio\n%s\n' "$line" >> "$config_txt"
            ;;
        *)
            printf '%s\n' "$line" >> "$config_txt"
            ;;
    esac
done
