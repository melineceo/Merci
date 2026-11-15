[Русский](CHANGELOG.md) · **English**

# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [semantic](https://semver.org/).

## 0.2.0 — 2026-11-15

A big release about three things: a new interface, updates and other systems.

### Added

- **New interface: Material You and “liquid glass”.** The window is split
  into logic and view, and the view is rewritten from scratch. A glass
  library list, a glass header with the container “island” (state, Eco and
  FPS at hand, expands into a panel), app windows as “screen” tiles with the
  state on the screen itself, a floating Launch button that shows what is
  being done while it works. Colours come from the icon of the selected app
  — the Material You engine is written in Python and matches the reference
  (matugen) to a single channel unit. The glass is real — GTK itself blurs
  the backdrop, so it looks the same on any desktop; “Reduce transparency”
  is there for weak GPUs. Light and dark themes, a theme choice in settings,
  a narrow window folds the library into an icon rail.
- **Update centre** (“Updates” in the menu and in the library). Merci checks
  by itself — at start-up and once a day — what is new: the Android image,
  the Waydroid package (including waydroid-nvidia from the AUR), the ARM
  translator and Merci itself. It installs on your click, one password
  prompt per update. Updating is built not to break anything:
  - before the image changes, extra windows are switched off (they keep the
    image mounted) and the container service is stopped;
  - the current image is kept (instantly and for free on btrfs and xfs), and
    if the new one does not work it can be brought back with one button;
  - an interrupted extraction is rolled back automatically; an update cannot
    be cut off halfway, and a stalled download is stopped by the helper;
  - Merci's settings come back after the update: window size, the Android
    user limit, the flicker fix, the overlay with the translator;
  - waydroid-nvidia is set up again after its package update (the container
    does not start without it) — in the same rendering mode, not in whatever
    the new version considers the default; the Venus server is restarted;
  - at the end Merci checks not “the update ran” but “everything works”: the
    session is up, Android responds, the translator and acceleration are
    in place.
- **Android's built-in updater is now hidden** (can be turned off in
  settings). Tapping its notification rewrote the image underneath running
  windows; Merci installs updates instead.
- **Other systems.** The portability layer knows the Arch, Debian, Fedora,
  openSUSE, Void, Alpine and NixOS families and immutable systems: how to
  install packages, how to ask for the password (sudo with a window, pkexec,
  run0, doas, a terminal), which firewall blocks the container (ufw,
  firewalld), what the container service is called (systemd, OpenRC,
  runit). In an X11 session Waydroid runs inside a nested Wayland
  compositor. Where Merci does not know the command or may not run it, the
  wizard says what to do.
- **A build has a home.** Several builds of one package (the stock client
  and a modified one) no longer evict each other: Launch goes to the build's
  own window; if there is none, it asks whether to create one. Replacing a
  build is first tried as an in-place update that keeps the data; erasing
  happens only after a separate question listing what will be lost.
- Renaming entries; a mark on the library row showing in how many windows
  the app is open; “Retry” when the window list cannot be read.

### Fixed

- **Launching after power-on.** A hang while creating the window (a deadlock
  in the waydroid-nvidia composer before 0.2.0) is now recognised in 20
  seconds instead of 40, and there are three attempts instead of two. The
  cause is fixed in waydroid-nvidia 0.2.0 itself — Merci will offer that
  update.
- The setup wizard showed “Waydroid is ready” when the Android image was not
  downloaded: the plan crashed on its own bug and an empty plan read as
  “all done”.
- The chosen window size silently rolled back: the window kept a snapshot of
  the settings from start-up and wrote the old values back with its own
  saves.
- “Close” and “Stop all” on one build's card closed another build's game
  when the window held a different build of the same package.
- “Remove from the container” on one build's card wiped another build's
  install and data. Removing an entry no longer touches Android profiles and
  erases the entry's windows before the entry itself, not “afterwards and
  silently”.
- Windows without an owner (the entry was removed, the containers stayed)
  are no longer lost: the first opened card adopts them.
- The root helper: one operation at a time (a lock), a check that a
  window's settings are really separated from the main container, the
  package checksum is verified before installing.
- “Copy command” produced an unusable line for script steps.
- **“Remove root (Magisk)” ended with an error and left Waydroid switched
  off** — although root had already been removed by then. The removal was done
  by the third-party `waydroid_script`, which treats any line on stderr as a
  failure; `waydroid-nvidia` 0.2.0 prints its acceleration mode there on every
  `waydroid upgrade -o`. On top of that, the removal downloaded Magisk itself
  for no reason.
