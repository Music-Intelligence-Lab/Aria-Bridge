#!/usr/bin/env python3
"""Aria control page: start, stop and watch the Aria Bridge backend from a browser.

  python3 real-time/control/aria_control.py                  # serve the page; start Aria from there
  python3 real-time/control/aria_control.py --start duet     # also start right away (duet, answer, clip, loop, overdub)
  python3 real-time/control/aria_control.py --keyboard "Yamaha CP50"

Open http://127.0.0.1:8765 (opened automatically unless --no-open). The server re-runs itself
under the repo's venv so it can talk OSC. It listens on 127.0.0.1 only. macOS (and Linux) only.
The log goes to logs/aria_backend.log at the repo root; the previous run is kept as .prev.log.
Clip track names come from AbletonOSC when it is installed (see real-time/README.md).
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent.parent  # real-time/control -> repo root
VENV = BRIDGE / "venv"
VENV_PY = VENV / "bin" / "python"

# Needs python-osc from the bridge's venv.
if VENV_PY.exists() and Path(sys.prefix).resolve() != VENV.resolve():
    os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

from pythonosc.dispatcher import Dispatcher  # noqa: E402
from pythonosc.osc_server import ThreadingOSCUDPServer  # noqa: E402
from pythonosc.udp_client import SimpleUDPClient  # noqa: E402

DEFAULT_LOG = BRIDGE / "logs" / "aria_backend.log"
MODEL = BRIDGE / "models" / "model-gen.safetensors"
SPLIT = re.compile(r"[\r\n]")
CONTROL_PORT = 9000

MODES = {
    "duet": {"label": "Duet", "clips": False,
             "desc": "Aria plays continuously and listens while you play. Record a seed phrase, then play along. Cancel stops it, Play resumes."},
    "answer": {"label": "Answer", "clips": False,
               "desc": "Record a phrase, Aria generates an answer, then press Play to hear it."},
    "clip": {"label": "Clip", "clips": True,
             "desc": "Same as Answer, but each answer lands in an Ableton clip on the track you choose."},
    "loop": {"label": "Loop", "clips": True,
             "desc": "Record once. Each clip seeds the next, down the column, until you Cancel."},
    "overdub": {"label": "Overdub", "clips": True,
                "desc": "Each take is layered over Aria's clip, and Aria writes one new clip per take. Cancel starts a new chain."},
}
OSC_ALLOWED = {"/aria/record", "/aria/play", "/aria/cancel", "/aria/temp", "/aria/top_p",
               "/aria/min_p", "/aria/tokens", "/aria/grade", "/aria/commit"}


def midi_ports():
    """Listed in a fresh process, so devices plugged in after startup show up."""
    code = "import json,mido;print(json.dumps({'in':mido.get_input_names(),'out':mido.get_output_names()}))"
    try:
        r = subprocess.run([str(VENV_PY), "-c", code], capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"in": [], "out": []}


def live_tracks():
    """Tracks in the open Live set, via AbletonOSC. None if Live does not answer."""
    replies = {}

    def on_reply(addr, *a):
        replies[addr] = a

    disp = Dispatcher()
    disp.set_default_handler(on_reply)
    try:
        srv = ThreadingOSCUDPServer(("127.0.0.1", 11001), disp)
    except OSError:
        return None  # a clip-mode backend owns the reply port
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    osc = SimpleUDPClient("127.0.0.1", 11000)

    def q(addr, *args):
        replies.pop(addr, None)
        osc.send_message(addr, list(args))
        end = time.time() + 0.8
        while time.time() < end:
            if addr in replies:
                return replies[addr]
            time.sleep(0.01)
        return None

    try:
        names = q("/live/song/get/track_names")
        if not names:
            return None
        out = []
        for i, name in enumerate(names):
            midi = q("/live/track/get/has_midi_input", i)
            r = q("/live/track/get/input_routing_type", i)
            mon = q("/live/track/get/current_monitoring_state", i)
            arm = q("/live/track/get/arm", i)
            out.append({"index": i, "name": name, "midi": bool(midi and len(midi) > 1 and midi[1]),
                        "input": str(r[1]) if r and len(r) > 1 else "",
                        "monitor": mon[1] if mon and len(mon) > 1 else None,
                        "armed": bool(arm and len(arm) > 1 and arm[1])})
        return out
    finally:
        srv.shutdown()
        srv.server_close()


def port_owner():
    """(pid, command) of a bridge backend holding the control port, if any."""
    r = subprocess.run(["lsof", "-nP", f"-tiUDP:{CONTROL_PORT}"], capture_output=True, text=True)
    for pid in r.stdout.split():
        c = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
        if "ableton_bridge.py" in c:
            return int(pid), c.strip()
    return None


def exit_reason(code):
    if code in (0, -signal.SIGINT, -signal.SIGTERM):
        return "Stopped.", False
    if code == -signal.SIGKILL:
        return "macOS killed the backend (usually out of memory). See the log, then start again.", True
    if code == -signal.SIGABRT:
        return "The backend aborted. The last log lines say why.", True
    if code == -signal.SIGSEGV:
        return "The backend crashed. The last log lines say why.", True
    return f"The backend exited with code {code}. The last log lines say why.", True


class Supervisor:
    def __init__(self, log_path: Path):
        self.log = log_path
        self.proc = None
        self.mode = None
        self.started = None
        self.exit = None
        self.stopping = False
        self.live_check = None
        self.tracks = []
        self.lock = threading.Lock()

    def _poll(self):
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            text, bad = exit_reason(0 if self.stopping else code)
            self.exit = {"code": code, "text": text, "bad": bad, "at": time.time(), "mode": self.mode}
            self.proc, self.mode, self.stopping = None, None, False

    def status(self):
        with self.lock:
            self._poll()
            pid, mode = (self.proc.pid, self.mode) if self.proc else (None, None)
            if pid is None:
                owner = port_owner()
                if owner:
                    pid, cmd = owner
                    mode = "duet" if " stream" in cmd else "external"
            return {"running": pid is not None, "pid": pid, "mode": mode, "started": self.started,
                    "exit": self.exit, "stopping": self.stopping, "live_check": self.live_check}

    def options(self):
        ports = midi_ports()
        if not self.status()["running"]:
            tracks = live_tracks()
            if tracks is not None:
                self.tracks = tracks
        midi = [t for t in self.tracks if t["midi"]]
        hears = next((t for t in midi if "aria_out" in t["input"].lower()), None)
        keys = [p for p in ports["in"] if "IAC" not in p and "Launchpad" not in p]
        return {"modes": [{"id": k, **v} for k, v in MODES.items()], "inputs": ports["in"], "outputs": ports["out"],
                "tracks": midi, "defaults": {"keyboard": keys[0] if keys else "ARIA_IN", "output": "IAC Driver ARIA_OUT",
                                             "track": hears["index"] if hears else None}}

    def start(self, cfg: dict):
        mode = cfg.get("mode")
        if mode not in MODES:
            return f"Unknown mode: {mode}"
        ports = midi_ports()
        keyboard, output = cfg.get("keyboard"), cfg.get("output")
        if keyboard not in ports["in"]:
            return f"Keyboard port not found: {keyboard}. Is it plugged in?"
        if output not in ports["out"]:
            return f"Output port not found: {output}"
        if MODES[mode]["clips"] and not isinstance(cfg.get("track"), int):
            return "Choose a clip track first. Is Ableton open with AbletonOSC?"
        with self.lock:
            self._poll()
            if self.proc or port_owner():
                return "Aria is already running. Stop it first."
            out_name = output.replace("IAC Driver ", "")
            tracks = live_tracks()
            if tracks is None:
                self.live_check = {"ok": False, "text": "AbletonOSC did not answer. Is Live open?"}
            else:
                self.tracks = tracks
                hear = [t for t in tracks if out_name.lower() in t["input"].lower()
                        and (t["monitor"] == 0 or (t["monitor"] == 1 and t["armed"]))]
                self.live_check = ({"ok": True, "text": f"{hear[0]['name']} hears Aria"} if hear else
                                   {"ok": False, "text": f"No track is hearing {out_name}. Aria will be silent."})
            cmd = [str(VENV_PY), "-X", "faulthandler", "ableton_bridge.py"]
            common = ["--checkpoint", str(MODEL), "--device", "mlx", "--in", keyboard, "--out", out_name]
            if mode == "duet":
                cmd += ["stream", *common]
                for key, flag in (("window", "--stream-window"), ("lookahead", "--stream-lookahead"),
                                  ("entropy_floor", "--stream-entropy-floor")):
                    if isinstance(cfg.get(key), (int, float)):
                        cmd += [flag, str(cfg[key])]
            else:
                cmd += ["m4l", *common, "--feedback", "--data-dir", str(BRIDGE / "data")]
                if mode != "answer":
                    cmd += [{"clip": "--clip", "loop": "--loop", "overdub": "--overdub"}[mode], "--track", str(cfg["track"])]
            self.log.parent.mkdir(parents=True, exist_ok=True)
            if self.log.exists():
                self.log.replace(self.log.with_suffix(".prev.log"))
            out = self.log.open("w")
            self.proc = subprocess.Popen(cmd, cwd=BRIDGE / "real-time", stdout=out, stderr=subprocess.STDOUT,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1"}, start_new_session=True)
            self.mode, self.started, self.exit, self.stopping = mode, time.time(), None, False
            print(f"Started {mode} (pid {self.proc.pid})", flush=True)
        return None

    def stop(self):
        """Interrupt first so the backend saves its take, then escalate."""
        with self.lock:
            target = self.proc
            pid = target.pid if target else None
            if pid is None:
                owner = port_owner()
                pid = owner[0] if owner else None
            if pid is None:
                return "Aria is not running."
            self.stopping = True

        def finish():
            for sig, wait in ((signal.SIGINT, 8), (signal.SIGTERM, 4), (signal.SIGKILL, 2)):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    break
                end = time.time() + wait
                while time.time() < end:
                    if target is not None and target.poll() is not None:
                        return
                    if target is None and port_owner() is None:
                        self.stopping = False
                        return
                    time.sleep(0.1)

        threading.Thread(target=finish, daemon=True).start()
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        sup = self.server.sup
        if path in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/events":
            self.stream()
        elif path == "/api/options":
            self.send_json(sup.options())
        elif path == "/snapshot":  # whole log once, for screenshots
            log = sup.log
            text = log.read_text("utf-8", "replace") if log.exists() else ""
            self.send_json({"t": time.time(), "live": False, "lines": [p for p in SPLIT.split(text) if p.strip()],
                            "log": str(log), "exists": log.exists(), **sup.status()})
        else:
            self.send_error(404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if origin and origin not in allowed:
            return self.send_json({"error": "Requests from other sites are not accepted."}, 403)
        try:
            cfg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send_json({"error": "Bad request."}, 400)
        sup = self.server.sup
        if self.path == "/api/start":
            err = sup.start(cfg)
            self.send_json({"error": err} if err else {"ok": True}, 400 if err else 200)
        elif self.path == "/api/stop":
            err = sup.stop()
            self.send_json({"error": err} if err else {"ok": True}, 400 if err else 200)
        elif self.path == "/api/osc":
            addr, value = cfg.get("addr"), cfg.get("value", 1)
            if addr not in OSC_ALLOWED or not isinstance(value, (int, float)):
                return self.send_json({"error": "Control not allowed."}, 400)
            SimpleUDPClient("127.0.0.1", CONTROL_PORT).send_message(addr, value)
            self.send_json({"ok": True})
        else:
            self.send_error(404)

    def emit(self, event, obj):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(obj)}\n\n".encode())
        self.wfile.flush()

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        sup = self.server.sup
        log = sup.log
        pos, buf, inode, last_beat = 0, "", None, 0.0
        live = False  # the first read replays history
        try:
            while True:
                if log.exists():
                    st = log.stat()
                    if inode is not None and (st.st_ino != inode or st.st_size < pos):
                        pos, buf, live = 0, "", False  # new run
                        self.emit("reset", {})
                    inode = st.st_ino
                    if st.st_size > pos:
                        with log.open("rb") as f:
                            f.seek(pos)
                            data = f.read()
                            pos = f.tell()
                        buf += data.decode("utf-8", "replace")
                        parts = SPLIT.split(buf)
                        buf = parts.pop()
                        lines = [p for p in parts if p.strip()]
                        if lines:
                            self.emit("lines", {"t": time.time(), "live": live, "lines": lines})
                    live = True
                now = time.time()
                if now - last_beat > 1:
                    self.emit("beat", {**sup.status(), "log": str(log), "exists": log.exists(), "now": now})
                    last_beat = now
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--start", choices=list(MODES), default=None, help="start Aria in this mode right away")
    ap.add_argument("--keyboard", default=None, help="with --start: MIDI input port (default: first keyboard found)")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    sup = Supervisor(args.log)

    def quit_all(*_):
        if sup.proc and sup.proc.poll() is None:
            sup.stop()
            try:
                sup.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, quit_all)
    signal.signal(signal.SIGTERM, quit_all)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.sup = sup
    url = f"http://127.0.0.1:{args.port}"
    print(f"Aria control page at {url}", flush=True)
    if args.start:
        o = sup.options()
        keyboard = next((p for p in o["inputs"] if args.keyboard and args.keyboard.lower() in p.lower()),
                        o["defaults"]["keyboard"])
        err = sup.start({"mode": args.start, "keyboard": keyboard, "output": o["defaults"]["output"],
                         "track": o["defaults"]["track"]})
        if err:
            print(err, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
