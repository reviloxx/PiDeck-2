# Changelog

Each `## vMAJOR.MINOR.PATCH` section is published as the GitHub release notes
for that tag and shown in PiDeck under **Settings > About** when the update is
available. Write for people using PiDeck on a TV; keep it short.

## v0.6.6

- Added: Appearance → Snowflakes can show falling snow on the home screen
  (Off, On, or Automatic). Automatic is on from 1 December through 6 January.

## v0.6.5

- Changed: Opening an application in Settings → Applications shows a short
  description under the name (English or German). The German arrange action is
  now **Anordnen** instead of **Verschieben**.

## v0.6.4

- Fixed: Switch Pro player LED udev rule was ignored (trailing parenthesis), so
  the connecting animation never stopped after Bluetooth connect.

## v0.6.3

- Fixed: Switch Pro Controller player LEDs show player 1 after connect instead
  of staying in the connecting animation (``hid-generic`` never set them).


## v0.6.2

- Fixed: After Bluetooth connects, the Pro Controller input device appears
  reliably. The kernel ``hid-nintendo`` driver often failed probe on CSR
  reconnect (no ``/dev/input`` node); images and OTA now blacklist it so
  ``hid-generic`` binds instead.

## v0.6.1

- Fixed: Paired Nintendo Switch Pro Controllers connect on the first power-on.
  Automatic host Connect was still racing the pad’s page-in every ~30s; PiDeck
  now only keeps page scan / fast-connectable enabled and lets the controller
  reconnect inbound.

## v0.6.0

- Fixed: When an update is available, **Update** / **Aktualisieren** in About
  is highlighted only after you navigate to it, not while Check or the menu
  has focus.
- Pre-stable milestone (`0.x`). First stable release `v1.0.0` waits until
  Raspberry Pi is fully working.

## v0.5.100

- Fixed: Paired Nintendo Switch Pro Controllers connect on the first power-on.
  Host Connect was paging continuously and racing the pad’s own reconnect
  (BlueZ “already in progress”), so inbound page-in is preferred and host
  Connect is only a rare fallback.

## v0.5.99

- Fixed: Paired Nintendo Switch Pro Controllers reconnect more reliably after
  power-on. PiDeck reacts to BlueZ Connected changes and no longer holds a long
  blackout after a timed-out Connect that can miss several wake attempts.

## v0.5.98

- Fixed: Paired Nintendo Switch Pro Controllers connect on the first power-on
  more reliably. Host reconnect no longer waits out a long “controller off”
  cooldown that could miss the pad’s short advertising window.

## v0.5.97

- Fixed: HDMI-CEC reclaims the TV remote more reliably when PiDeck boots while
  the TV is already on (delayed Active Source retries).
- Fixed: The bottom-left TV-remote icon hides as soon as CEC disconnects or
  reconnects, and when the HDMI link drops.

## v0.5.96

- Changed: Home-screen **Turn off** / **Ausschalten** powers the system off again.
  Suspend/hibernate was freezing the UI on the NVIDIA appliance without reliably
  sleeping. Settings → Shutdown is unchanged.

## v0.5.95

- Changed: Home-screen **Turn off** / **Ausschalten** suspends to RAM instead of
  hibernating. Hibernate was freezing the UI and failing to resume on the
  NVIDIA appliance. Settings → Shutdown still powers off.

## v0.5.94

- Fixed: Turn off shows an error dialog when hibernate fails instead of doing
  nothing visible.
- Fixed: x86 hibernate swapfile is fully written (not sparse) and uses
  ``HibernateMode=shutdown`` so Ausschalten can hibernate on typical PCs.

## v0.5.93

- Changed: The home-screen power button is now **Turn off** / **Ausschalten** and
  hibernates the system instead of shutting down. Settings → Shutdown still
  powers off. x86 images (and SSH host prep) provide the swap and resume support.

## v0.5.92

- Added: Network settings show the live Ethernet link speed (for example
  ``1 Gb/s``) and update it when you unplug, replug, or the link rate changes.

## v0.5.91

- Fixed: Launcher starts again after the Background styles update. Rendering no
  longer needs ``PySide6.QtSvg``, which the appliance image does not install.

## v0.5.90

- Added: Appearance lets you pick a Background style (Glow, Horizon, Ribbons,
  Constellation, Well). Each motif uses the colors of the selected theme.

## v0.5.89

- Fixed: Drive mount and share settings stay owned by the appliance user after
  save, so a root-owned ``drives.yaml`` can no longer block auto-mount or LAN
  sharing.

