"""Scanner Kiosk service.

Press the ScanSnap's Scan button -> scan -> drop blank pages -> PDF -> upload
to Papra. A small web page (for a phone) shows the scanner status and recent
scans, retries failed uploads, starts scans in other modes, and sets what the
button does.

    python3 kiosk.py            # then open http://<pi-address>:8080

Settings come from the environment (see scanner-kiosk.env.example).
"""
import json
import logging
import mimetypes
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import button
import papra
import scanner

log = logging.getLogger("kiosk")

DATA = Path(os.environ.get("KIOSK_DATA") or Path.home() / "scanner-kiosk-data")
PORT = int(os.environ.get("KIOSK_PORT", "8080"))
WEB = Path(__file__).with_name("web")

KEEP_UPLOADED_DAYS = 7    # uploaded scans stay downloadable this long
KEEP_FAILED_DAYS = 2      # failed scans (nothing to upload) are listed this long
MAX_JOBS = 100            # hard cap on stored scans
LIST_JOBS = 30            # scans shown on the web page
RETRY_DELAYS = [30, 60, 120, 300, 900]   # seconds between automatic upload retries
PAPRA_CHECK_EVERY = 600   # seconds between Papra connection checks

PUBLIC_FIELDS = ("id", "created", "preset", "source", "status", "pages",
                 "blanks", "error", "attempts", "next_try", "uploaded", "name")


# -- job store -----------------------------------------------------------------
class Jobs:
    """Scans on disk: DATA/jobs/<id>/job.json plus scan.pdf until it's pruned.

    status: scanning -> pending -> uploading -> uploaded
            scanning -> scan_failed          (nothing to upload)
            uploading -> pending (will retry) or failed (needs a manual retry)
    """

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        for f in root.glob("*/job.json"):
            try:
                job = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            # Work that was in progress when the service stopped.
            if job["status"] == "scanning":
                job.update(status="scan_failed", error="Interrupted by a restart")
            elif job["status"] == "uploading":
                job.update(status="pending", next_try=0)
            self.jobs[job["id"]] = job
            self._save(job)

    def _save(self, job: dict):
        d = self.root / job["id"]
        d.mkdir(exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, indent=1))
        tmp.replace(d / "job.json")

    def new(self, preset: str, source: str) -> dict:
        now = time.time()
        stamp = datetime.fromtimestamp(now)
        job = dict(
            id=stamp.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4],
            name=stamp.strftime("Scan %Y-%m-%d %H-%M-%S") + ".pdf",
            created=now, preset=preset, source=source, status="scanning",
            pages=0, blanks=0, error=None, attempts=0, next_try=0,
            uploaded=None, document_id=None,
        )
        with self.lock:
            self.jobs[job["id"]] = job
            self._save(job)
        return dict(job)

    def update(self, job_id: str, **changes) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            job.update(changes)
            self._save(job)
            return dict(job)

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def recent(self, n: int) -> list[dict]:
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)
            return [dict(j) for j in jobs[:n]]

    def due(self) -> list[dict]:
        now = time.time()
        with self.lock:
            jobs = [j for j in self.jobs.values()
                    if j["status"] == "pending" and j["next_try"] <= now]
            return [dict(j) for j in sorted(jobs, key=lambda j: j["created"])]

    def pdf(self, job_id: str) -> Path:
        return self.root / job_id / "scan.pdf"

    def prune(self):
        now = time.time()
        with self.lock:
            done = sorted((j for j in self.jobs.values()
                           if j["status"] in ("uploaded", "scan_failed")),
                          key=lambda j: j["created"])
            excess = max(0, len(self.jobs) - MAX_JOBS)
            for j in done:
                age_days = (now - j["created"]) / 86400
                limit = KEEP_UPLOADED_DAYS if j["status"] == "uploaded" else KEEP_FAILED_DAYS
                if age_days > limit or excess > 0:
                    shutil.rmtree(self.root / j["id"], ignore_errors=True)
                    del self.jobs[j["id"]]
                    excess -= 1


