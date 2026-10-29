# Start the appliance only for the Imager-provisioned user on the first console.
if [ -n "${PS1:-}" ] && [ -z "${DISPLAY:-}" ] \
    && [ "$(id -u)" = 1000 ] && [ "$(tty)" = /dev/tty1 ]; then
    exec /usr/lib/pideck/pideck-session
fi
