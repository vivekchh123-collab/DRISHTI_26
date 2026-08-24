# Desktop application

DRISHTI ships as a Windows installer. **The end user needs neither Python nor
Node** — the interpreter, numpy, scikit-learn, the trained models and the whole
front end are inside the package.

```
dist/installer/DRISHTI-Setup-1.0.0.exe        136 MB   NSIS installer
dist/installer/win-unpacked/DRISHTI.exe       515 MB   unpacked, runs directly
```

---

## Build it

```bash
python scripts/build_desktop.py
```

Flags: `--server-only` (freeze the backend and stop), `--dir` (unpacked app, no
installer), `--skip-server` (reuse an existing frozen backend),
`--skip-smoke` (skip the runtime check).

First run downloads about 150 MB of Electron toolchain. Later runs are minutes.

### Prerequisites

```bash
winget install OpenJS.NodeJS.LTS
pip install pyinstaller
```

---

## How it fits together

```
  PyInstaller                       Electron
  ───────────                       ────────
  backend/  ──▶ drishti-server.exe ──▶ spawned as a child process
  frontend/     (256 MB, self-           │
  models/        contained)              ├─ reads DRISHTI_PORT= from its stdout
                                         ├─ polls /healthz until it answers
                                         ├─ opens a window on that URL
                                         └─ kills the whole process tree on exit
```

Electron is doing very little here, and that is the right amount. The
application *is* the Python server; the shell's entire job is to start it, wait
for it, show it, and make sure it dies afterwards.

Three details are handled deliberately, because each is how this class of app
normally breaks:

- **The port is read from the server's stdout, not assumed.** A hard-coded 8000
  means the app refuses to start whenever anything else holds that port, which
  on a working laptop is often.
- **The window waits for `/healthz`, not for a timer.** Loading too early shows
  the user a connection-refused page with no hint that it would have worked in
  two seconds.
- **The child is killed on every exit path**, via `taskkill /T` on Windows.
  `SIGTERM` does not reliably reach a Windows child, and an orphaned server
  holding a port after the window closes is the classic Electron sidecar bug.

---

## Two build problems, and what was done about them

Both are environment issues rather than code issues, and both are worked around
in the build script so they do not recur.

### 1. OneDrive locks files during PyInstaller's clean

This project sits under OneDrive on the machine it was built on, and OneDrive
holds handles open on files while syncing them. PyInstaller's `--clean` then
dies with `PermissionError` trying to delete its own work directory.

`--clean` was dropped and the work directory is removed best-effort instead.
**Building outside a synced folder avoids this entirely** and is worth doing if
you build often.

### 2. electron-builder needs admin to unpack its signing helper

electron-builder downloads a `winCodeSign` bundle to sign executables and to
rewrite their icon and version resources. That archive contains **macOS
symlinks**, and Windows refuses to create symlinks without Developer Mode or an
elevated shell:

```
ERROR: Cannot create symbolic link : A required privilege is not held by the client.
        .../winCodeSign/<id>/darwin/10.12/lib/libcrypto.dylib
```

Pre-extracting the archive does not help — a fresh directory is generated on
each attempt.

Requiring an elevated shell to produce an unsigned internal build is a poor
trade, so `signAndEditExecutable` is **off**, and the one part that was actually
wanted — branding — is done directly in `desktop/afterPack.js` with `rcedit`.
The result is identical: the executable carries the DRISHTI icon and version
strings, and no elevation is needed.

If `rcedit` cannot be found the build continues with the stock Electron icon
rather than failing. A slightly wrong icon is a much better outcome than no
installer.

---

## Code signing

The installer is **unsigned**. Windows SmartScreen will show "Windows protected
your PC" on first run; *More info → Run anyway* proceeds.

For anything distributed beyond a demonstration, buy an OV or EV code-signing
certificate and set:

```bash
set CSC_LINK=path\to\certificate.pfx
set CSC_KEY_PASSWORD=...
```

then rebuild. electron-builder signs automatically when those are present — and
in that case turn `signAndEditExecutable` back on, since you will need the
signing helper anyway and will presumably be building in a shell that can
create symlinks.

---

## Other platforms

`package.json` already declares macOS (`dmg`) and Linux (`AppImage`) targets.
Both need to be built **on that platform** — PyInstaller cannot cross-compile,
so the backend must be frozen natively:

```bash
python scripts/build_desktop.py --dir      # on the target OS
```

---

## Testing what you built

`build_desktop.py` runs a smoke test automatically: it starts the frozen server
and checks `/healthz`, `/api/districts`, `/api/methods`, `/api/ml` and `/`.

That check earns its ninety seconds. PyInstaller failures are almost always
missing hidden imports, and they surface at **runtime**, not at build time — a
successful build and a binary that crashes on launch look identical until
somebody runs the installer.

Verified on this build:

```
/healthz     -> {'status': 'ok', 'districts': 22}
/api/districts -> 200 (7,423 bytes)
/api/methods   -> 200 (8,245 bytes)
/api/ml        -> 200 (2,910 bytes)   ← sklearn + joblib + trained models bundled
/              -> 200 (4,202 bytes)
```

And the packaged application, launched from `win-unpacked`, picked a free port
(57035), served the front end, and reported both trained models — with no Python
on the path.