- **“Install root (Magisk)” could not download Magisk**: the repository
  `waydroid_script` took it from disappeared in November 2026 (404). The
  download had no checksum and retried the server's refusal thirty times.
- “Switch ARM translator” to libndk failed on the same stderr line.
- **A privilege hole:** installing libndk (and libhoudini on images whose
  version Merci did not know) ran a third-party script as root from a
  directory any program of the user can write to, and changed the owner of a
  directory along a path the same program could replace with a link. By
  swapping a file, such a program got root at the very moment the person
  typed the password.
- Cancelling a step in the middle of root work: the step's shell exited at
  once, with an “all good” code, and brought Waydroid up next to the helper
  that was still working. The step now waits for the helper, and “Cancel” in
  the wizard is there only while the step downloads or builds: once root
  work begins it disappears — it never reached that work anyway. The package
  swap during the first NVIDIA acceleration setup is protected the same way:
  cancelling between removing `waydroid` and installing the new package left
  the machine without Waydroid. Where the password is typed in a terminal,
  closing that terminal in the middle of such work is no longer reported as
  “the step failed”: the wizard waits for its real end.
- Setup wizard: after a failed step “Check again” led to a dead end — the
  caption said “Ready to install” (or “Waydroid is ready”) while the button
  under it copied the command. “Close” after completed steps now actually
  closes the wizard instead of starting another check.
- The interface is smoother: the dock and panel scrim are animated, the glass
  blur costs less rendering work, and hidden layers no longer intercept clicks
  on the main buttons.
- A failed translator step or a cancelled password prompt left Waydroid
  switched off.

**Actions that did not look at the current state.** A separate pass over
the whole program after one report: “I installed NVIDIA acceleration, I
click it again — and it says not installed again”.

- **“NVIDIA hardware acceleration” offered to enable what was already
  enabled.** The menu item never checked whether acceleration was in place.
  You agreed — the package was rebuilt and reinstalled, and the setup reset
  the flicker fix. The item now says what is installed (version, mode, how
  the container renders right now) and only offers “Set up again” — through
  the helper, which puts Merci's settings back. The first install takes the
  same guarded path as a package update. Merci now notices a reset flicker
  fix by itself, at startup, and offers to restore it.
- “Install root” and “Remove root” look at whether root is there: an
  installed one is offered a reinstall, a missing one gets “nothing to
  remove”, leftovers get “remove the traces”. “Switch ARM translator” with
  no translator offers to install one instead of “switching”.
- **A stopped container was read as “no”.** “Fix flickering picture” answered
  “already off”, MultiUser said “more than one user is not allowed” (and
  offered to prepare what was ready), the “each app in its own window”
  switch showed “off”: properties were only asked from a running container.
  For a stopped one Merci now reads the files it will boot with.
- “Each app in its own window” changed nothing while Waydroid was stopped,
  yet reported success. The container is now started first and the setting
  is verified; if it did not take, the switch goes back. The restart runs
  under the common lock, not on top of a launch or an update.
- **A cancelled password prompt left Waydroid switched off** in “Lock the
  container's adb with a key”, “Restart the whole container” and “Allow
  several Android users”; “Restore the ARM64 translator” reported
  success on cancel and did not stop the extra windows, so the overlay got
  disabled again. All four now use the common skeleton: one password
  prompt, the work is done by the helper, Waydroid returns to the state it
  was in. The helper restarts the container service with the tools of the
  system at hand (systemd, OpenRC, runit) — it used to know systemd only,
  and elsewhere the user limit silently never took effect.
- **Eco Mode and the FPS limit** only reached the main window, and only if
  it was running at the moment of the click; the FPS limit was also lost on
  every restart. Merci still said “enabled”. Every window now gets the
  settings when it starts, a switched-off setting is undone exactly where
  it was applied, and the message tells the truth: applied now, or will
  apply when Android starts.
- “Apply” (multi-window) and “Clean memory” reported success with the
  container stopped.
- “Hide the built-in updater”: turning it off brought the updater back only
  in running windows while saying “is back” — the rest now get it when they
  start. A window that was deleted and created again is no longer treated
  as “already hidden”.
