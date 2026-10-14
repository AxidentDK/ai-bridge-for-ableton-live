"""Set up sound search: fetch the listener, its models and its dependencies.

The bridge ships without ears. `find_sound` reads a SQLite index at
~/.ai-bridge/sound_index.db and politely answers "not available" until something
writes it. The thing that writes it is the listener — a separate program in its own
repository (ai-bridge-listener) with real dependencies (onnxruntime, soundfile) and
~55 MB of model files. Bundling all that would triple every download for a feature
not everyone wants, so it stays optional and THIS module is the one-button path to it:
Gemini Studio calls these functions from its "Sound search" dialog, and everything
lands under ~/.ai-bridge/, never inside the app.

Layout after install():

    ~/.ai-bridge/
        models/                      what the listener loads (hardcoded there)
        listener/
            src/ai-bridge-listener/  the listener's source, from its release
            site/                    pip --target: numpy, soundfile, onnxruntime
            run_listener.py          wrapper; see _WRAPPER for why it exists

Everything is idempotent: what already exists is kept, never overwritten — a user's
models folder may hold more than our zip (trained classifiers, experiments), and a
re-run after a half-failed download must finish the job, not redo it.

Also a CLI, because a dialog cannot be driven by a test:
    python sound_search_setup.py --status | --install | --scan [--folder DIR] [--limit N]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASES_LATEST = "https://api.github.com/repos/AxidentDK/ai-bridge-listener/releases/latest"
SRC_DIRNAME = "ai-bridge-listener"          # the source zip's root folder
_UA = {"User-Agent": "ai-bridge-sound-search-setup"}

# The Windows setup program bundles an EMBEDDABLE Python: no pip, PYTHONPATH ignored,
# sys.path fixed by its ._pth file. A wrapper script mutates sys.path at runtime —
# the one mechanism an embeddable interpreter does honour — so the listener and its
# site dir need no environment at all, under any of the three install routes.
_WRAPPER = '''\
import runpy, sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here / "site"))
sys.path.insert(0, str(here / "src" / "ai-bridge-listener"))
runpy.run_module("listener", run_name="__main__", alter_sys=True)
'''


def home() -> Path:
    """~/.ai-bridge — overridable for tests, which must not touch a real home."""
    override = os.environ.get("AI_BRIDGE_HOME")
    return Path(override) if override else Path.home() / ".ai-bridge"


def _listener_dir() -> Path:
    return home() / "listener"


def db_path() -> Path:
    return home() / "sound_index.db"


def _marker() -> Path:
    return _listener_dir() / "installed_release.txt"


def installed_release() -> str | None:
    """Which listener release the last install() fetched, e.g. 'v1.0.0'."""
    try:
        return _marker().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def check_update() -> dict:
    """Compare the installed listener release with the newest published one."""
    with urllib.request.urlopen(urllib.request.Request(RELEASES_LATEST, headers=_UA)) as r:
        latest = json.load(r).get("tag_name")
    current = installed_release()
    return {"installed": current, "latest": latest,
            "update_available": bool(latest) and latest != current}


def status() -> dict:
    """What exists, what is missing, and how many sounds are indexed."""
    missing = []
    if not (_listener_dir() / "src" / SRC_DIRNAME / "listener" / "__main__.py").exists():
        missing.append("listener")
    if not (_listener_dir() / "site").is_dir():
        missing.append("dependencies")
    if not (home() / "models" / "discogs-effnet-bsdynamic-1.onnx").exists():
        missing.append("models")
    if not (_listener_dir() / "run_listener.py").exists():
        missing.append("wrapper")

    indexed = None
    if db_path().exists():
        try:
            import sqlite3
            with sqlite3.connect(db_path()) as con:
                indexed = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        except Exception:
            indexed = None
    return {"installed": not missing, "missing": missing, "indexed": indexed,
            "release": installed_release(), "db": str(db_path())}


def _fetch(url: str, dest: Path, progress, label: str) -> None:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 18)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                progress(f"{label}: {done // (1 << 20)} of {total // (1 << 20)} MB")


def _extract_missing(zip_path: Path, into: Path, progress, label: str) -> int:
    """Unpack only what is not already there. Existing files are the user's."""
    written = 0
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            target = into / member.filename
            if member.is_dir() or target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(target, "wb") as out:
                out.write(src.read())
            written += 1
    progress(f"{label}: {written} new file(s)")
    return written


