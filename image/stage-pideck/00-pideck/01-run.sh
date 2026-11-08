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
install -m 644 files/00-pideck-keyboard.conf "${ROOTFS_DIR}/etc/X11/xorg.conf.d/00-pideck-keyboard.conf"
install -m 644 files/project/src/pideck/infrastructure/cec_support/71-pideck-cec-attach.rules \
    "${ROOTFS_DIR}/etc/udev/rules.d/71-pideck-cec-attach.rules"
install -m 644 files/project/src/pideck/infrastructure/cec_support/pideck-cec-attach@.service \
    "${ROOTFS_DIR}/etc/systemd/system/pideck-cec-attach@.service"
install -m 644 files/window-modes.yaml "${ROOTFS_DIR}/usr/share/pideck/config/window-modes.yaml"
install -m 644 files/openbox/rc.xml "${ROOTFS_DIR}/usr/share/pideck/openbox/rc.xml"
install -m 755 files/pideck-getty files/pideck-session files/pideck-xsession \
    "${ROOTFS_DIR}/usr/lib/pideck/"
install -m 644 files/picom.conf "${ROOTFS_DIR}/usr/lib/pideck/picom.conf"
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
    "${ROOTFS_DIR}/usr/share/pideck/keyrings/"
install -m 644 files/google-chrome.sources files/brave-browser.sources \
    "${ROOTFS_DIR}/etc/apt/sources.list.d/"
install -m 644 files/google-chrome.default "${ROOTFS_DIR}/etc/default/google-chrome"
install -m 644 files/firefox-policies.json "${ROOTFS_DIR}/etc/firefox/policies/policies.json"
install -m 644 files/56-steamlink.rules "${ROOTFS_DIR}/usr/lib/udev/rules.d/56-steamlink.rules"
install -m 644 files/uinput.conf "${ROOTFS_DIR}/etc/modules-load.d/pideck-uinput.conf"

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
systemctl enable pideck-updater.service
# A NoMachine Player install must never start a remote-desktop server.
systemctl mask nxserver.service
# Red (YouTube) is preinstalled system-wide; German runtime translations are included.
flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak config --system --set languages "de;en"
flatpak install --system --noninteractive --assumeyes flathub com.ktechpit.red
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
