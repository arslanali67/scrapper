"""Local web page for both scrapers: fill in a form, see the cost estimate, run, download the CSV.

  .venv/Scripts/python -X utf8 app.py        then open http://127.0.0.1:8765

It runs google_scraper.py / linkedin_scraper.py exactly as the command line does (same flags, budget, suppress.txt,
run log), one run at a time, and keeps the CSVs in outputs/. Only this computer can reach it (127.0.0.1).
"""
import csv
import json
import re
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
OUT = ROOT / "outputs"
PORT = 8765
JOB = {"proc": None, "log": [], "out": None, "dry": False}
LOCK = threading.Lock()


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "run"


def build_command(f):
    """Form fields -> (argv, output CSV path). Raises ValueError with a message for the page."""
    kind = f.get("scraper")
    limit, max_cost = int(f.get("limit") or 100), float(f.get("max_cost") or 1)
    if not 1 <= limit <= 100000 or not 0 < max_cost <= 1000:
        raise ValueError("limit must be 1-100000 and max cost $0-1000")
    on = lambda k: str(f.get(k, "")).lower() in ("1", "true", "on")
    text = lambda k: str(f.get(k) or "").strip()
    # "--flag=value" keeps a value that starts with "-" from being read as another flag
    opt = lambda flag, k: [f"{flag}={text(k)}"] if text(k) else []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if kind == "google":
        if not text("query") or not text("location"):
            raise ValueError("give a business type and a location")
        script, name = "google_scraper.py", f"google-{_slug(text('query'))}-{_slug(text('location'))}"
        args = opt("--query", "query") + opt("--location", "location")
        args += [f for k, f in (("paid_emails", "--paid-emails"), ("verify", "--verify"),
                                ("require_website", "--require-website"), ("no_enrich", "--no-enrich")) if on(k)]
    elif kind == "people":
        if not text("role") and not text("titles"):
            raise ValueError("give a role (e.g. HR) or job titles")
        script, name = "linkedin_scraper.py", f"people-{_slug(text('role') or text('titles'))}-{_slug(text('location'))}"
        args = opt("--role", "role") + opt("--titles", "titles") + opt("--industry", "industry") \
            + opt("--location", "location") + opt("--size", "size")
        args += [f for k, f in (("verify", "--verify"), ("phone", "--phone"), ("no_enrich", "--no-enrich")) if on(k)]
    elif kind == "company":
        urls = re.findall(r"\S*linkedin\.com/company/\S+", text("urls"))
        if not (urls or text("keyword") or text("industry") or text("location")):
            raise ValueError("give a keyword, industry or location, or paste LinkedIn company URLs")
        script, name = "linkedin_scraper.py", f"companies-{_slug(text('keyword') or 'list')}-{_slug(text('location'))}"
        args = ["--mode=company"]
        if urls:
            OUT.mkdir(exist_ok=True)
            url_file = OUT / f"{name}-{stamp}-urls.txt"
            url_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
            args.append(f"--input={url_file}")
        else:
            args += opt("--keyword", "keyword") + opt("--industry", "industry") + opt("--location", "location") \
                + opt("--size", "size")
        args += [f for k, f in (("paid_emails", "--paid-emails"), ("verify", "--verify"), ("no_enrich", "--no-enrich"))
                 if on(k)]
    else:
        raise ValueError("unknown scraper")
    out = OUT / f"{name}-{stamp}.csv"
    argv = [sys.executable, "-u", "-X", "utf8", str(ROOT / script), *args,
            f"--limit={limit}", f"--max-cost={max_cost}", f"--out={out}"]
    return argv + (["--dry-run"] if on("dry") else []), out


def start(fields):
    with LOCK:
        if JOB["proc"] and JOB["proc"].poll() is None:
            raise ValueError("a run is already going: wait for it or press Stop")
        argv, out = build_command(fields)
        OUT.mkdir(exist_ok=True)
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        JOB.update(proc=proc, log=[], out=out, dry="--dry-run" in argv)

    def read():
        for line in proc.stdout:
            with LOCK:
                JOB["log"].append(line.rstrip())
    threading.Thread(target=read, daemon=True).start()


def status():
    with LOCK:
        p, out = JOB["proc"], JOB["out"]
        running = bool(p and p.poll() is None)
        has_csv = bool(out and out.exists() and out.stat().st_size and not JOB["dry"])
        return {"running": running, "exit_code": None if running or not p else p.returncode,
                "log": JOB["log"][-300:], "csv": out.name if has_csv else None, "dry": JOB["dry"]}


