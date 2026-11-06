// PiDeck: decode video through VA-API (NVDEC via nvidia-vaapi-driver).
pref("media.ffmpeg.vaapi.enabled", true);
pref("media.hardware-video-decoding.force-enabled", true);
pref("media.rdd-ffmpeg.enabled", true);
pref("gfx.x11-egl.force-enabled", true);
pref("widget.dmabuf.force-enabled", true);
// The GeForce GT 1030 cannot decode AV1; prefer VP9 or H.264 streams instead.
pref("media.av1.enabled", false);