- **Adding the same APK again overwrote the entry:** the name you gave it,
  the chosen Android profile, the last-run time. Merci now says “already in
  the library” and shows that entry.
- “Remove from the container” was available under “not installed” too — and
  ended by asking you to confirm the removal in the Android window.
- “Details” after an app crash showed the most recent report of all — it
  could belong to another app — or “no records”. It is now the very report
  that was found; crashes in extra windows are found as well, and the words
  in the crash log are translated.
- The “Set up Waydroid” wizard did not hold the window: while it worked as
  root, the tray could start the session and state polling woke the stopped
  container service. After a failure or a cancel the card showed stale
  state.
- “Fit to the screen” was offered every time the wizard opened with Waydroid
  stopped and restarted it for nothing. It is no longer offered while extra
  windows are open: restarting the main session under them disabled the
  overlay with the translator.
- A new window size did not reach the main container if it was stopped at
  the moment of the change.
- “Set up acceleration again” was offered again after a successful setup:
  the steps did not remember what they had done. The same went for a
  flicker fix applied by the wizard: Merci did not know about it and did
  not restore it after an update.
- Tray: “Stop Waydroid” on a stopped one asked and “stopped” it; “Restart”
  ran without a question although it closes everything running; “Open the
  running game” launched the last one even when nothing was running. While
  root work is in progress the tray items leave the container alone.
- Updates window: “Delete the image copy” and “Restore the image” check
  that no update is running; Merci does not update itself twice at once.
- **A step that restarts the container was declared done three seconds
  after its command ended** — whether the container came up or not. The
  promised wait for “the container is really running” never actually
  happened: the wizard said “done” with Waydroid down, and the next step
  started on top of a session that was still booting. A step now ends when
  the container is running, or fails with “the container did not come up
  in two minutes”.
- “Fix flickering picture” restarted the main session under running extra
  windows — Waydroid responds to that by disabling the overlay, and the ARM
  translator disappears in every window. The step now stops the windows
  first, like the others do.
- Ctrl+Q closed Merci in the middle of root work — past the refusal that
  “Quit” in the tray gives.
- “Open the Android window” in the middle of a stop or a restart started
  the session again; “Remove from the container” and “Delete from the
  library” asked for confirmation and only then refused because the windows
  were busy.
- Library: an entry with a truncated APK copy or unreadable metadata is
  repaired in place instead of being recreated (recreating wiped everything
  in its folder); two identical files added at once no longer get in each
  other's way.

### Changed

- **Root is installed and removed by Merci's own helper**, with no
  `waydroid_script` running as root. Only a Magisk Delta file with a known
  SHA-256 goes into the system (the list of builds lives on the root side);
  it is taken from the cache or from Downloads, the network comes last. The
  file is looked for before the password prompt and before Waydroid is
  stopped, and however the step ends, Waydroid returns to the state it was
  in. Removal downloads nothing, cleans the data of every window and then
  uninstalls the Magisk app. Magisk files in the Android data are now
  executable — with the old permissions Zygisk silently never started.
- **`waydroid_script` no longer takes part in Merci.** Both ARM translators
  (libhoudini and libndk) are installed by the helper from an archive with a
  known checksum; when the container is off, the Android version is read
  straight from the image. No cloning from GitHub, no pip, no third-party
  code as root. A translator goes in whole or not at all (out of space — the
  previous one is untouched), and on a switch the previous one is removed:
  both used to stay, and ARM programs were registered by whichever name came
  first alphabetically.
- One-off steps from the menu (root, translator, acceleration, container
  restart) start right after the question: there used to be a second
  “Install” button after it — which had to be pressed to confirm a removal
  too. While such a step runs, the window is locked the same way as during
  an update.
- When a step fails, the wizard states the reason right in the window — the
  step's own last words instead of “details are in the log”.
- About 35 unused functions and the module of the old clone scheme are
  removed.
- `waydroid-nvidia`: the “does this machine fit” check no longer rejects
  proprietary kernel modules and older drivers — the setup itself checks.

## 0.1.2 — 2026-08-29

A release about many windows: there are now as many as memory allows, and
each one is a separate Android with data of its own. More than half the list
below is repair work for what broke along the way — a container that loses
its picture dies within a minute, and nearly every oddity grew from that one
root.

