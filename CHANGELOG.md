# Changelog

Each `## vMAJOR.MINOR.PATCH` section is published as the GitHub release notes
for that tag and shown in PiDeck under **Settings > About** when the update is
available. Write for people using PiDeck on a TV; keep it short.

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
