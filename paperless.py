"""Minimal Paperless-ngx REST client: check the API token and upload a document.

Configuration comes from the environment, loaded by systemd from
/etc/scanner-kiosk.env (see scanner-kiosk.env.example):
  PAPERLESS_URL        e.g. https://paperless.example.com
  PAPERLESS_TOKEN      API token of a user allowed to add documents
"""
import os
from pathlib import Path

import requests

TIMEOUT = (10, 120)   # connect, read (seconds)


class PaperlessError(Exception):
    """Upload failed. `retry` says whether trying again later may help."""

    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


def _config() -> tuple[str, str]:
    url = os.environ.get("PAPERLESS_URL", "").rstrip("/")
    token = os.environ.get("PAPERLESS_TOKEN", "")
    missing = [n for n, v in (("PAPERLESS_URL", url), ("PAPERLESS_TOKEN", token)) if not v]
    if missing:
        raise PaperlessError(f"Not set up: fill in {', '.join(missing)} in "
                             "/etc/scanner-kiosk.env, then restart the service", retry=False)
    return url, token


def _request(method: str, path: str, **kw) -> requests.Response:
    url, token = _config()
    try:
        r = requests.request(method, url + path, timeout=TIMEOUT,
                             headers={"Authorization": f"Token {token}",
                                      "Accept": "application/json"}, **kw)
    except requests.RequestException as e:
        raise PaperlessError(f"Can't reach Paperless: {e.__class__.__name__}", retry=True) from None
    if r.ok:
        return r
    # 5xx, timeouts and rate limits are worth retrying; other 4xx are not.
    retry = r.status_code >= 500 or r.status_code in (408, 429)
    known = {401: "Paperless rejected the API token",
             403: "The token's user lacks permission to add documents",
             404: "Paperless returned 404: check PAPERLESS_URL"}
    if r.status_code in known:
        raise PaperlessError(known[r.status_code], retry=retry)
    raise PaperlessError(f"Paperless returned HTTP {r.status_code}: {r.text.strip()[:200]}",
                         retry=retry)


def check() -> str:
    """Verify the URL and token. Returns a short description of the user."""
    r = _request("GET", "/api/ui_settings/")
    try:
        info = r.json()
    except ValueError:
        raise PaperlessError("Unexpected reply from Paperless: check PAPERLESS_URL",
                             retry=False) from None
    user = (info.get("user") or {}).get("username", "?")
    perms = info.get("permissions")
    if perms is not None and not any(p.endswith("add_document") for p in perms):
        raise PaperlessError(f"Paperless user '{user}' can't add documents", retry=False)
    return f"user '{user}'"


def upload(pdf: Path, filename: str) -> str:
    """Upload a PDF; returns the Paperless consume task ID.

    Paperless processes uploads in the background, so the document itself
    appears a little later (after OCR).
    """
    title = filename.removesuffix(".pdf")
    with pdf.open("rb") as f:
        r = _request("POST", "/api/documents/post_document/",
                     data={"title": title},
                     files={"document": (filename, f, "application/pdf")})
    try:
        task = r.json()
    except ValueError:
        task = r.text.strip()
    return str(task.get("task_id", "") if isinstance(task, dict) else task)


if __name__ == "__main__":
    # `python paperless.py` checks the settings in the environment (used by install.sh).
    import sys
    try:
        print(f"  OK: Paperless accepted {check()}")
    except PaperlessError as e:
        sys.exit(f"  {e}")
