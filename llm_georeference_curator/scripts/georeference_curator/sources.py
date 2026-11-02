from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .llm_cache import LlmResponseCache


def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source must be a public HTTP(S) URL without credentials")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Non-public source address was not fetched")
    return url


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        public_url(new_url)
        return super().redirect_request(request, fp, code, message, headers, new_url)


class SourceAuditor:
    def __init__(self, directory, *, enabled=True, refresh=False, limit=3, timeout=15):
        self.cache = LlmResponseCache(directory, enabled=True)
        self.directory = Path(directory)
        self.refresh = refresh
        self.enabled = enabled
        self.limit = limit
        self.timeout = timeout
        self._lock = threading.Lock()
        self._memory = {}

    def audit(self, url_text, timeout=None, deadline=None):
        urls = list(dict.fromkeys(value.strip() for value in str(url_text).split("|") if value.strip()))[:self.limit]
        if not self.enabled:
            return [{"url": url, "status": "not_checked"} for url in urls]
        result = []
        for url in urls:
            key = hashlib.sha256(url.encode()).hexdigest()
            with self.cache.request_lock(key):
                with self._lock:
                    cached = self._memory.get(key)
                cached = cached or (None if self.refresh else self.cache.get(key))
                if cached:
                    result.append(dict(cached, snapshotReused=True))
                    continue
                record = {"url": url, "retrievedAt": datetime.now(timezone.utc).isoformat(), "factualLocalityMatch": "not_established"}
                try:
                    remaining = deadline - time.monotonic() if deadline else self.timeout
                    if remaining <= 0:
                        raise TimeoutError("Verification stage expired before source retrieval")
                    request = Request(public_url(url), headers={"User-Agent": "VASCULUM-georeference-source-audit/1.0", "Accept": "text/html,application/json,text/plain,application/pdf"})
                    with build_opener(PublicRedirects()).open(request, timeout=min(self.timeout, timeout or self.timeout, remaining)) as response:
                        data = response.read(1024 * 1024 + 1)
                        digest = hashlib.sha256(data).hexdigest()
                        self.directory.mkdir(parents=True, exist_ok=True)
                        snapshot = self.directory / f"{digest}.snapshot"
                        snapshot.write_bytes(data)
                        record.update(status="reachable", finalUrl=response.geturl(), contentType=response.headers.get("Content-Type", ""), sha256=digest, snapshotPath=str(snapshot), hashScope="first_1048577_bytes" if len(data) > 1024 * 1024 else "complete_response")
                except Exception as exc:
                    record.update(status="unavailable", error=str(exc))
                with self._lock:
                    self._memory[key] = record
                if record["status"] == "reachable":
                    self.cache.put(key, record)
                result.append(record)
        return result


def audit_candidates(candidates, auditor, timeout=None, deadline=None):
    for candidate in candidates:
        checks = auditor.audit(candidate.source_urls, timeout=timeout, deadline=deadline)
        candidate.source_checks = json.dumps(checks, ensure_ascii=False, separators=(",", ":"))
        # Reachability establishes provenance, never the truth of a locality match.
        if checks and not any(item["status"] == "reachable" for item in checks):
            candidate.confidence = "low"
            candidate.curation_reasons = "Source retrieval unavailable; estimate retained with low confidence"