**A warning.** Multi-window is new and rough in places. A window sometimes
fails to open on the first try; Merci notices and restarts it, which costs a
minute or two. If something goes wrong, do tell:
https://github.com/melineceo/Merci/issues


### Added

- **Every app has windows of its own.** The list of windows used to be shared
  across the whole library: clones made for one game showed up in every other
  app's card — even though each holds its own copy of the data and its own
  installed build. A window now belongs to the entry it was created for and
  appears only in that card. The main window stays shared — Waydroid has just
  the one. Windows created before this change go to the first card that is
  opened: that is what they were made for anyway.

- **Windows are separate Android containers.** The app card gained a "Windows"
  section: every window is a standalone Android with its own data, its own set
  of binder devices, its own network address and its own window on screen.
  "+ Window" creates a new container, "Launch all" opens the app in every one
  of them, "Stop all" closes them. Each container is hardware accelerated: they
  all talk to the same venus server, which accepts several clients.
- **One window size for all.** The size is set once — by the button in the
  window row or by the "render resolution" field, now the same control — and it
  reaches every window: running, stopped, and created later. The value lives in
  `waydroid_base.prop`, which every container reads at startup. A window that
  once got its own size fixes itself on the next start: Merci compares it with
  the shared value and rewrites it when they differ. Changing the size costs
  two password prompts instead of six: the root work is batched into two
  commands, because sudo does not cache the password here.
- **Tuning for many windows:** an eco mode and an FPS cap.

Why containers and not Android profiles: a profile is only visible while the
container is switched to it, and the main user gets exactly two profile
neighbours (clone and work) — a limit baked into the framework. That was the
old ceiling of three windows. Containers are limited only by memory: roughly a
gigabyte for an empty one plus the app itself.

About window size. Waydroid draws a frame of the size given to its container
and does not answer the compositor's request to resize. So in a tiling layout,
where the window size is imposed, the picture gets clipped at the edges — that
is what "it does not fit" looks like.

The cure is not fitting but not imposing a size: a floating window takes its
size from the picture itself, so the whole of it is visible, pixel for pixel.
For Hyprland that is a single rule:

```lua
hl.window_rule({match = {class = "^(waydroid\\..*)$"}, float = true})
```

The size is then changed in Merci: the "fit to window" button or the render
resolution — the container restarts at the new size and the window follows.

What cannot be done: having the picture stretch along with the frame on the
fly, the way LDPlayer does. That needs an intermediate compositor — gamescope —
and it does not start at all on NVIDIA: "vulkan: returned zero modifiers for
DRM format". Verified again today.

Worth knowing: containers do not share installed apps, so the APK is installed
into every new window; root is needed only for actions (create, start, stop,
delete) — the list of windows is read without it.

### Fixed

- **Merci did not get out of the way after a launch in a clone.** It hid
  itself only when launching in the main window; after a clone the library
  stayed on top of the game. It now hides the same way for a window launch
  and for "Launch all" — and only when the launch actually succeeded.

- **The first launch in a new window would not open — the "All files access"
  screen was in the way.** Android raises it over the game on the very first
  start, and everything stops: the game sits behind it drawing no frames, no
  window appears, and a container without a picture suffocates. There is
  nobody to press "Allow" in a separate window — there is no window. This is
  not an ordinary manifest permission (installation grants those) but a
  special one with a screen of its own; Merci now grants it up front. After
  that the game in a fresh window drew its first frame in 7 seconds instead
  of three minutes of waiting for nothing.

- **A window now inherits the main container's settings.** The main one is
  tuned by the Waydroid session and by Merci itself, while extra windows come
  up directly and read only the shared property file — which left them at
  180 Hz against the main one's 60, with multi-window mode not set at all.
  The difference is not cosmetic: a window carrying the main one's settings
  opened a heavy game three times out of three, while its neighbour, left as
  it was, managed zero out of three, wedging its picture every time. The
  properties are now compared with the main container at start and copied
  over when they differ; the window then reboots itself, because they are
  read at startup.
- **"I press launch and nothing happens."** A task record survives the app's
  previous life, and Android delivers the launch into it:

      Warning: Activity not started, its current task has been brought
      to the front

  It sounds fine, but there is no process — the task is empty. Neither a
  repeated launch nor a force-stop removes it. Merci now checks whether a
  process appeared and, when it did not, opens the app in a brand new task.
  A launch also no longer reports success when the app never came up.
