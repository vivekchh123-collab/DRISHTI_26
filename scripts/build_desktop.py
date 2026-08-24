"""Build the DRISHTI desktop application.

    python scripts/build_desktop.py                # backend + installer
    python scripts/build_desktop.py --server-only  # just the frozen backend
    python scripts/build_desktop.py --dir          # unpacked app, no installer

Two stages. PyInstaller freezes the Python backend — interpreter, numpy,
scikit-learn, FastAPI, the trained models and the whole front end — into a
self-contained ``drishti-server.exe``. Electron then wraps that in a native
window and electron-builder produces an NSIS installer.

Electron is doing very little here, and that is the correct amount: the
application is a Python server, so the shell's whole job is to start it, wait
for it, show it, and kill it afterwards. Bundling a browser engine to render
locally-served HTML is the price of a native window and a Start-menu entry.

The end user needs neither Python nor Node.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
DESKTOP = os.path.join(ROOT, "desktop")
DIST = os.path.join(ROOT, "dist")

# Modules PyInstaller's static analysis cannot see, because they are imported
# by name at runtime rather than with an import statement.
HIDDEN = [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "sklearn.ensemble._forest", "sklearn.tree._partitioner",
    "sklearn.utils._typedefs", "sklearn.utils._heap", "sklearn.utils._sorting",
    "sklearn.utils._vector_sentinel", "sklearn.neighbors._partition_nodes",
    "joblib",
]


def run(cmd, cwd=None, env=None) -> None:
    printable = " ".join(cmd if isinstance(cmd, list) else [cmd])
    print("\n$ %s" % printable[:200])
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                       env=env or os.environ.copy())
    if r.returncode != 0:
        raise SystemExit("failed (exit %d): %s" % (r.returncode, printable[:120]))


def node_env() -> dict:
    """npm and electron-builder are not on PATH in a fresh shell after install."""
    env = os.environ.copy()
    for p in (r"C:\Program Files\nodejs",
              os.path.expandvars(r"%APPDATA%\npm")):
        if os.path.isdir(p) and p not in env.get("PATH", ""):
            env["PATH"] = env["PATH"] + os.pathsep + p
    return env


def build_server(clean: bool = True) -> str:
    """Freeze the backend into dist/drishti-server/.

    Note on build location: this project lives under OneDrive on the machine it
    was developed on, and OneDrive holds handles open on files while it syncs
    them. PyInstaller's --clean then fails with a permission error trying to
    remove its own work directory. Cleaning is therefore best-effort, and the
    build directory is excluded from sync in .gitignore. Building outside a
    synced folder avoids the problem entirely.
    """
    out = os.path.join(DIST, "drishti-server")
    if clean and os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    work = os.path.join(DIST, "_work")
    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)

    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "drishti-server",
        "--distpath", DIST,
        "--workpath", os.path.join(DIST, "_work"),
        "--specpath", os.path.join(DIST, "_spec"),
        # A directory build rather than one file: a --onefile binary unpacks
        # ~200 MB to a temp directory on every launch, which adds seconds to
        # startup and trips antivirus heuristics.
        "--console",
        "--add-data", "%s%s%s" % (os.path.join(ROOT, "frontend"), sep, "frontend"),
        "--add-data", "%s%s%s" % (os.path.join(BACKEND, "models"), sep, "models"),
        "--collect-submodules", "sklearn",
        "--collect-data", "sklearn",
    ]
    # Baked data. PyInstaller collects Python modules, not the .npz and .json
    # files sitting beside them, and every one of these loaders fails *softly* —
    # terrain falls back to synthetic, history to nothing, the national screen to
    # "boundaries not baked". An installer that quietly ships a demo-grade
    # version of the app is worse than one that fails loudly, so these are
    # bundled explicitly and the smoke test checks for them.
    #
    # Destinations mirror the source tree because every loader resolves its path
    # relative to its own module: app/core/terrain.py looks for ../data/dem.
    for sub in ("dem", "ghsl", "tracks", "boundaries", "climatology", "boards"):
        src = os.path.join(BACKEND, "app", "data", sub)
        if os.path.isdir(src):
            cmd += ["--add-data", "%s%s%s" % (src, sep,
                                              os.path.join("app", "data", sub))]
        else:
            print("  WARNING: %s not baked - the installed app will fall back "
                  "for this layer" % sub)
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    cmd.append(os.path.join(BACKEND, "server_entry.py"))

    run(cmd, cwd=BACKEND)

    exe = os.path.join(out, "drishti-server.exe" if os.name == "nt"
                       else "drishti-server")
    if not os.path.exists(exe):
        raise SystemExit("PyInstaller reported success but produced no binary")

    # Confirm the baked data actually landed. Every one of these loaders fails
    # softly, so a missing directory produces a working binary that quietly does
    # less - synthetic terrain, no history, no cloud baseline. Checking the
    # files is cheaper and more direct than inferring it from a response later.
    internal = os.path.join(out, "_internal")
    for sub in ("dem", "ghsl", "tracks", "boundaries", "climatology", "boards"):
        here = os.path.join(internal, "app", "data", sub)
        if not (os.path.isdir(here) and os.listdir(here)):
            raise SystemExit(
                "frozen build is missing app/data/%s. The binary would run and "
                "silently serve a degraded application." % sub)
    print("  baked data present: dem, ghsl, tracks, boundaries, climatology, boards")
    size = sum(os.path.getsize(os.path.join(dp, f))
               for dp, _, fs in os.walk(out) for f in fs) / 1e6
    print("\n  backend frozen: %s  (%.0f MB)" % (exe, size))
    return exe


def smoke_test(exe: str, timeout: float = 90.0) -> None:
    """Start the frozen server and confirm it actually serves.

    Worth the ninety seconds: PyInstaller failures are almost always missing
    hidden imports, and they surface at *runtime*, not at build time. A build
    that succeeds and a binary that crashes on launch look identical until
    someone runs the installer.
    """
    import json
    import urllib.request

    print("\n  smoke-testing the frozen backend ...")
    env = os.environ.copy()
    env["DRISHTI_PORT"] = "8399"
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    try:
        deadline = time.time() + timeout
        ok = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:8399/healthz", timeout=2) as r:
                    body = json.loads(r.read())
                    print("    /healthz -> %s" % body)
                    ok = True
                    break
            except Exception:
                if proc.poll() is not None:
                    out = proc.stdout.read() if proc.stdout else ""
                    raise SystemExit("frozen server exited early:\n%s" % out[-3000:])
                time.sleep(1.5)
        if not ok:
            raise SystemExit("frozen server never answered /healthz")

        for path in ("/api/districts", "/api/methods", "/api/ml", "/"):
            with urllib.request.urlopen("http://127.0.0.1:8399" + path,
                                        timeout=30) as r:
                print("    %-18s -> %d (%d bytes)"
                      % (path, r.status, len(r.read())))

        # Every baked-data loader fails softly, so a binary that is missing its
        # elevation still starts, still serves, and still answers every check
        # above — while quietly running on synthetic terrain. These assertions
        # are the difference between shipping the application and shipping a
        # demo of it.
        checks = [
            ("/api/districts/BR-DAR/redzones", "real elevation",
             lambda d: "Copernicus" in d["provenance"]["dem_source"]),
            ("/api/history", "disaster register",
             lambda d: len(d.get("events", [])) > 30),
            # Deliberately NOT /api/national/districts: that endpoint kicks off
            # a live 532-coordinate weather sweep and takes minutes on a cold
            # process, which is a network test wearing a smoke test's clothes.
            # The boundaries are already verified by file above.
        ]
        for path, what, ok_fn in checks:
            with urllib.request.urlopen("http://127.0.0.1:8399" + path,
                                        timeout=180) as r:
                payload = json.loads(r.read())
            if not ok_fn(payload):
                raise SystemExit(
                    "frozen build is missing its %s. The binary runs, but it "
                    "would ship a degraded application. Check the --add-data "
                    "entries for app/data." % what)
            print("    %-18s -> %s present" % (path.split("/")[-1], what))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    print("  smoke test passed")


def find_npm() -> str:
    """Absolute path to npm.

    Windows resolves a child process's executable against the *parent's* PATH,
    not against the PATH handed to it in ``env``. Adding the Node directory to
    the child environment therefore does nothing on its own, and a freshly
    installed Node is not on the parent's PATH until the shell restarts. So the
    binary is located explicitly.
    """
    name = "npm.cmd" if os.name == "nt" else "npm"
    found = shutil.which(name)
    if found:
        return found
    for base in (r"C:\Program Files\nodejs", r"C:\Program Files (x86)\nodejs",
                 os.path.expandvars(r"%APPDATA%\npm"),
                 os.path.expandvars(r"%LOCALAPPDATA%\Programs\nodejs")):
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            return candidate
    raise SystemExit(
        "npm was not found. Install Node.js LTS and reopen the shell:\n"
        "    winget install OpenJS.NodeJS.LTS")


def build_installer(dir_only: bool = False) -> None:
    env = node_env()
    npm = find_npm()
    print("\n  using npm at %s" % npm)
    if not os.path.isdir(os.path.join(DESKTOP, "node_modules")):
        print("  installing Electron toolchain (first run, downloads ~150 MB) ...")
        run([npm, "install"], cwd=DESKTOP, env=env)
    run([npm, "run", "dir" if dir_only else "dist"], cwd=DESKTOP, env=env)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server-only", action="store_true",
                    help="freeze the backend and stop")
    ap.add_argument("--dir", action="store_true",
                    help="unpacked application, skip the installer")
    ap.add_argument("--skip-smoke", action="store_true")
    ap.add_argument("--skip-server", action="store_true",
                    help="reuse the existing frozen backend")
    args = ap.parse_args()

    print("=" * 70)
    print("DRISHTI desktop build")
    print("=" * 70)

    exe = os.path.join(DIST, "drishti-server",
                       "drishti-server.exe" if os.name == "nt" else "drishti-server")
    if args.skip_server and os.path.exists(exe):
        print("\n  reusing existing frozen backend: %s" % exe)
    else:
        exe = build_server()
    if not args.skip_smoke:
        smoke_test(exe)
    if args.server_only:
        print("\nbackend only, as requested.")
        return 0

    build_installer(dir_only=args.dir)

    out = os.path.join(DIST, "installer")
    print("\n" + "=" * 70)
    if os.path.isdir(out):
        for f in sorted(os.listdir(out)):
            full = os.path.join(out, f)
            if os.path.isfile(full):
                print("  %-46s %7.0f MB" % (f, os.path.getsize(full) / 1e6))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
