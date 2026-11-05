# Build environment for image/x86/container_build.py. It provides the host
# tools required by image/x86/build.py on Debian trixie.
FROM debian:trixie

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates debian-archive-keyring debootstrap dosfstools e2fsprogs \
        fdisk gdisk git gpgv mount openssl python3 systemd udev util-linux xz-utils \
    && rm -rf /var/lib/apt/lists/*