# -- the service ---------------------------------------------------------------
class Kiosk:
    def __init__(self):
        self.jobs = Jobs(DATA / "jobs")
        self.settings_file = DATA / "settings.json"
        self.settings = {"button_preset": button.BUTTON_PRESET}
        try:
            self.settings.update(json.loads(self.settings_file.read_text()))
        except (OSError, ValueError):
            pass
        self.activity: str | None = None
        self.scan_lock = threading.Lock()          # one scan at a time
        self.wake_uploader = threading.Event()
        self.check_papra_now = threading.Event()
        self.papra_state = {"ok": None, "message": "Checking…"}
        self.watcher = button.ButtonWatcher(self._button_pressed)

    # scanning
    def _button_pressed(self):
        # Runs on the watcher thread, which has already released the scanner.
        self._scan(self.settings["button_preset"], "button")

    def scan_from_web(self, preset: str) -> bool:
        if self.scan_lock.locked():
            return False

        def run():
            with self.watcher.released():          # waits for a button scan to finish
                self._scan(preset, "web")
        threading.Thread(target=run, name="web-scan", daemon=True).start()
        return True

    def _scan(self, preset: str, source: str):
        if not self.scan_lock.acquire(blocking=False):
            log.info("Already scanning; ignoring the %s request", source)
            return
        job = self.jobs.new(preset, source)
        jid = job["id"]
        self.activity = f"Scanning ({scanner.LABELS[preset]})"
        log.info("Scan %s started from the %s (%s)", jid, source, preset)
        try:
            folder = DATA / "jobs" / jid
            pages_dir = folder / "pages"
            try:
                _, pages = scanner.scan(preset, pages_dir)
            except scanner.ScanError as e:
                self.jobs.update(jid, status="scan_failed", error=_scan_error(str(e)))
                log.warning("Scan %s failed: %s", jid, e)
                return
            blanks = len(list((pages_dir / "blank").glob("*.png")))
            scanner.build_pdf(pages_dir, pages, preset).replace(self.jobs.pdf(jid))
            shutil.rmtree(pages_dir, ignore_errors=True)
            self.jobs.update(jid, status="pending", pages=len(pages), blanks=blanks)
            log.info("Scan %s: %d page(s), %d blank removed", jid, len(pages), blanks)
            self.wake_uploader.set()
        except Exception as e:
            log.exception("Scan %s crashed", jid)
            self.jobs.update(jid, status="scan_failed", error=f"Internal error: {e}")
        finally:
            self.activity = None
            self.scan_lock.release()

    # uploading
    def _uploader(self):
        while True:
            self.wake_uploader.wait(timeout=10)
            self.wake_uploader.clear()
            for job in self.jobs.due():
                self._upload(job)
            self.jobs.prune()

    def _upload(self, job: dict):
        jid = job["id"]
        self.jobs.update(jid, status="uploading")
        try:
            doc_id = papra.upload(self.jobs.pdf(jid), job["name"])
        except Exception as e:
            retry = e.retry if isinstance(e, papra.PapraError) else True
            if not isinstance(e, papra.PapraError):
                log.exception("Upload %s crashed", jid)
            attempts = job["attempts"] + 1
            if retry:
                delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
                self.jobs.update(jid, status="pending", error=str(e),
                                 attempts=attempts, next_try=time.time() + delay)
            else:
                self.jobs.update(jid, status="failed", error=str(e), attempts=attempts)
            self.papra_state = {"ok": False, "message": str(e)}
            log.warning("Upload %s failed (%s): %s", jid,
                        "will retry" if retry else "needs a manual retry", e)
            return
        self.jobs.update(jid, status="uploaded", document_id=doc_id,
                         uploaded=time.time(), error=None, next_try=0)
        if not self.papra_state["ok"]:
            self.check_papra_now.set()
        log.info("Uploaded %s as %s", jid, job["name"])

    def retry(self, job_id: str | None) -> int:
        """Queue one job, or every failed/waiting job, for upload right now."""
        targets = [self.jobs.get(job_id)] if job_id else self.jobs.recent(MAX_JOBS)
        n = 0
        for job in targets:
            if job and job["status"] in ("failed", "pending") and self.jobs.pdf(job["id"]).exists():
                self.jobs.update(job["id"], status="pending", next_try=0)
                n += 1
        self.wake_uploader.set()
        return n

    def _papra_checker(self):
        while True:
            was_ok = self.papra_state["ok"]
            try:
                self.papra_state = {"ok": True, "message": f"Connected ({papra.check()})"}
                if not was_ok:
                    # Papra just became reachable (or the service just
                    # started): give uploads that gave up one more try.
                    self.retry(None)
            except papra.PapraError as e:
                self.papra_state = {"ok": False, "message": str(e)}
            except Exception as e:
                self.papra_state = {"ok": False, "message": f"Check failed: {e}"}
            self.check_papra_now.wait(timeout=PAPRA_CHECK_EVERY)
            self.check_papra_now.clear()

    # settings & status
    def set_button_preset(self, preset: str):
        self.settings["button_preset"] = preset
        self.settings_file.write_text(json.dumps(self.settings))
        log.info("Scan button now does: %s", preset)

    def status(self) -> dict:
        jobs = self.jobs.recent(LIST_JOBS)
        uploading = any(j["status"] == "uploading" for j in jobs)
        return {
            "scanner": self.watcher.state(),
            "activity": self.activity or ("Uploading…" if uploading else None),
            "button_preset": self.settings["button_preset"],
            "presets": [{"id": k, "label": v} for k, v in scanner.LABELS.items()],
            "papra": self.papra_state,
            "jobs": [{k: j.get(k) for k in PUBLIC_FIELDS} | {
                        "has_pdf": self.jobs.pdf(j["id"]).exists()} for j in jobs],
            "now": time.time(),
        }

    def start(self):
        self.watcher.start()
        threading.Thread(target=self._uploader, name="uploader", daemon=True).start()
        threading.Thread(target=self._papra_checker, name="papra-check", daemon=True).start()