## v0.5.88

- Changed: Check for updates sits below the PiDeck version card again and is
  highlighted only when you move to it.

## v0.5.87

- Changed: Network settings show a status card with icon, connection state,
  and IP; clearer Ethernet icon.
- Fixed: Selecting About only highlights the left menu until you move Right;
  checking for updates no longer also highlights Appearance.

## v0.5.86

- Changed: Network settings lead with Ethernet / Wi-Fi icons and a short link
  summary; the Wi-Fi list appears only when no cable is connected.
- Fixed: Mount and share errors stay in the drive options dialog instead of
  the list behind it.
- Fixed: About remote navigation no longer jumps away when Check hides, and
  the image build date sits beside the version.

## v0.5.85

- Fixed: About focuses only the Check / Update buttons, not the whole row, so
  remote and controller selection works reliably; German check label shortened.
- Fixed: Drive options stay open after mount, unmount, or share and refresh
  the current state instead of closing.
- Added: Network settings and System information show Ethernet when a cable is
  connected; Wi-Fi setup is unavailable while Ethernet is up so the box does
  not dual-home.

## v0.5.84

- Fixed: Sharing several drives with the same volume label (for example
  Volume1–Volume4) no longer fails with “Share name is already in use” after
  remount. PiDeck frees and re-applies preferred Samba exports together, and
  duplicate labels keep stable numbers by filesystem UUID.

## v0.5.83

- Changed: Hold Guide/Home to open the session overlay; Start no longer opens
  it, so in-game pause and other Start actions stay with the app.
- Changed: About no longer shows an “Up to date” hint next to the installed
  version; Check for updates sits in that spot instead.

## v0.5.82

- Fixed: Switch Pro Controllers no longer disconnect a few seconds after
  connecting (idle HID silence no longer forces a BlueZ Disconnect).
- Fixed: Host reconnect waits longer between failed attempts so inbound
  reconnect can succeed without Connect spam.

## v0.5.81

- Fixed: Theme wallpapers and icons load again after an update that lacked the
  empty release ``config`` directory (paths like ``config/../assets/...``).

## v0.5.80

- Fixed: Late TV power-on recovery now enables HDMI when it is connected but
  has no mode yet (the common NVIDIA/AVR black-screen case). v0.5.79 only
  retrained outputs that already had an active CRTC.

## v0.5.79

- Fixed: Turning the TV on after PiDeck has already booted no longer leaves a
  black screen on NVIDIA/AVR setups. PiDeck re-applies the saved HDMI mode when
  the TV wakes (CEC) or an output becomes active again.

## v0.5.78

- Fixed: Paired controllers can reconnect when turned on (adapter page scan /
  Connectable stays enabled so the pad can page in).
- Fixed: Gamepad icon clears within a few seconds after the controller goes
  silent, instead of waiting on a long BlueZ Connected linger.

## v0.5.77

- Fixed: Switch Pro reconnect no longer wedges BlueZ in InProgress when the
  pad is off (one Connect attempt waits, then cools down instead of retrying).

## v0.5.76

- Added: Input settings can leave the TV off when PiDeck starts (Turn on TV /
  TV einschalten). Switch TV input still reclaims Anynet after the TV wakes.
- Added: Keyboard and HDMI-CEC section headers on the Input settings page.

## v0.5.75

- Fixed: Checking for updates no longer fails on German systems when DNS is
  briefly unavailable at startup (retries temporary name-resolution errors).

## v0.5.74

- Fixed: Switch Pro Controllers reconnect after you turn them off and on again
  (BlueZ no longer gets stuck mid-connect).
- Fixed: Gamepad icon clears promptly when the controller is turned off.

## v0.5.73

- Fixed: Paired Nintendo Switch Pro Controllers connect on the first power-on
  while PiDeck is running (host reconnects trusted gamepads automatically).

## v0.5.72

- Fixed: Wireless keyboard icon stays off at boot when the keyboard is powered
  off (no more false positive before the receiver reports online).
- Fixed: Gamepad icon clears as soon as Bluetooth disconnects, and the
  controller reconnects when you turn it back on.

## v0.5.71

- Fixed: Keyboard icon only shows for a plugged USB keyboard or a wireless
  keyboard that is actually on; the motherboard keyboard is ignored.
- Fixed: Gamepad icon disappears as soon as the controller is turned off.

## v0.5.70

- Fixed: Startup crash that left the appliance on a black console after v0.5.69
  (CEC connection status was read incorrectly).

