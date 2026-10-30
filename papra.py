"""Minimal Papra REST client: check the API key and upload a document.

Configuration comes from the environment, loaded by systemd from
/etc/scanner-kiosk.env (see scanner-kiosk.env.example):
  PAPRA_URL            e.g. https://papra.example.com
  PAPRA_ORG_ID         organization ID (from the Papra URL /organizations/<id>)
  PAPRA_API_KEY        API key with the documents:create permission
"""
import os
from pathlib import Path

import requests

TIMEOUT = (10, 120)   # connect, read (seconds)


class PapraError(Exception):
    """Upload failed. `retry` says whether trying again later may help."""

    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


def _config() -> tuple[str, str, str]:
    url = os.environ.get("PAPRA_URL", "").rstrip("/")
    org = os.environ.get("PAPRA_ORG_ID", "")
    key = os.environ.get("PAPRA_API_KEY", "")
    missing = [n for n, v in (("PAPRA_URL", url), ("PAPRA_ORG_ID", org),
                              ("PAPRA_API_KEY", key)) if not v]
    if missing:
        raise PapraError(f"Not set up: fill in {', '.join(missing)} in "
                         "/etc/scanner-kiosk.env, then restart the service", retry=False)
    return url, org, key


def _request(method: str, path: str, **kw) -> requests.Response:
    url, _, key = _config()
    try:
        r = requests.request(method, url + path, timeout=TIMEOUT,
                             headers={"Authorization": f"Bearer {key}"}, **kw)
    except requests.RequestException as e:
        raise PapraError(f"Can't reach Papra: {e.__class__.__name__}", retry=True) from None
    if r.ok:
        return r
    # 5xx, timeouts and rate limits are worth retrying; other 4xx are not.
    retry = r.status_code >= 500 or r.status_code in (408, 429)
    known = {401: "Papra rejected the API key",
             403: "The API key lacks permission (needs documents:create)",
             404: "Papra returned 404: check PAPRA_URL and PAPRA_ORG_ID"}
    if r.status_code in known:
        raise PapraError(known[r.status_code], retry=retry)
    raise PapraError(f"Papra returned HTTP {r.status_code}: {r.text.strip()[:200]}", retry=retry)


def check() -> str:
    """Verify the URL and key. Returns a short description of the key."""
    key = _request("GET", "/api/api-keys/current").json().get("apiKey", {})
    perms = key.get("permissions", [])
    if perms and "documents:create" not in perms:
        raise PapraError(f"API key '{key.get('name')}' lacks documents:create", retry=False)
    return f"key '{key.get('name', '?')}'"


def upload(pdf: Path, filename: str) -> str:
    """Upload a PDF; returns the new document's ID."""
    _, org, _ = _config()
    with pdf.open("rb") as f:
        r = _request("POST", f"/api/organizations/{org}/documents",
                     files={"file": (filename, f, "application/pdf")})
    return r.json().get("document", {}).get("id", "")


if __name__ == "__main__":
    # `python papra.py` checks the settings in the environment (used by install.sh).
    import sys
    try:
        print(f"  OK: Papra accepted {check()}")
    except PapraError as e:
        sys.exit(f"  {e}")
