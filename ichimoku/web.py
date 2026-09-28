"""Local website: `python main.py serve` -> http://127.0.0.1:8000

A small dashboard over the files the CLI already writes (paper/, results/),
plus buttons that run the CLI commands in the background. Standard library
only; binds to localhost by default so only you can open it.
"""
import html
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from . import config as C

PY = sys.executable
MAIN = os.path.join(C.ROOT, "main.py")
BACKTEST_REPORT = os.path.join(C.RESULTS_DIR, "backtest", "report.html")
STATE = os.path.join(C.PAPER_DIR, "state.json")

# (command, required): an optional step that fails is logged and the job carries on
OPT = False
JOBS = {
    "auto": ("Updating everything", [
        (["git", "pull", "--ff-only", "--autostash"], OPT),                       # paper account kept by the GitHub Action
        ([PY, MAIN, "update-data"], OPT),                          # download the latest prices (no scan output)
        ([PY, MAIN, "--offline", "backtest"], OPT),                # backtest up to the latest bar
    ]),
    "backtest": ("Run full backtest", [([PY, MAIN, "backtest"], True)]),
}
STEP_NAMES = {"git": "Pulling the latest paper account from GitHub", "update-data": "Downloading the latest prices",
              "backtest": "Running the backtest"}
AUTO_EVERY_MIN = 60          # while the site is open, refresh everything this often


class Job:
    lock = threading.Lock()
    name, log, running, ok, started, finished, next_url, step = None, "", False, None, None, None, "/", ""

    @classmethod
    def start(cls, key, next_url="/"):
        with cls.lock:
            if cls.running:
                return False
            cls.name, cls.log, cls.running, cls.ok, cls.started, cls.next_url = key, "", True, None, time.time(), next_url
        threading.Thread(target=cls._run, args=(JOBS[key][1],), daemon=True).start()
        return True

    @classmethod
    def _run(cls, cmds):
        ok = True
        for n, (cmd, required) in enumerate(cmds, 1):
            word = "git" if cmd[0] == "git" else next((w for w in ("backtest", "update-data") if w in cmd), "")
            cls.step = f"Step {n} of {len(cmds)}: {STEP_NAMES.get(word, ' '.join(cmd[2:]))}…"
            cls.log += "$ " + " ".join(os.path.basename(c) if c in (PY, MAIN) else c for c in cmd) + "\n"
            try:
                p = subprocess.Popen(cmd, cwd=C.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     env={**os.environ, "PYTHONUNBUFFERED": "1"})
                for line in p.stdout:
                    cls.log += line
                code = p.wait()
            except OSError as e:
                cls.log += f"{e}\n"
                code = -1
            if code != 0:
                cls.log += f"\n[exit code {code}]" + ("" if required else " - skipped, continuing") + "\n"
                if required:
                    ok = False
                    break
        cls.ok, cls.running, cls.finished, cls.step = ok, False, time.time(), ""


# ---------------------------------------------------------------------- data
def _mtime(p):
    return os.path.getmtime(p) if os.path.exists(p) else 0


def _ago(ts):
    if not ts:
        return "never"
    s = time.time() - ts
    return "just now" if s < 60 else f"{int(s // 60)} min ago" if s < 3600 else f"{int(s // 3600)} h ago" if s < 86400 else time.strftime("%d %b %Y %H:%M", time.localtime(ts))


def _read_csv(p):
    try:
        return pd.read_csv(p) if os.path.exists(p) and os.path.getsize(p) > 1 else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def paper_summary():
    if not os.path.exists(STATE):
        return None
    st = json.load(open(STATE))
    cap = st["config"]["cfg"]["capital"]
    eq = st.get("equity") or {}
    last_eq = list(eq.values())[-1] if eq else cap
    vals = list(eq.values())
    peak = max(vals) if vals else cap
    closed = st.get("trades", [])
    wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
    return dict(start=st["start_date"], last=st.get("last_date"), capital=cap, equity=last_eq, cash=st["cash"],
                ret=(last_eq / cap - 1) * 100, dd=(last_eq / peak - 1) * 100 if peak else 0,
                n_pos=len(st["positions"]), max_pos=st["config"]["cfg"]["max_positions"],
                n_closed=len(closed), wins=wins, updated=_mtime(STATE))