## v0.5.69

- Fixed: YouTube (Red) can no longer read the controller natively, so inputs
  are not doubled; PiDeck grabs the pad before launch and keeps GPU via Flatpak.
- Added: Home and the session overlay show connected CEC, gamepad, and keyboard
  icons in the bottom-left footer.

## v0.5.68

- Fixed: YouTube no longer double-handles the controller. PiDeck takes exclusive
  ownership while forwarding keys, so Switch A confirms and B goes back once.

## v0.5.67

- Fixed: Controllers work in YouTube and Kodi again (D-pad, A confirm, B back),
  including Nintendo Switch face-button labels. Steam Link and RetroArch still
  use the pad natively.

## v0.5.66

- Fixed: Returning home from Steam Link via the overlay covers PiDeck
  immediately and terminates Flatpak / ``steamlink`` orphans so Steam Link
  does not stay on screen.
- Changed: Nintendo Switch controllers use face-button labels in the PiDeck
  UI (A confirms, B goes Back). Xbox-style controllers are unchanged.

## v0.5.65

- Fixed: Switch Pro Controller Home opens the session overlay (BTN_MODE).
- Fixed: IMU motion nodes are ignored so they no longer compete with the
  gamepad reader.
- Changed: Appliance images set BlueZ ``UserspaceHID=true`` so Bluetooth
  gamepads create input devices on CSR USB adapters.

## v0.5.64

- Fixed: Bluetooth pairing now trusts the device and tries to connect, so
  controllers like the Switch Pro stay usable after pairing instead of only
  showing as paired.

## v0.5.63

- Fixed: In Kodi, Back / Return stops fullscreen playback instead of leaving
  the video running in the background.

## v0.5.62

- Changed: Fully restored the application tree from **v0.5.57** (before
  per-app PCM, Fixed Kodi channels, and related HDMI promote changes).
  Use this when later audio OTAs leave the AVR on stereo.

## v0.5.61

- Changed: Removed per-application 2.1/5.1 PCM layouts. Surround PCM is
  system-wide again for every app; Kodi still uses Fixed 5.1 in surround mode.
- Fixed: Changing HDMI mode no longer hits Pulse ``No such entity`` when the
  pre-switch stereo sink was destroyed during the surround profile promote.

## v0.5.60

- Fixed: Kodi PCM surround uses Output configuration **Fixed** with 5.1
  channels so a 5.1 movie is sent as multichannel PCM. Optimized mode was
  opening a stereo Pulse stream, so the AVR kept showing 2 CH STEREO.

## v0.5.59

- Fixed: Opening Kodi (or any app on system Surround) switches HDMI back to
  PCM 5.1 after another app used 2.1/stereo. Pulse no longer keeps a stale
  stereo sink or picks a DTS profile.
- Fixed: Settings → Audio scrolls when many apps are listed, so controls no
  longer overlap the footer.

## v0.5.58

- Added: Settings → Audio can set per-application **2.1 PCM** or **5.1 PCM**
  (never bitstream). Choices are stored in ``audio.yaml`` and applied at launch
  (Pulse profile for every app; Kodi also gets matching channel settings).
- Fixed: Samba share paths reject newlines and tabs so a crafted path cannot
  inject share conf lines.
- Fixed: New x86 images install and enable ``systemd-timesyncd`` so the home
  clock stays on time. Rebuild the image for this; OTA does not add the package.
- Changed: New x86 images load NVIDIA earlier (initramfs local-bottom), mask
  ``NetworkManager-wait-online`` and ``plymouth-quit-wait``, and skip grow-root
  after the first settle. Rebuild the image for these; OTA does not change the
  boot stack.

## v0.5.57

- Fixed: Chrome and Brave use NVIDIA hardware video decoding again (NVDEC via
  VA-API). Launch flags now enable ``AcceleratedVideoDecodeLinuxGL``,
  ``AcceleratedVideoDecodeLinuxZeroCopyGL`` and ``VaapiOnNvidiaGPUs``; existing
  installs pick them up after update.
- Changed: YouTube (Red) gets Flatpak VA-API environment overrides. Red still
  disables accelerated decode in QtWebEngine, so its video path remains software
  until upstream removes that.

## v0.5.56

- Fixed: After update, Openbox/picom no longer die as zombies under the launcher.
  Without a window manager, Brave/YouTube stayed small and clicks/CEC focus failed;
  the session now treats unreaped helpers as stopped and starts a live WM again.

## v0.5.55

