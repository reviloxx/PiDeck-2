#!/bin/bash -e

install -d "${ROOTFS_DIR}/usr/lib/pideck/python" \
    "${ROOTFS_DIR}/usr/lib/pideck-updater/python" \
    "${ROOTFS_DIR}/usr/share/pideck/config" \
    "${ROOTFS_DIR}/usr/share/pideck/assets" \
    "${ROOTFS_DIR}/etc/profile.d" \
    "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d" \
    "${ROOTFS_DIR}/etc/systemd/system/userconfig.service.d"

cp -a files/project/src/pideck "${ROOTFS_DIR}/usr/lib/pideck/python/"
cp -a files/project/src/pideck "${ROOTFS_DIR}/usr/lib/pideck-updater/python/"
cp -a files/project/assets/. "${ROOTFS_DIR}/usr/share/pideck/assets/"
install -m 644 files/pideck.yaml "${ROOTFS_DIR}/usr/share/pideck/config/pideck.yaml"
install -m 755 files/pideck-getty files/pideck-session files/pideck-xsession \
    "${ROOTFS_DIR}/usr/lib/pideck/"
install -m 644 files/pideck-profile.sh "${ROOTFS_DIR}/etc/profile.d/pideck.sh"
install -m 644 files/getty-override.conf \
    "${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d/pideck.conf"
install -m 644 files/userconfig-override.conf \
    "${ROOTFS_DIR}/etc/systemd/system/userconfig.service.d/pideck.conf"
install -m 644 files/pideck-updater.service files/pideck-activate.service \
    "${ROOTFS_DIR}/etc/systemd/system/"

if [ -f files/project/release-version ]; then
    version="$(cat files/project/release-version)"
    release="${ROOTFS_DIR}/usr/lib/pideck/releases/$version"
    install -d "$release/python" "$release/assets" "$release/config"
    cp -a files/project/src/pideck "$release/python/"
    cp -a files/project/assets/. "$release/assets/"
    ln -s "releases/$version" "${ROOTFS_DIR}/usr/lib/pideck/current"
    install -m 644 files/project/update-public.pem "${ROOTFS_DIR}/usr/share/pideck/update-public.pem"
fi

on_chroot <<'EOF'
systemctl enable NetworkManager
systemctl enable pideck-updater.service
EOF