# ---------------------------------------------------------------------- html
CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--ring:rgba(11,11,11,.10);--accent:#2a78d6;--good:#006300;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--accent:#3987e5;--good:#0ca30c;--bad:#e66767}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--ring);background:var(--surface);position:sticky;top:0;z-index:5}
header b{font-size:15px}nav a{color:var(--ink2);text-decoration:none;margin-right:14px}nav a.on,nav a:hover{color:var(--accent)}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}h2{font-size:16px;margin:28px 0 10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.tile,.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:12px 14px}
.k{color:var(--ink2);font-size:12px}.v{font-size:22px;font-weight:620}.d{color:var(--muted);font-size:12px}
.pos{color:var(--good)}.neg{color:var(--bad)}
.btns{display:flex;flex-wrap:wrap;gap:8px}form{margin:0}
button,.btn{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:8px 12px;cursor:pointer;text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:disabled{opacity:.5;cursor:default}
.tablewrap{overflow:auto;border:1px solid var(--ring);border-radius:12px;background:var(--surface);max-height:560px}
table{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--grid)}th{position:sticky;top:0;background:var(--surface);color:var(--ink2)}
th:first-child,td:first-child{text-align:left}
pre{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:12px;overflow:auto;max-height:65vh;font-size:12px}
.note{color:var(--muted);font-size:12.5px}
.banner{background:color-mix(in srgb,var(--accent) 12%,var(--surface));border:1px solid color-mix(in srgb,var(--accent) 35%,transparent);border-radius:12px;padding:10px 14px;margin-bottom:14px}.empty{color:var(--muted);padding:14px}
iframe{border:0;width:100%;height:calc(100vh - 52px);display:block}
"""


def page(title, body, active="", full=False):
    links = [("/", "Dashboard"), ("/backtest", "Backtest"), ("/job", "Activity")]
    nav = "".join(f'<a href="{u}" class="{"on" if u == active else ""}">{t}</a>' for u, t in links)
    inner = body if full else f"<main>{body}</main>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body><header><b>Ichimoku Nifty 200</b><nav>{nav}</nav></header>{inner}</body></html>"""


def job_button(key, label=None, primary=False, next_url="/job"):
    dis = " disabled" if Job.running else ""
    return (f'<form method="post" action="/run/{key}?next={next_url}"><button class="{"primary" if primary else ""}"{dis}>'
            f'{html.escape(label or JOBS[key][0])}</button></form>')


rs = lambda v: "" if pd.isna(v) else f"₹{v:,.0f}"
pc = lambda v: "" if pd.isna(v) else f'<span class="{"pos" if v > 0 else "neg" if v < 0 else ""}">{v:+.2f}%</span>'