- Added: Settings > System > Remote support installs OpenSSH and authorizes a
  support key so the TV can be reached over the LAN without a console password.
- Fixed: When the system clock is before 2024, PiDeck requests NTP before package
  installs (Flatpak SSL error 60 is usually a wrong clock).

## v0.5.54

- Fixed: On the TV, Brave/YouTube fill the screen and keyboard/CEC work inside
  apps again. Existing Brave installs pick up ``--start-maximized`` after update,
  ignored maximize falls back to full-screen geometry, the app window is focused
  when the launcher hides, and Openbox maximize rules are re-applied after OTA.

## v0.5.53

- Fixed: Brave, Chrome, Chromium and YouTube open maximized reliably. Browsers
  get ``--start-maximized``, Openbox maximizes ``Brave-browser`` / ``red`` (and
  other browser classes), and PiDeck reapplies maximize shortly after the window
  maps so Chromium cannot keep a restored size.

## v0.5.52

- Fixed: YouTube and Brave open maximized again. When the main window was slow
  to appear, PiDeck stepped aside without applying maximize; it now does a
  last-chance maximize, and YouTube is configured for maximized windows.

## v0.5.51

- Fixed: Settings, the home overlay, and other PiDeck panels fill the screen
  again. They were being recentered as small dialogs after Openbox mapped them
  fullscreen; they now use the same frameless screen geometry as the launcher.

## v0.5.50

- Fixed: The home screen no longer jumps back over a running app after a while.
  When PiDeck cannot detect the app window, it steps aside instead of raising
  itself, and delayed fullscreen reasserts are cancelled once the launcher hides.
- New: Settings > Display has a brightness slider (10–100%) that dims the whole
  screen, including apps. The value is saved and restored on startup.

## v0.5.48

- Fixed: PiDeck stays fullscreen. Openbox was restarted on every launch because
  the config path under ``current`` did not match the resolved release path;
  the launcher also forces screen-sized frameless fullscreen.

## v0.5.47

- Fixed: PiDeck fills the whole screen again after an update that refreshes
  Openbox. The session waits for the window manager and re-applies fullscreen.

## v0.5.46

- Fixed: Mouse clicks work again in PiDeck and in apps. Openbox was keeping
  left-clicks instead of passing them through; the update applies the fixed
  window-manager config without reflashing.

## v0.5.45

- Fixed: The mouse pointer shows again when you click inside a running app, and
  when you return to the home screen after closing an app.
- Changed: Aurora, Ember, Nordic, and Daylight backgrounds are brighter again
  while staying flat (less banding). Midnight stays darker.

## v0.5.44

- Changed: Aurora is the default theme for new installs and appears first in
  Design. Existing installs keep their saved theme. Midnight is a little darker
  so it differs more from Aurora. Theme backgrounds use flatter fills to reduce
  banding on TVs.
- Changed: When two drives share the same volume label, Settings and Samba use
  numbered names (for example Volume1, Volume2) instead of Volume and Volume1.

## v0.5.43

- Fixed: HDMI audio defaults to Surround PCM 5.1 for PC→AVR→TV again. Settings
  adds an HDMI mode: Surround PCM 5.1 (AVR) or Bitstream (TV ARC). Launching
  Kodi applies the selected mode.

## v0.5.42

- Changed: HDMI audio targets TV ARC passthrough. Settings prefers Digital
  Stereo (HDMI) per port, enables AC3/DTS/EAC3 sink formats, and Kodi uses
  2.0 channels with passthrough and AC3 transcoding (sync-to-display off).

## v0.5.41

- Fixed: Settings > Audio lists every HDMI port (for example HDMI and HDMI 2 on
  NVIDIA cards), not only the active one. IEC958 / Xonar sinks are hidden when
  HDMI is available. Surround 5.1 promotion stays on the selected port.

## v0.5.40

- Changed: Settings > Audio is a single system output and volume again. Xonar /
  S/PDIF support and per-application outputs are removed. HDMI sinks are
  promoted to Digital Surround 5.1 when PulseAudio offers that profile.
- Changed: Kodi launches with PulseAudio Default, 5.1 channels, and passthrough
  off so multichannel PCM plays over HDMI.

## v0.5.39

- Fixed: ASUS Xonar DX S/PDIF appears in Settings > Audio when PulseAudio only
  shows analog. PiDeck detects the ALSA Digital PCM and opens it with
  `module-alsa-sink`. New x86 images also ship an oxygen PulseAudio profile set
  and `alsa-utils` so Digital Stereo (IEC958) can appear as a native profile.