def _scan_error(msg: str) -> str:
    """Turn scanimage's wording into something readable on a phone."""
    low = msg.lower()
    if "out of documents" in low or "feeder empty" in low or "no docs" in low:
        return "No paper in the feeder"
    if "every page was blank" in low:
        return msg
    if "jammed" in low:
        return "Paper jam"
    if "cover open" in low:
        return "Scanner cover is open"
    if "busy" in low:
        return "Scanner is busy"
    return msg.splitlines()[-1][:200] if msg else "Scan failed"


# -- web server ----------------------------------------------------------------
def make_handler(kiosk: Kiosk):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ScannerKiosk"

        def log_message(self, fmt, *args):      # keep the journal quiet
            pass

        def _send(self, code, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=HTTPStatus.OK):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(min(n, 10_000)))
            except ValueError:
                return {}

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/status":
                return self._json(kiosk.status())
            if path.startswith("/api/jobs/") and path.endswith("/pdf"):
                jid = path.split("/")[3]
                job = kiosk.jobs.get(jid)
                pdf = kiosk.jobs.pdf(jid) if job else None
                if not pdf or not pdf.exists():
                    return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                return self._send(HTTPStatus.OK, pdf.read_bytes(), "application/pdf", {
                    "Content-Disposition": f'inline; filename="{job["name"]}"'})
            name = "index.html" if path == "/" else path.lstrip("/")
            f = (WEB / name).resolve()
            if WEB.resolve() in f.parents and f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(HTTPStatus.OK, f.read_bytes(), ctype)
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            path, body = self.path.split("?")[0], self._body()
            if path == "/api/scan":
                preset = body.get("preset")
                if preset not in scanner.PRESETS:
                    return self._json({"error": "Unknown scan mode"}, HTTPStatus.BAD_REQUEST)
                if not kiosk.watcher.state()["online"]:
                    return self._json({"error": "Scanner is offline"}, HTTPStatus.CONFLICT)
                if not kiosk.scan_from_web(preset):
                    return self._json({"error": "Already scanning"}, HTTPStatus.CONFLICT)
                return self._json({"ok": True})
            if path == "/api/settings":
                preset = body.get("button_preset")
                if preset not in scanner.PRESETS:
                    return self._json({"error": "Unknown scan mode"}, HTTPStatus.BAD_REQUEST)
                kiosk.set_button_preset(preset)
                return self._json({"ok": True})
            if path == "/api/retry":
                return self._json({"queued": kiosk.retry(body.get("id"))})
            if path == "/api/papra/check":
                kiosk.check_papra_now.set()
                return self._json({"ok": True})
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    scanner.SCAN_ROOT = DATA / "jobs"
    kiosk = Kiosk()
    kiosk.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(kiosk))
    server.daemon_threads = True
    log.info("Web page on http://0.0.0.0:%d  (data in %s)", PORT, DATA)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