- **A window is restarted when Android inside it dies completely too** —
  before, Merci revived only a window without a picture and left a dead
  container to the person.

- **A heavy game would not open in an extra window.** The screen was handed
  to the app as soon as its task appeared — and a task appears long before
  the first frame: for Roblox on a cold start, a minute or more. All that
  time the container is told to show something that is not drawing yet, so
  there is no window, no frame callbacks from the host compositor, and
  Android suffocates: `dequeueBuffer failed, error = -110`, then
  WindowManager blocks, then the watchdog kills system_server in a loop.
  Merci now waits until the app really draws and only then hands over the
  screen; until then the window rests — no picture, but no harm either. The
  signal comes from SurfaceFlinger (the "(BLAST)" layer), because the frame
  counter knows nothing about an app running in a second profile.
- **Starting a window now heals itself.** The first start of an extra window
  sometimes hangs: SurfaceFlinger inside gets stuck initialising graphics
  and the container is left without networking for good. The helper now
  waits for the network itself and, when it does not come, restarts the
  window — inside the same trip through root, so the password is not asked
  twice. The card says plainly that this can take up to three minutes.

- **An app whose window had been closed would never open again.** The
  compositor remembers the task whose window was closed and never gives it a
  window again, however many times you press "Launch":

      single-window: tid 1000009 (com.roblox.client) in ignored_apps -> no window

  Usually the app hears the close request and drops the task itself; the next
  launch then creates a new one and a window appears. But a busy app (a game
  still loading, say) misses the request — and stays without a window for
  good. Merci now reads the compositor's decision straight from the
  container's log, where it is announced within seconds. A refusal means the
  task is marked closed, so Merci stops the app and opens it again with a
  fresh one.
- **A container at rest shows nothing, rather than "all of Android".** The
  previous "show the full interface" was a guaranteed death: this build of
  the compositor blacklists the launcher, so there is nothing to show, no
  window appears — while the container believes it has a screen, and
  suffocates without frame callbacks.
- **Windows are now watched.** Every twenty seconds Merci checks that what a
  container is told to show is really on the screen. If the window was
  closed, hidden or moved away, the container is returned to rest — and
  survives it, instead of dying on the sixtieth second. Such a window used to
  need a restart.
- **The window subtitles lied.** "Running · no app here yet" also showed up
  when the app was installed and Android inside was simply dead: the package
  list came back empty and Merci took it for "not installed". It now says
  what is true: "no network" or "not answering — the window needs a restart".

- **After a container restart, "Launch" would not open the app.** Merci said
  "Waydroid will open the window", no window came, and the picture wedged for
  good. The trap is the same as in the extra windows, only in the main one:
  the screen was handed to the app before it drew its first frame. Usually
  the app is quick enough and nobody notices — but right after a restart it
  starts cold and slow, and all that time the container stands without a
  single window. With no window there are no frame callbacks from the host
  compositor, and Android suffocates. The screen is now handed over after the
  launch, and when no window appears the container gets Android's full
  interface back before anything is restarted.

- **One window out of four would not start — and the binder devices were to
  blame.** Every window has its own set, and the set outlives the container.
  We stop windows hard (they do not stop otherwise), and the devices keep
  references from the Android that was killed. The next start inherits them
  along with the leftovers: binder calls into SurfaceFlinger hang for good,
  system_server blocks inside the DisplayManagerService constructor, and the
  watchdog kills it in a loop. The container "runs" all the while, with
  neither networking nor a picture inside — from the outside all you see is
  "the window got no network address". The set is now recreated before every
  window start; if the container has not let go yet, Merci waits a few
  seconds and, failing that, detaches it lazily.
- **A window left without a screen would die of it.** In single-window mode
  the compositor shows exactly the app named in `waydroid.active_apps`.
  Merci named it before the launch, and until the app started drawing the
  compositor showed nothing at all — it closed the window entirely
  ("single-window: NO TID layer among 4 layers"). With no window the host
  compositor sends no frame callbacks, and Android inside suffocates:
  `dequeueBuffer failed, error = -110`, then WindowManager blocks, and the
  familiar loop follows. By then it is too late to cure: the compositor
  re-reads that property only on a frame. The screen is now handed to the
  app after it starts, and when no window appears the window gets Android's
  full interface back. Closing an app returns it too: the window used to be
  left pointing at an app that was no longer there.