## v0.5.38

- Fixed: Samsung Stop works again by registering as a CEC recording device
  (same default as Kodi), not playback.
- Fixed: after the TV wakes, PiDeck reclaims Anynet with active source when
  Switch TV input is on (now the default for new installs). Enable it under
  Settings > Input if an older config still has it off.

## v0.5.37

- Fixed: Share on network works again when PiDeck cannot create
  `/media/pideck/by-uuid` links. The real mount path is used instead.
- Changed: Settings > Drives shows mount and share status as icons in the list.
  Mount path and SMB URL details are in the drive options menu.

## v0.5.36

- Fixed: LAN drive shares open from other computers again. Samba now exports the
  real mount directory instead of the by-uuid symlink. After updating, stop
  sharing the drive and share it once more (or reboot) so the share is
  rewritten.
- Fixed: the SMB URL status line above the drive list appears only while a
  shared drive is highlighted.

## v0.5.35

- Fixed: Settings > Drives options menu stays open when activated with Enter or
  OK. The key that opened the menu no longer immediately runs Mount.

## v0.5.34

- Fixed: HDMI-CEC connects again. Auto-reconnect no longer restarts the client
  on normal startup power-status lines (unknown to on).

## v0.5.33

- Added: in Settings > Drives, select a drive for options (mount, unmount, and
  share on the local network). A single click opens the menu. Shared drives are
  guest read-write Samba exports (`smb://<hostname>/<ShareName>`, for example
  from Linux Mint Files). Sharing requires a current image with Samba.
- Added: Settings > Audio can set a system default output and optional
  per-application outputs (stored in `audio.yaml`). Inactive card profiles such
  as S/PDIF appear in the list; Kodi follows PulseAudio Default so routing
  applies.

## v0.5.32

- Fixed: HDMI-CEC registers as one Player again. Dual Player/Recorder from
  v0.5.28 broke Anynet connection on Samsung TVs. TV wake reconnect remains.

## v0.5.30

- Fixed: update checks honor GitHub rate-limit headers (`Retry-After` /
  `X-RateLimit-Reset`) and wait until that time instead of retrying early.
- Fixed: while rate-limited, PiDeck uses the last successful check and shows
  when the next check is allowed.

## v0.5.29

- Fixed: installing a PiDeck update retries briefly when GitHub returns a
  temporary error such as HTTP 504, instead of failing on the first attempt.
- Fixed: Retry after a failed install tries the download again, not only a new
  update check.

## v0.5.28

- Fixed: HDMI-CEC reconnects automatically after the TV is turned off and on
  again (and when the CEC link drops).
- Changed: PiDeck registers as a CEC playback and recording device so Samsung
  remotes are more likely to send Stop.

## v0.5.27

- Added: Settings > Input > CEC debug shows live remote key codes so unmapped
  buttons (for example Stop) can be diagnosed on the TV without a terminal.

## v0.5.26

- Fixed: Samsung TV Stop works in Kodi and YouTube (CEC Stop Function).

## v0.5.25

- Fixed: keyboard input in virtual machines after v0.5.24. Picom now uses a
  VM-safe xrender profile there; NVIDIA keeps GLX with vsync, and other
  physical GPUs keep xrender with vsync. Restart the PiDeck session after
  updating.

## v0.5.24

- Fixed: video tearing under the session overlay (for example 1080p24 in Kodi)
  by enabling picom vsync on physical GPUs. An application update applies it;
  restart the PiDeck session after updating.
- Fixed: mounted drives keep a stable `/media/<user>/by-uuid/<UUID>` path across
  remounts so Kodi library sources do not break after restart. Re-point existing
  Kodi sources once to that path (shown in Settings > Drives).

## v0.5.23

- Fixed: Kodi no longer inherits browser VA-API settings, so NVIDIA decode can
  use VDPAU cleanly.
- Fixed: Kodi cannot open the HDMI-CEC adapter while PiDeck owns it.
- Note: NVIDIA smooth composited video uses picom's `glx` profile from the
  application update; virtual machines use a separate safe profile.

## v0.5.22

- Fixed: on NVIDIA PCs, Kodi uses hardware video decoding (VDPAU) again instead
  of lagging on software decode.

## v0.5.21

- Drives mounted in Settings are remounted automatically after a restart.

## v0.5.20

- YouTube: TV Return and Stop exit a video (both sent as Escape). Stop no
  longer uses the media Stop key, which Red ignores.