def status_banner():
    if Job.running:
        mins = int((time.time() - Job.started) // 60)
        first = "" if os.path.exists(C.CACHE_FILE) else " The first run downloads 12 years of prices for 200 stocks (5–10 min)."
        return ('<meta http-equiv="refresh" content="4"><div class="banner">⟳ <b>Updating to the latest data</b> — '
                f'{html.escape(Job.step)} ({mins} min so far).{first} This page reloads by itself. '
                '<a href="/job">Show log</a></div>')
    if Job.finished:
        warn = " Some steps had problems — <a href='/job'>see log</a>." if "[exit code" in Job.log else ""
        nxt = time.strftime("%H:%M", time.localtime(Job.finished + AUTO_EVERY_MIN * 60))
        return f'<p class="note">✓ Updated {_ago(Job.finished)}; next automatic update at {nxt}.{warn}</p>'
    return ""


def dashboard():
    s = paper_summary()
    parts = [status_banner()]
    if s:
        tiles = [
            ("Paper equity", rs(s["equity"]), f"started {rs(s['capital'])} on {s['start']}"),
            ("Return", pc(s["ret"]), f"drawdown from peak {s['dd']:.2f}%"),
            ("Cash", rs(s["cash"]), ""),
            ("Open positions", f"{s['n_pos']} / {s['max_pos']}", ""),
            ("Closed trades", str(s["n_closed"]), f"{s['wins']} winners" if s["n_closed"] else ""),
            ("Last market day", s["last"] or "—", f"state saved {_ago(s['updated'])}"),
        ]
        parts.append('<div class="tiles">' + "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="d">{d}</div></div>' for k, v, d in tiles) + "</div>")
        if not s["last"]:
            parts.append('<p class="note">No market day processed yet: the account starts trading on the first session on/after its start date.</p>')
    else:
        parts.append('<div class="card">No paper account yet. It appears here after the daily GitHub Action has run.</div>')
    parts.append('<p class="note">The strategy runs in the background: the daily GitHub Action trades the paper account and this site '
                 'updates by itself. It shows performance only &mdash; no signals, scans, positions or buy/sell levels.</p>')
    return page("Dashboard", "".join(parts), "/")


def job_page():
    title = JOBS[Job.name][0] if Job.name else "No activity yet"
    status = "running…" if Job.running else ("✓ finished" if Job.ok else "✗ failed" if Job.ok is False else "")
    refresh = '<meta http-equiv="refresh" content="2">' if Job.running else ""
    go = ""
    if Job.ok and not Job.running and Job.next_url not in ("/job", ""):
        go = f'<script>setTimeout(()=>location.href={json.dumps(Job.next_url)},800)</script>'
    body = (f"{refresh}<h2>{html.escape(title)} <span class='note'>{status}</span></h2>"
            f"<pre id='log'>{html.escape(Job.log[-60000:]) or 'Nothing has run yet.'}</pre>"
            "<script>const l=document.getElementById('log');l.scrollTop=l.scrollHeight</script>" + go +
            "<h2>Run</h2><div class='btns'>" + job_button("auto", "⟳ Update everything now", primary=True) + "".join(job_button(k) for k in JOBS if k != "auto") + "</div>"
            "<p class='note'>First backtest downloads ~12 years of prices (5–10 min); after that it uses the cache in data/.</p>")
    return page("Activity", body, "/job")


def report_page(path, title, active, job_key, missing_msg):
    if Job.running and not os.path.exists(path):
        return page(title, "<main>" + status_banner() + "</main>", active, full=True)
    body = (f"<iframe src='{active}/report.html'></iframe>" if os.path.exists(path) else
            f"<main><h2>{title}</h2><div class='card'>{missing_msg}</div><br>{job_button(job_key, primary=True, next_url=active)}</main>")
    return page(title, body, active, full=True)


# ---------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        if not os.path.exists(path):
            return self._send("Not found", "text/plain", 404)
        with open(path, "rb") as f:
            self._send(f.read())

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path.rstrip("/") or "/"
        if p == "/":
            return self._send(dashboard())
        if p == "/job":
            return self._send(job_page())
        if p == "/backtest":
            return self._send(report_page(BACKTEST_REPORT, "Backtest", "/backtest", "backtest",
                                          "No backtest yet. It downloads ~12 years of Nifty 200 prices the first time (5–10 minutes)."))
        if p == "/backtest/report.html":
            return self._file(BACKTEST_REPORT)
        self._send("Not found", "text/plain", 404)

    def do_POST(self):
        u = urlparse(self.path)
        # only accept buttons from this site (blocks other web pages from triggering jobs)
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        host = self.headers.get("Host", "")
        if origin and urlparse(origin).netloc != host:
            return self._send("Forbidden", "text/plain", 403)
        key = u.path.rsplit("/", 1)[-1]
        if not u.path.startswith("/run/") or key not in JOBS:
            return self._send("Not found", "text/plain", 404)
        nxt = parse_qs(u.query).get("next", ["/job"])[0]
        Job.start(key, nxt if nxt.startswith("/") else "/job")
        self._redirect("/job")

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def log_message(self, *a):
        pass


def serve(host="127.0.0.1", port=8000):
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        raise SystemExit(f"Port {port} is busy (already running?). Open http://127.0.0.1:{port} or use --port 8001.")
    def auto_loop():
        while True:
            Job.start("auto")
            time.sleep(AUTO_EVERY_MIN * 60)
    threading.Thread(target=auto_loop, daemon=True).start()
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print(f"Ichimoku dashboard running at {url}  (Ctrl+C to stop)")
    if host not in ("127.0.0.1", "localhost"):
        print("Warning: listening on all interfaces - anyone on your network can open it.")
    try:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