- **The "did a window appear" check looked in the wrong place.** It counted
  any mention of the package among the layers, while the compositor looks
  only at the task layers (`TID:`) — the very ones that were missing. And for
  a stopped window the check silently read the main container's picture,
  because an empty address fell back to the default one.

- **A running window was reported as stopped** — and everything else broke
  from there. The state was read off the network: it has an address and adb
  answers, so it runs. A container that came up without networking counted
  as stopped, so Merci never shut it down before starting the main session.
  And it holds the image mount, so the session's overlay cannot be mounted;
  Waydroid answers that once and for all — it writes
  `mount_overlays = False` — and libhoudini goes with the overlay. In the
  card that surfaces half an hour later and in entirely different words:
  "ARM64 → x86_64 translation needed". The window's state now comes from
  the container itself.
- **A clone would not start after a machine reboot:** "the window got no
  network address". A window's config is a copy of the main container's, and
  the session rewrites that one on every start: it holds the paths of the
  socket, the image and the data. The copy was taken once, when the window
  was created, and after a reboot it led nowhere. The container came up, the
  composer inside hung waiting for what was no longer there, and neither
  SurfaceFlinger nor the network followed. The copy is now taken afresh on
  every start; the window's own data lives elsewhere and is untouched.
- **The window size vanished again after an image upgrade.** It lived only in
  `waydroid_base.prop`, and Waydroid rebuilds that file from scratch. The
  size is now written into `waydroid.cfg` as well — where Waydroid reads it
  from during that very rebuild.
- **Preparing for a container start asked for the password three times in a
  row** — stop the extra windows, restore the overlay, restore the size. A
  missed prompt left the machine half-done: no overlay and no ARM translator
  in every window at once. It is now a single trip through root.

- **A window closed with Win+Q would never open again.** The compositor
  really does close the Waydroid window, and Android never learns of it: the
  app keeps running, but the surface it drew on is gone. After a few such
  closes SurfaceFlinger stops answering, and no new window will ever appear.
  From the outside everything looks healthy — the window and package
  services report "found", `waydroid app launch` returns zero — so Merci
  reported success and went on checking an empty screen. It now asks
  SurfaceFlinger itself, waits for the window to reach the screen and, when
  it does not, brings the picture back by restarting the container: about
  twenty seconds instead of an endless check.
- **An empty list of apps was taken for "nothing is installed",** and Merci
  started installing the APK again — a long install instead of a clear
  refusal. The list comes back empty only when the service did not answer:
  a container always has its system apps.
- **The launch went to the background after a container restart.** A restart
  returns Android to the main profile, while the app lives in its own — and
  it was launched into the profile that was not switched to. It runs, there
  is no window. The profile is now switched back first.
- **"Activity class does not exist" right after a restart:** the package
  service answers before it has finished reading the installed apps. Merci
  now waits for this particular app and retries the launch instead of giving
  up on the first try.
- **adb stopped being allowed into the container after an image upgrade.**
  The `ro.adb.secure=0` line lived in `waydroid_base.prop`, and Waydroid
  rebuilds that file from scratch on every `waydroid upgrade` — from its own
  defaults plus the `[properties]` section of `waydroid.cfg`. The line now
  lives where it survives the rebuild.

- **"Press the checkmark" — with no checkmark there.** The "use the monitor
  size" button only typed the number into the field and advised pressing a
  checkmark. That button appears only when a person edits the text: setting it
  from code does not show it. The advice sent people looking for a button that
  was not there, and the size was never applied. The button now applies at once.
- **The field's title promised stretching to the monitor** — which does not
  happen. It now says what it does: "Size of every window".
- **Merci remembered a size that was never applied:** the choice was saved
  before the work, not after. When the work broke off (a missed password
  prompt, say), Merci would later restore a size no window had.
- **adb stopped being allowed into the main container.** The `ro.adb.secure=0`
  line vanished from the base properties — same file rewrite — and the
  container began asking for a confirmation nobody could give. Merci now puts
  the host key into the trusted list itself and reconnects.
- **Android sometimes gets stuck while booting:** no DHCP address, no package
  service, the watchdog killing system_server in a loop. Restarting the session
  does not help there — Merci restarts the whole container service once.