## v0.5.19

- NoMachine opens fullscreen.
- Browser windows keep only the close button (no minimize or maximize).
- Remove and other confirmations use the secondary text style for the
  explanation.
- Installed applications always appear on the home screen; Show and Hide
  options are removed.

## v0.5.18

- Fixed: new themes appear in Design after an update, even when an older
  settings file still listed only Midnight.

## v0.5.17

- Appearance keeps theme and language only; reduced motion is always on and the
  home clock is always shown.
- System settings gain a Timezone page that changes the host timezone.
- New themes: Ember, Nordic, Aurora, and Daylight (Midnight remains the default).

## v0.5.16

- Fixed: Samsung Return Back works repeatedly in Kodi and YouTube. Remotes that
  never send a CEC key-released for Return no longer stick after the first press.

## v0.5.15

- Fixed: Samsung Return now goes Back in Kodi and YouTube while Exit still
  opens the home overlay. A hidden overlay can no longer block remote keys.
- YouTube Back uses Backspace; Kodi Back uses Escape.

## v0.5.14

- Home-return overlay explanation uses the settings description style.
- Changelog headings no longer mention that notes are in English.
- After a PiDeck update is ready, a dialog offers an immediate restart.

## v0.5.13

- The in-app overlay now shows the PiDeck title and clock, and asks
  **Return home?** with Cancel and Return home.

## v0.5.12

- Confirmation dialogs (shutdown, restart, and others) use the same blurred
  backdrop as **Back to PiDeck**.
- Home screen no longer shows help text or launch status under the PiDeck title.
- YouTube's tile shows a TV remote instead of a gamepad.

## v0.5.11

- Fixed: the **Back to PiDeck** blur now appears every time the overlay opens,
  not only every other time.

## v0.5.10

- Fixed: the **Back to PiDeck** blur no longer shows a leftover rectangle from
  the previous app; the overlay rebuilds its window and fades the full screen.

## v0.5.9

- Fixed: opening **Back to PiDeck** after switching apps no longer keeps the
  previous app's blurred background; the overlay redraws the full screen.

## v0.5.8

- Remote Pause works in Kodi and YouTube (sent as play/pause).
- The mouse pointer hides inside apps when you use the remote or a controller.
- The overlay blur now shows the app you are in, not an earlier screenshot.

## v0.5.7

- The **Back to PiDeck** overlay now shows the running app blurred behind a
  simple centered button, without how-to text.

## v0.5.6

- **Back to PiDeck from any app:** press the Windows key, Xbox Guide button or
  TV Exit key for an overlay with **Back to PiDeck**, which closes the app.
  Press the same key again to stay in the app.
- PiDeck keeps HDMI-CEC while apps run and forwards the remote. Exit opens the
  overlay; Return goes Back. Apps no longer get native CEC.

## v0.5.5

- Fixed: on the home screen, Right no longer jumps from the end of a row to
  the next row. Use Down to move between rows.
- Fixed: in Settings, Up no longer leaves a section for the menu on the left.
  Use Left to go back to the categories.

## v0.5.4

- **Kodi keeps PiDeck's HDMI-CEC connection** by default. The TV remote
  controls Kodi through PiDeck, and Kodi's own CEC adapter is turned off so
  both no longer fight over the same device.
- To use Kodi's built-in CEC again, set `cec: release` for Kodi in
  `pideck.yaml`.

## v0.5.3

- **Backspace goes back** everywhere, like Escape and the remote's Back button.
  In text fields and the on-screen keyboard it still deletes.
- Fixed: moving down past **Back** in Settings no longer leaves nothing
  highlighted.

## v0.5.2

- **Sort applications in the list:** select an application and choose
  **Move**, then move it with Up and Down. OK saves the new order, Back keeps
  the old one.
- Fixed: after moving an application, the keyboard and remote no longer
  selected anything in Settings.

## v0.5.1

- **One place for applications:** Settings > Applications now replaces the
  separate Home screen and Updates pages. Every application shows whether it is
  on the home screen, its installed version and available updates.
- Select an application to show or hide it, move its tile up or down, update,
  install or remove it.
- PiDeck updates now show what's new before you install them.

## v0.5.0

- New images only include Kodi and YouTube. Steam Link, Chromium, Chrome,
  Brave, Firefox, NoMachine and RetroArch can be installed and removed from
  Settings > Applications. Removing an application keeps its settings.
- PiDeck, the system and applications default to German on new images.
- Chrome, Brave and the NoMachine client are new.
