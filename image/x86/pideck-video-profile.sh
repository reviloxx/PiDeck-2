# Hardware video decoding with the proprietary NVIDIA driver. Sourced before
# pideck.sh starts the session, so launched applications inherit it. Other
# GPUs keep the VA-API driver that libva detects automatically.
if [ -r /proc/driver/nvidia/version ]; then
    export LIBVA_DRIVER_NAME=nvidia
    export NVD_BACKEND=direct
    export MOZ_DISABLE_RDD_SANDBOX=1
fi
