#!/bin/bash -e

install -d "${ROOTFS_DIR}/usr/lib/pideck/python" \
    "${ROOTFS_DIR}/usr/lib/pideck-updater/python" \
    "${ROOTFS_DIR}/usr/share/pideck/config" \
    "${ROOTFS_DIR}/usr/share/pideck/assets" \
    "${ROOTFS_DIR}/etc/profile.d" \
    "${ROOTFS_DIR}/etc/udev/rules.d" \
    "${ROOTFS_DIR}/usr/share/plymouth/themes/pideck" \
    "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d" \
    "${ROOTFS_DIR}/etc/systemd/system/userconfig.service.d"

cp -a files/project/src/pideck "${ROOTFS_DIR}/usr/lib/pideck/python/"
cp -a files/project/src/pideck "${ROOTFS_DIR}/usr/lib/pideck-updater/python/"
cp -a files/project/assets/. "${ROOTFS_DIR}/usr/share/pideck/assets/"
install -m 644 files/pideck.yaml "${ROOTFS_DIR}/usr/share/pideck/config/pideck.yaml"
install -m 644 files/70-pideck-cec.rules "${ROOTFS_DIR}/etc/udev/rules.d/70-pideck-cec.rules"
install -m 644 files/project/src/pideck/infrastructure/cec_support/71-pideck-cec-attach.rules \
    "${ROOTFS_DIR}/etc/udev/rules.d/71-pideck-cec-attach.rules"
install -m 644 files/project/src/pideck/infrastructure/cec_support/pideck-cec-attach@.service \
    "${ROOTFS_DIR}/etc/systemd/system/pideck-cec-attach@.service"
install -m 644 files/window-modes.yaml "${ROOTFS_DIR}/usr/share/pideck/config/window-modes.yaml"
install -m 755 files/pideck-getty files/pideck-session files/pideck-xsession \
    "${ROOTFS_DIR}/usr/lib/pideck/"
install -m 644 files/pideck-profile.sh "${ROOTFS_DIR}/etc/profile.d/pideck.sh"
install -m 644 files/pideck.plymouth files/pideck.script \
    "${ROOTFS_DIR}/usr/share/plymouth/themes/pideck/"
install -m 644 files/getty-override.conf \
    "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d/pideck.conf"
install -m 644 files/userconfig-override.conf \
    "${ROOTFS_DIR}/etc/systemd/system/userconfig.service.d/pideck.conf"
install -m 644 files/pideck-updater.service files/pideck-activate.service \
    "${ROOTFS_DIR}/etc/systemd/system/"

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
