[Русский](CHANGELOG.md) · **English**

# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [semantic](https://semver.org/).

## 0.1.1 — 2026-08-23

The first update after the public build. The two languages that shipped inside
0.1.0 without a version bump turned out to be flawed — that is fixed here,
along with link forwarding to the host browser.

### Added

- **Two interface languages — Russian and English.** Switched in the settings,
  the choice is saved and the window is redrawn right away. Until a language is
  picked the system one is used: a Russian speaker gets Merci in Russian with
  no setup, everyone else gets English.
- **An English README** and a language switcher at the top of both files.
- **This file.** Every release from now on is described here.

### Fixed

- **The English interface dropped setup steps.** The plan was built from the
  container's state description, and that description arrived already
  translated: the comparison against the Russian text no longer matched, so the
  image-server connectivity check and the Android image download fell out of
  the plan. The wizard offered to start a session that had nothing to run on.
  Logic now works on the source strings, and translation happens where the text
  goes to the screen.
- **Links from Android did not open in the host browser.** The service starts
  together with the systemd session — before the compositor puts its
  environment there — and Chromium, seeing no sign of a Wayland session, fell
  back to X11, found no `$DISPLAY` and exited within half a second. `xdg-open`
  reported success all the while, so from the outside it looked like nothing
  happened. The environment is now taken from the live session on every
  request.
- **Restarting the link service closed the browser** along with every tab: a
  browser opened through a link stayed in the service's cgroup, and systemd
  kills the whole group.
- **Links did nothing in a profile where the forwarder had never been
  launched.** Android 13 does not offer "stopped" packages to implicit intents,
  and it has no `am unstop` yet. Merci checks what the profile would open a
  link with and wakes the forwarder once — only when it does not answer.
- **Untranslated spots in the English interface:** step titles and hints,
  installation phases, download counters. Hints for failed steps were
  translated at import time, that is before a language is chosen, and stayed
  Russian forever.
- **A "1600х900" resolution typed on a Russian layout** stopped parsing in the
  English interface: replacing the Cyrillic "х" with a Latin one got caught by
  the translator and turned into a no-op. File size units no longer depend on
  the dictionary either.

### Changed

- **Distribution comes from GitHub Pages only** —
  `https://melineceo.github.io/Merci`. A custom domain for the Flatpak
  repository was tried and rolled back: a `CNAME` file published ahead of the
  DNS record pointed `github.io` itself into nowhere, and distribution stopped
  working from both addresses at once. The project page on
  `repo.hackerstone.xyz` stays, and its install command points at GitHub.
- **The application icon** was rebuilt from the corrected file for every size
  from 16 to 256 pixels — menu, search, tray and repository.
- **The link service log now shows the result of `xdg-open`** even on a
  successful return code: silence in the log was the most expensive situation
  to debug.

## 0.1.0 — 2026-08-21

The first public build (beta).

- APK library: drop a file in and Merci parses the manifest and launches it in
  Waydroid.
- A container setup wizard with a live log: kernel module, Android image,
  session, ARM64 translation.
- Hardware acceleration on NVIDIA through `waydroid-nvidia`.
- Android profiles: a second account of the same app without logging out.
- Links from Android open in the host browser.
- A tray icon with container controls.