def _ensure_pip(progress) -> None:
    probe = subprocess.run([sys.executable, "-m", "pip", "--version"],
                           capture_output=True, text=True)
    if probe.returncode == 0:
        return
    # The bundled embeddable Python has no pip. get-pip.py installs one into its
    # Lib\site-packages, which IS on the bundle's search path.
    progress("fetching pip (this Python came without one)")
    with tempfile.TemporaryDirectory() as td:
        getpip = Path(td) / "get-pip.py"
        _fetch("https://bootstrap.pypa.io/get-pip.py", getpip, progress, "get-pip")
        r = subprocess.run([sys.executable, str(getpip), "--quiet",
                            "--no-warn-script-location"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("could not set up pip:\n" + (r.stderr or r.stdout).strip()[-800:])


def install(progress=print, update: bool = False) -> dict:
    """Fetch and lay out everything sound search needs. Safe to run again.

    update=True replaces the listener's SOURCE with the newest release (and re-runs
    the dependency install, in case it needs more). Models and the index are never
    replaced — new model files arrive under new names, existing ones are the user's.
    """
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    if update:
        import shutil
        old = _listener_dir() / "src" / SRC_DIRNAME
        if old.exists():
            progress(f"removing the old listener ({installed_release() or 'unknown version'})")
            shutil.rmtree(old)

    progress("looking up the latest listener release")
    with urllib.request.urlopen(urllib.request.Request(RELEASES_LATEST, headers=_UA)) as r:
        release = json.load(r)
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    src_url = next((u for n, u in assets.items()
                    if n.startswith("ai-bridge-listener-source-") and n.endswith(".zip")), None)
    models_url = next((u for n, u in assets.items()
                       if n.startswith("ai-bridge-models-") and n.endswith(".zip")), None)
    if not src_url or not models_url:
        raise RuntimeError(f"release {release.get('tag_name')} is missing the source or "
                           "models zip — please report this")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        progress("downloading the listener")
        _fetch(src_url, tmp / "src.zip", progress, "listener")
        src_root = _listener_dir() / "src"
        src_root.mkdir(parents=True, exist_ok=True)
        _extract_missing(tmp / "src.zip", src_root, progress, "listener")

        if (root / "models" / "discogs-effnet-bsdynamic-1.onnx").exists():
            progress("models: already present, kept as they are")
        else:
            progress("downloading the models (~55 MB)")
            _fetch(models_url, tmp / "models.zip", progress, "models")
            _extract_missing(tmp / "models.zip", root, progress, "models")

    progress("checking for pip")
    _ensure_pip(progress)
    progress("installing numpy, soundfile and onnxruntime (a few minutes)")
    site = _listener_dir() / "site"
    site.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", "--no-warn-script-location",
                        "--target", str(site), "numpy", "soundfile", "onnxruntime"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("pip could not install the listener's dependencies:\n"
                           + (r.stderr or r.stdout).strip()[-800:])

    wrapper = _listener_dir() / "run_listener.py"
    wrapper.write_text(_WRAPPER, encoding="utf-8")

    progress("checking the result")
    r = subprocess.run([sys.executable, str(wrapper), "--help"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("the listener does not start:\n"
                           + (r.stderr or r.stdout).strip()[-800:])
    _marker().write_text(str(release.get("tag_name") or ""), encoding="utf-8")
    progress(f"sound search is installed ({release.get('tag_name')})")
    return status()


def scan_command(folders: list[str] | None = None, limit: int | None = None) -> list[str]:
    """The subprocess command for a scan — the caller streams its output.

    No folders means: scan what Ableton already knows (Live's own file database).
    """
    cmd = [sys.executable, str(_listener_dir() / "run_listener.py"),
           "--out", str(db_path())]
    for f in folders or []:
        cmd += ["--folder", f]
    if limit:
        cmd += ["--limit", str(limit)]
    return cmd


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--status", action="store_true")
    p.add_argument("--install", action="store_true")
    p.add_argument("--update", action="store_true")
    p.add_argument("--check-update", action="store_true")
    p.add_argument("--scan", action="store_true")
    p.add_argument("--folder", action="append", dest="folders")
    p.add_argument("--limit", type=int)
    args = p.parse_args(argv)

    if args.check_update:
        print(json.dumps(check_update(), indent=2))
        return 0
    if args.install or args.update:
        install(update=args.update)
    if args.scan:
        cmd = scan_command(args.folders, args.limit)
        return subprocess.run(cmd).returncode
    print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
