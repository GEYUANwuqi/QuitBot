"""Run QQ and QuitBot together in a persistent, private Linux X11 session."""

import argparse
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def enable_accessibility():
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for name in ("IsEnabled", "ScreenReaderEnabled"):
        bus.call_sync(
            "org.a11y.Bus",
            "/org/a11y/bus",
            "org.freedesktop.DBus.Properties",
            "Set",
            GLib.Variant("(ssv)", ("org.a11y.Status", name, GLib.Variant("b", True))),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )


def session(args):
    children = []

    def start(command):
        process = subprocess.Popen(command)
        children.append(process)
        return process

    def stop(signum, frame):
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    with tempfile.TemporaryDirectory(prefix="quitbot-x11-") as directory:
        os.chmod(directory, 0o700)
        os.environ.update(
            DISPLAY=args.display,
            XAUTHORITY=str(Path(directory) / "Xauthority"),
            QT_QPA_PLATFORM="xcb",
        )
        Path(os.environ["XAUTHORITY"]).touch(mode=0o600)
        subprocess.run(
            ["xauth", "add", args.display, ".", secrets.token_hex(16)], check=True
        )
        try:
            display = start(
                [
                    "Xvfb",
                    args.display,
                    "-screen",
                    "0",
                    args.screen,
                    "-nolisten",
                    "tcp",
                    "-auth",
                    os.environ["XAUTHORITY"],
                ]
            )
            deadline = time.monotonic() + 10
            while True:
                if display.poll() is not None:
                    raise RuntimeError("Xvfb exited; choose an unused display")
                ready = subprocess.run(
                    ["xdpyinfo"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                if ready.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Xvfb did not become ready")
                time.sleep(0.1)
            start([args.window_manager])
            enable_accessibility()
            start([args.qq, "--force-renderer-accessibility"])
            start([sys.executable, "-m", "quitbot_backend"])
            print(
                f"QuitBot X11 session ready: DISPLAY={args.display}; XAUTHORITY={os.environ['XAUTHORITY']}",
                flush=True,
            )
            while all(process.poll() is None for process in children):
                time.sleep(0.5)
            raise RuntimeError(
                "A graphical session process exited; restart the session"
            )
        finally:
            for process in reversed(children):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(children):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display", default=":99")
    parser.add_argument("--screen", default="1440x1000x24")
    parser.add_argument("--qq", default="/opt/QQ/qq")
    parser.add_argument("--window-manager", default="openbox")
    parser.add_argument("--inside-session", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("The virtual desktop launcher requires Linux")
    if os.getuid() == 0:
        parser.error("Run QQ and QuitBot as an ordinary dedicated Linux user")
    for command in (
        "dbus-run-session",
        "Xvfb",
        "xauth",
        "xdpyinfo",
        "xdotool",
        "xclip",
        args.window_manager,
        args.qq,
    ):
        if shutil.which(command) is None:
            parser.error(f"Missing dependency: {command}")
    if not args.inside_session:
        os.execvp(
            "dbus-run-session",
            [
                "dbus-run-session",
                "--",
                sys.executable,
                "-m",
                "quitbot_backend.desktop",
                *sys.argv[1:],
                "--inside-session",
            ],
        )
    session(args)


if __name__ == "__main__":
    main()