def preview(name, n=25):
    with open(OUT / name, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    return {"columns": rows[0] if rows else [], "rows": rows[1:n + 1], "total": max(0, len(rows) - 1)}


def safe_csv(name):
    """Only plain file names of CSVs inside outputs/ can be read or downloaded."""
    p = OUT / Path(name or "").name
    return p if p.suffix == ".csv" and p.is_file() else None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", extra=()):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(data)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, PAGE, "text/html")
        if u.path == "/status":
            return self._send(200, status())
        if u.path in ("/download", "/preview"):
            p = safe_csv((q.get("f") or [""])[0])
            if not p:
                return self._send(404, {"error": "no such CSV"})
            if u.path == "/preview":
                return self._send(200, preview(p.name))
            return self._send(200, p.read_bytes(), "text/csv", [("Content-Disposition", f'attachment; filename="{p.name}"')])
        if u.path == "/files":
            files = sorted(OUT.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True) if OUT.exists() else []
            return self._send(200, [{"name": p.name, "kb": round(p.stat().st_size / 1024, 1)} for p in files[:30]])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self._send(403, {"error": "other sites may not start runs"})  # a web page elsewhere can't spend
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/run":
            try:
                start(body)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True})
        if self.path == "/stop":
            with LOCK:
                if JOB["proc"] and JOB["proc"].poll() is None:
                    JOB["proc"].terminate()  # rows already written stay in the CSV
                    JOB["log"].append("Stopped. Rows written so far are kept in the CSV.")
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass  # keep the console quiet; runs have their own log


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lead Scraper</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--mute:#667085;--line:#e3e6eb;--acc:#2f5bd3;--ok:#1a7f4b;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}
main{max-width:980px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}p.sub{color:var(--mute);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}
.tabs{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}.tabs button{border:1px solid var(--line);background:#fff;
padding:8px 14px;border-radius:8px;cursor:pointer;font:inherit}.tabs button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
label{display:block;font-size:13px;color:var(--mute)}label>input,label>textarea{margin-top:4px}input[type=text],input[type=number],textarea{width:100%;
padding:8px 10px;border:1px solid var(--line);border-radius:7px;font:inherit;background:#fff}textarea{min-height:70px}
.checks{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}.checks label{display:flex;gap:6px;align-items:center;color:var(--ink);font-size:14px;margin:0}
.actions{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}.actions button,a.btn{padding:9px 16px;border-radius:8px;border:1px solid var(--acc);
font:inherit;cursor:pointer;background:#fff;color:var(--acc);text-decoration:none}.actions .go,a.btn{background:var(--acc);color:#fff}
.actions .stop{border-color:var(--bad);color:var(--bad)}button:disabled{opacity:.5;cursor:default}
pre{background:#11151c;color:#d6dbe4;padding:12px;border-radius:8px;max-height:260px;overflow:auto;font-size:12.5px;white-space:pre-wrap;margin:0}
.state{font-weight:600;margin-bottom:8px}.err{color:var(--bad);margin-top:10px}.hint{font-size:12.5px;color:var(--mute);margin-top:6px}
.tablewrap{overflow:auto;max-height:420px;border:1px solid var(--line);border-radius:8px;margin-top:12px}
table{border-collapse:collapse;font-size:12.5px;min-width:100%}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap;max-width:260px;overflow:hidden;text-overflow:ellipsis}
th{background:#f0f2f5;position:sticky;top:0}ul.files{margin:0;padding-left:18px}ul.files a{color:var(--acc)}
</style></head><body><main>
<h1>Lead Scraper</h1><p class="sub">Fill in what you want, check the estimated cost, then start. The CSV appears here when the run ends.</p>
<div class="card">
 <div class="tabs"><button data-k="google" class="on">Google businesses</button><button data-k="people">LinkedIn people</button><button data-k="company">LinkedIn companies</button></div>
 <form id="f" onsubmit="return false">
  <div class="grid" id="fields"></div>
  <div class="checks" id="checks"></div>
  <div class="grid" style="margin-top:12px">
   <label>How many leads (limit)<input type="number" name="limit" value="50" min="1"></label>
   <label>Max cost (USD, hard stop)<input type="number" name="max_cost" value="1" min="0.01" step="0.01"></label>
  </div>
  <div class="actions"><button id="est">Estimate cost (free)</button><button id="go" class="go">Start scraping</button><button id="stop" class="stop" disabled>Stop</button></div>
  <div class="err" id="err"></div>
 </form>
</div>
<div class="card" id="runcard" hidden><div class="state" id="state"></div><pre id="log"></pre>
 <div class="actions" id="dl"></div><div class="tablewrap" id="prev" hidden></div></div>
<div class="card"><div class="state">Earlier results</div><ul class="files" id="files"></ul></div>
</main><script>
const F = {
 google: {fields: [["query","Business type","dentists"],["location","Location: state, \"City, ST\" or ZIP","Austin, TX"]],
          checks: [["paid_emails","Paid email search when the website shows none (~$0.025 each)"],["verify","Verify emails"],["require_website","Only businesses with a website"]]},
 people: {fields: [["role","Role: HR, sales, founder, IT... or any title","HR"],["titles","Or exact job titles, comma-separated",""],
                   ["industry","Industry: tech, healthcare, finance...","tech"],["location","Location: state or \"City, ST\"","Texas"],["size","Company size, e.g. 11-50 or 1000+",""]],
          checks: [["verify","Verify emails"],["phone","Also find phone numbers"]]},
 company: {fields: [["keyword","Keyword in the company profile","SaaS"],["industry","Industry",""],["location","Location","Texas"],["size","Company size, e.g. 11-50",""],["urls","Or paste LinkedIn company URLs, one per line",""]],
           checks: [["paid_emails","Paid email search when the website shows none"],["verify","Verify emails"]]}};
let kind = "google", timer = null;
const $ = s => document.querySelector(s);
function render(){
 $("#fields").innerHTML = F[kind].fields.map(([n,l,v]) => `<label${n==="urls"?' style="grid-column:1/-1"':''}>${l}${n==="urls"?`<textarea name="${n}"></textarea>`:`<input type="text" name="${n}" value="${v}">`}</label>`).join("");
 $("#checks").innerHTML = F[kind].checks.map(([n,l]) => `<label><input type="checkbox" name="${n}"> ${l}</label>`).join("");
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {document.querySelectorAll(".tabs button").forEach(x=>x.classList.remove("on")); b.classList.add("on"); kind=b.dataset.k; render();});
async function run(dry){
 $("#err").textContent = "";
 const data = {scraper: kind, dry};
 new FormData($("#f")).forEach((v,k) => data[k] = v);
 $("#f").querySelectorAll("input[type=checkbox]").forEach(c => data[c.name] = c.checked);
 const r = await fetch("/run", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(data)});
 const j = await r.json(); if (!r.ok) { $("#err").textContent = j.error; return; }
 $("#runcard").hidden = false; $("#dl").innerHTML = ""; $("#prev").hidden = true; poll();
}
$("#est").onclick = () => run(true); $("#go").onclick = () => run(false);
$("#stop").onclick = () => fetch("/stop", {method:"POST"});
function esc(s){return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
async function poll(){
 clearTimeout(timer);
 const s = await (await fetch("/status")).json();
 $("#log").textContent = s.log.join("\n"); $("#log").scrollTop = 1e9;
 $("#go").disabled = $("#est").disabled = s.running; $("#stop").disabled = !s.running;
 $("#state").textContent = s.running ? (s.dry ? "Estimating..." : "Scraping... (progress every 10 leads)") : s.exit_code === null ? "" : s.exit_code === 0 ? (s.dry ? "Estimate ready" : "Done") : "Stopped";
 if (s.running) { timer = setTimeout(poll, 1500); return; }
 if (s.csv) {
  $("#dl").innerHTML = `<a class="btn" href="/download?f=${encodeURIComponent(s.csv)}">Download CSV</a>`;
  const p = await (await fetch("/preview?f=" + encodeURIComponent(s.csv))).json();
  $("#prev").hidden = false;
  $("#prev").innerHTML = `<table><tr>${p.columns.map(c=>`<th>${esc(c)}</th>`).join("")}</tr>${p.rows.map(r=>`<tr>${r.map(c=>`<td title="${esc(c)}">${esc(c)}</td>`).join("")}</tr>`).join("")}</table>`;
 }
 files();
}
async function files(){
 const fs = await (await fetch("/files")).json();
 $("#files").innerHTML = fs.length ? fs.map(f => `<li><a href="/download?f=${encodeURIComponent(f.name)}">${esc(f.name)}</a> (${f.kb} KB)</li>`).join("") : "<li>None yet</li>";
}
render(); files(); poll();
</script></body></html>"""


if __name__ == "__main__":
    print(f"Lead Scraper page: http://127.0.0.1:{PORT}  (Ctrl+C to quit)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
