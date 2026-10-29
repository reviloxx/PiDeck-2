#!/bin/bash -e

systemctl enable getty@tty1.service
dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' \
    > /usr/share/pideck/package-manifest.tsv