- **The chosen window size disappeared on its own.** It lives in
  `waydroid_base.prop`, and that file gets rewritten both by Waydroid on image
  upgrades and by third-party scripts — the size vanished from it without a
  word. The wizard's "fit to screen" step then set the monitor size, so windows
  went back to 1920×1080. The choice is now kept in Merci's own settings and
  written back into the base properties at session start whenever it is gone.
- **"The window did not open: it got no network address".** An extra window
  could be started while the main session was down — and the Android image, the
  bridge and the socket are prepared by that session. The container came up into
  nothing: Android started without the image, system_server hung in
  `DisplayManagerService`, the watchdog killed it in a loop, and no address ever
  appeared. Merci now brings the main window up first, and the helper refuses to
  start a window while the image is not mounted.
- **"Waydroid did not launch the APK: Can't find service: package".** Android
  inside raises `sys.boot_completed` while system_server is already dead:
  Android's watchdog kills it when it hangs and starts it again (a hang in
  `DisplayManagerService`). In that window every command answers "Can't find
  service: package", which looks like a Merci failure. The package service
  itself is now checked before launching, not the boot flag; when it does not
  answer, Merci waits and says a full container restart helps.
- **The wizard hung for minutes on "asking the host".** The checks go through
  adb and each waits out its timeout — with a dead system_server that added up
  to minutes of silence. A short "is the system alive" probe now comes first,
  and if it is not, the wizard offers a full restart instead of waiting.
- **The "fit to screen" step undid the chosen window size.** It set the monitor
  size on every wizard run without asking. While a shared size is set, the step
  no longer enters the plan.
- **The card showed the wrong size:** it read a property that could linger from
  an earlier setting. It now asks the container itself.
- **Restarting the container did not work** — neither the plain nor the full
  one. Two reasons. Extra windows held the image mount, so the container never
  came up within the two-minute budget; both restarts now stop those windows
  first and restore the overlay if it had been turned off. And in the full
  restart step `pkill -f "waydroid session start"` killed the script itself:
  the very same string sat at its end, in the start command. The command is now
  assembled from a variable, so the pattern no longer matches it.
- **The container address was resolved wrongly after a restart:** `waydroid
  status` names addresses from stale DHCP leases that are no longer on the
  bridge. Merci now takes a live neighbour whose MAC does not belong to an
  extra window.
- **Extra windows stole Waydroid's ARM translator.** While they run they hold
  the previous image mount, so the overlay cannot be mounted when the main
  session starts. Waydroid answers that once and for all — it writes
  `mount_overlays = False` and never tries again — and libhoudini lives in that
  overlay. Merci now keeps the order itself: it stops the extra windows before
  starting the session and restores the overlay if it was already turned off.
- **The "ARM64 translation" step failed while the container was running.**
  waydroid_script treats any stderr output as a failure, and `waydroid
  container stop` writes a plain "Stopping container" there — which produced
  "returned non-zero exit status 0" for a successful command. The container is
  now stopped before the script is called.
- **The translator check lied because of quoting:** a nested `sh -c` lost the
  path on its way through adb, `ls` listed the root, and the wizard offered to
  install a translation that was already in place.
- **ARM apps stopped launching in every container at once.** Waydroid turns its
  overlay off after a single failed mount (`mount_overlays = False` in
  waydroid.cfg) and never tries again — and libhoudini lives in that overlay.
  From the outside it looks like "the game crashes on start":
  `UnsatisfiedLinkError: dlopen ... is for EM_AARCH64 instead of EM_X86_64`.
  Merci now checks the library itself instead of the property, and offers to
  restore the overlay as a wizard step.
- **`waydroid status` reports someone else's address** when another container
  runs alongside: it picks the first neighbour on the bridge. Merci now finds
  its container by MAC and no longer relies on that answer.
- **Switching a profile hit the running-users limit:** Android answered "Failed
  to switch to user N" and it looked like a plain breakage. Merci now stops the
  spare profiles itself and retries.
- **Windows did not open when the container sat on another profile** — the
  launch did not bring it back.
- **The launch never named the app to Waydroid** (`waydroid.active_apps`),
  without which the container draws no windows for it.
- **Creating a window never worked:** the command used a `--type` flag that
  `pm create-user` does not have.
- **"Launch all" kept quiet about failures** — exceptions were swallowed, so a
  total failure looked like success.
- **Multi-window mode was switched on without restarting the session**, that is
  not switched on at all.
- **Every new window opened the "All files access" screen** on top of the game.

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
