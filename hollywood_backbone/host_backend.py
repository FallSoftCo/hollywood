"""Read adapter for an existing host-local NextComp backend.

Credentials stay local. This deliberately exposes no execution or human input
endpoint: those require the separate original-principal admission contract.
"""
import asyncio
import json
import os
from pathlib import Path
import stat
import urllib.error
import urllib.parse
import urllib.request

from .backplane import BackplaneError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BackplaneError("LOCAL_BACKEND_REDIRECT_REFUSED")


class NextCompReadAdapter:
    def __init__(self, base_url: str, credential_file: str, *, timeout: float = 5):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or not 0 < timeout <= 10):
            raise ValueError("Explicit host-local backend URL required")
        self.base_url = base_url.rstrip("/")
        self.credential_file = Path(credential_file)
        if not self.credential_file.is_absolute():
            raise ValueError("Absolute private credential path required")
        self.timeout = timeout

    def _read(self, path):
        info = self.credential_file.stat()
        if (not stat.S_ISREG(info.st_mode) or (os.geteuid() != 0 and info.st_uid != os.geteuid())
                or info.st_mode & 0o077):
            raise BackplaneError("LOCAL_CREDENTIAL_UNAVAILABLE")
        credential = json.loads(self.credential_file.read_text()).get("adminToken")
        if not isinstance(credential, str) or len(credential) < 20:
            raise BackplaneError("LOCAL_CREDENTIAL_UNAVAILABLE")
        request = urllib.request.Request(self.base_url + path,
                                          headers={"Authorization": "Bearer " + credential})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(512 * 1024 + 1)
                if len(raw) > 512 * 1024:
                    raise BackplaneError("LOCAL_RESULT_TOO_LARGE")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise BackplaneError("LOCAL_RESULT_INVALID")
                return value
        except urllib.error.HTTPError as error:
            raise BackplaneError("LOCAL_BACKEND_AUTH_REQUIRED" if error.code in (401, 403)
                                 else "LOCAL_BACKEND_UNAVAILABLE") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise BackplaneError("LOCAL_BACKEND_UNAVAILABLE") from None

    async def runtime_status(self, request):
        if request.payload:
            raise BackplaneError("INVALID_PARAMETERS")
        value = await asyncio.to_thread(self._read, "/api/status")
        return {key: value[key] for key in ("connected", "version", "host", "local") if key in value}

    async def session_catalog(self, request):
        if set(request.payload) - {"limit", "cursor"}:
            raise BackplaneError("INVALID_PARAMETERS")
        limit = request.payload.get("limit", 20)
        cursor = request.payload.get("cursor")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise BackplaneError("INVALID_PARAMETERS")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor.encode()) > 4096):
            raise BackplaneError("INVALID_PARAMETERS")
        query = {"limit": str(limit)}
        if cursor is not None:
            query["cursor"] = cursor
        value = await asyncio.to_thread(self._read, "/api/threads?" + urllib.parse.urlencode(query))
        # Retain native catalogue freshness and pagination; do not replace an
        # unavailable provider with a fabricated empty, complete inventory.
        if not isinstance(value.get("threads"), list):
            raise BackplaneError("LOCAL_CATALOG_UNAVAILABLE")
        return value

    def handlers(self):
        return {"sessions.list": self.session_catalog, "runtime.status": self.runtime_status}
