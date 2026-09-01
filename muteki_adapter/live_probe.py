"""Live Network & Security Prober Adapter for real-world target URLs.

Performs live HTTP/HTTPS requests, extracts technology fingerprints, security headers,
analyzes HTML structures (forms, inputs, meta tags), discovers public endpoints,
and normalizes real findings into InvestigationEvents and InvestigationResults.
"""

from __future__ import annotations

import asyncio
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

from app.models import (
    Evidence,
    InvestigationEvent,
    InvestigationResult,
    SandboxTarget,
    Strategy,
    TrustedTargetRegistry,
)
from app.validation import validate_target


class LiveNetworkAdapter:
    """Real HTTP/HTTPS Network & Security Prober for live target URLs."""

    def __init__(
        self,
        registry: TrustedTargetRegistry,
        *,
        run_id: str = "live-run-001",
        timeout: float = 8.0,
    ) -> None:
        self.registry = registry
        self.run_id = run_id
        self.timeout = timeout
        self._events: list[InvestigationEvent] = []

    def _add_event(self, event_type: str, summary: str) -> InvestigationEvent:
        event = InvestigationEvent(
            sequence=len(self._events) + 1,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            type=event_type,
            run_id=self.run_id,
            worker_id="worker-live-prober",
            summary=summary,
        )
        self._events.append(event)
        return event

    def _fetch_url(self, url: str) -> dict[str, Any]:
        """Perform a synchronous HTTP GET request with safe SSL handling and realistic browser headers."""
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme:
            url = f"http://{url}"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        start_t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as response:
                content = response.read(65536)  # Read up to 64KB
                elapsed = time.time() - start_t
                return {
                    "url": url,
                    "final_url": response.geturl(),
                    "status_code": response.status,
                    "headers": dict(response.headers),
                    "body": content.decode("utf-8", errors="replace"),
                    "elapsed": elapsed,
                    "error": None,
                }
        except urllib.error.HTTPError as exc:
            elapsed = time.time() - start_t
            try:
                body = exc.read(16384).decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return {
                "url": url,
                "final_url": url,
                "status_code": exc.code,
                "headers": dict(exc.headers),
                "body": body,
                "elapsed": elapsed,
                "error": str(exc),
            }
        except Exception as exc:
            elapsed = time.time() - start_t
            return {
                "url": url,
                "final_url": url,
                "status_code": None,
                "headers": {},
                "body": "",
                "elapsed": elapsed,
                "error": str(exc),
            }

    async def run_strategy(
        self,
        target: SandboxTarget,
        strategy: Strategy,
    ) -> InvestigationResult:
        """Execute live network probing based on strategy priorities and target URL."""
        validate_target(target, self.registry)
        base_url = target.runtime_reference
        if base_url.startswith("mock://"):
            base_url = "http://127.0.0.1:8000"

        evidence_list: list[Evidence] = []
        progress_signals: list[str] = []
        event_summary: list[str] = []

        self._add_event(
            "probe.started",
            f"Initiating security analysis on target: {base_url} (Strategy Revision {strategy.revision})",
        )

        priorities = set(strategy.priorities)

        # -------------------------------------------------------------
        # Phase 1: Base URL Fetch & Technology Fingerprinting
        # -------------------------------------------------------------
        self._add_event("probe.recon", f"Connecting to base URL: {base_url}")
        res = await asyncio.to_thread(self._fetch_url, base_url)

        if res["error"] and res["status_code"] is None:
            self._add_event("probe.error", f"Connection failed to {base_url}: {res['error']}")
            event_summary.append(f"Network error connecting to {base_url}: {res['error']}")
            return InvestigationResult(
                run_id=self.run_id,
                solved=False,
                evidence=[],
                evidence_summary=f"Unable to connect to target {base_url}: {res['error']}",
                progress_signals=["connection_error"],
                elapsed_seconds=res["elapsed"],
                event_summary=event_summary,
                error=res["error"],
            )

        headers = res["headers"]
        status = res["status_code"]
        body = res["body"]
        final_url = res.get("final_url", base_url)

        # 1. Server & Technology Detection
        server = headers.get("Server") or headers.get("server") or "Not disclosed"
        powered_by = headers.get("X-Powered-By") or headers.get("x-powered-by") or "None"
        content_type = headers.get("Content-Type") or headers.get("content-type") or "Unknown"

        # 2. Extract HTML Page Title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        page_title = title_match.group(1).strip() if title_match else "No HTML title found"
        page_title = re.sub(r"\s+", " ", page_title)[:100]

        # 3. Extract Generator Meta Tags (WordPress, Drupal, Joomla, etc.)
        gen_match = re.search(r'<meta[^>]*name=["\']generator["\'][^>]*content=["\']([^"\']+)["\']', body, re.IGNORECASE)
        generator = gen_match.group(1).strip() if gen_match else None

        tech_summary = f"HTTP {status} at {final_url} | Title: '{page_title}' | Server: {server} | Powered-By: {powered_by}"
        if generator:
            tech_summary += f" | Generator: {generator}"

        self._add_event("probe.surface", tech_summary)
        event_summary.append(f"Target responded with HTTP {status} (Title: '{page_title}', Server: {server})")

        ev_recon = Evidence(
            type="reconnaissance",
            summary=f"Target web surface: HTTP {status}, Server: {server}, PoweredBy: {powered_by}, Title: '{page_title}'",
            confidence=0.90,
            source_event=len(self._events),
        )
        evidence_list.append(ev_recon)
        progress_signals.append("reconnaissance")

        # -------------------------------------------------------------
        # Phase 2: Security Headers & Cookie Audit
        # -------------------------------------------------------------
        sec_header_checks = {
            "Strict-Transport-Security": headers.get("Strict-Transport-Security") or headers.get("strict-transport-security"),
            "Content-Security-Policy": headers.get("Content-Security-Policy") or headers.get("content-security-policy"),
            "X-Frame-Options": headers.get("X-Frame-Options") or headers.get("x-frame-options"),
            "X-Content-Type-Options": headers.get("X-Content-Type-Options") or headers.get("x-content-type-options"),
            "Referrer-Policy": headers.get("Referrer-Policy") or headers.get("referrer-policy"),
        }

        missing_sec_headers = [k for k, v in sec_header_checks.items() if not v]
        present_sec_headers = [f"{k}: {v}" for k, v in sec_header_checks.items() if v]

        if missing_sec_headers:
            ev_headers = Evidence(
                type="observation",
                summary=f"Security headers missing: {', '.join(missing_sec_headers)}",
                confidence=0.85,
                source_event=len(self._events),
            )
            evidence_list.append(ev_headers)
            self._add_event("probe.security_headers", f"Missing security headers: {', '.join(missing_sec_headers)}")

        if present_sec_headers:
            ev_pres = Evidence(
                type="observation",
                summary=f"Security headers enabled: {', '.join(present_sec_headers[:2])}",
                confidence=0.90,
                source_event=len(self._events),
            )
            evidence_list.append(ev_pres)

        # Cookie Security Inspection
        set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie")
        if set_cookie:
            is_httponly = "httponly" in set_cookie.lower()
            is_secure = "secure" in set_cookie.lower()
            is_samesite = "samesite" in set_cookie.lower()
            cookie_flags = []
            if not is_httponly:
                cookie_flags.append("Missing HttpOnly")
            if not is_secure and base_url.startswith("https://"):
                cookie_flags.append("Missing Secure flag")
            if not is_samesite:
                cookie_flags.append("Missing SameSite attribute")

            if cookie_flags:
                ev_cookie = Evidence(
                    type="observation",
                    summary=f"Session cookie security concerns: {', '.join(cookie_flags)}",
                    confidence=0.80,
                    source_event=len(self._events),
                )
                evidence_list.append(ev_cookie)
                self._add_event("probe.cookie_audit", ev_cookie.summary)

        # -------------------------------------------------------------
        # Phase 3: Form, Login & Parameter Surface Extraction
        # -------------------------------------------------------------
        forms = re.findall(r"<form[^>]*>(.*?)</form>", body, re.IGNORECASE | re.DOTALL)
        inputs = re.findall(r'<input[^>]*name=["\']([^"\']+)["\']', body, re.IGNORECASE)
        form_actions = re.findall(r'<form[^>]*action=["\']([^"\']+)["\']', body, re.IGNORECASE)

        if forms or inputs:
            discovered_inputs = list(set(inputs))[:6]
            form_summary = f"Discovered {len(forms)} HTML form(s) with parameters: {', '.join(discovered_inputs)}"
            if form_actions:
                form_summary += f" (Actions: {', '.join(form_actions[:3])})"

            ev_forms = Evidence(
                type="correlation",
                summary=form_summary,
                confidence=0.85,
                source_event=len(self._events),
            )
            evidence_list.append(ev_forms)
            self._add_event("probe.forms", form_summary)
            progress_signals.append("strong evidence")
            event_summary.append(f"Identified {len(forms)} interactive form(s) on target")

        # -------------------------------------------------------------
        # Phase 4: Active Endpoint & Path Discovery (Concurrent)
        # -------------------------------------------------------------
        paths_to_probe = [
            "/robots.txt",
            "/sitemap.xml",
            "/login",
            "/admin",
            "/api",
            "/search",
            "/.env",
            "/.well-known/security.txt",
        ]
        self._add_event("probe.paths", f"Probing {len(paths_to_probe)} standard endpoint paths on {base_url}")

        async def probe_path(path: str) -> tuple[str, dict[str, Any]]:
            target_url = urllib.parse.urljoin(base_url, path)
            res_path = await asyncio.to_thread(self._fetch_url, target_url)
            return path, res_path

        path_results = await asyncio.gather(*[probe_path(p) for p in paths_to_probe])

        found_endpoints: list[str] = []
        for path, res_p in path_results:
            status_p = res_p["status_code"]
            if status_p in (200, 301, 302, 401, 403):
                found_endpoints.append(f"{path} (HTTP {status_p})")
                self._add_event("probe.endpoint_found", f"Discovered endpoint: {path} -> HTTP {status_p}")

        if found_endpoints:
            ev_endpoints = Evidence(
                type="correlation",
                summary=f"Active endpoints discovered on {base_url}: {', '.join(found_endpoints)}",
                confidence=0.90,
                source_event=len(self._events),
            )
            evidence_list.append(ev_endpoints)
            progress_signals.append("strong evidence")
            event_summary.append(f"Discovered {len(found_endpoints)} reachable path(s) on target")

        # -------------------------------------------------------------
        # Phase 5: Verification & Comprehensive Assessment
        # -------------------------------------------------------------
        # Solved is True only when a comprehensive audit is completed with valid evidence across multiple categories
        has_surface = any(e.type == "reconnaissance" for e in evidence_list)
        has_headers = any("security headers" in e.summary.lower() for e in evidence_list)
        has_endpoints_or_forms = any(e.type == "correlation" for e in evidence_list)

        solved = bool(has_surface and has_headers and (has_endpoints_or_forms or len(evidence_list) >= 3))

        if solved:
            ev_success = Evidence(
                type="verified_success",
                summary=(
                    f"Comprehensive security analysis verified for {base_url}. "
                    f"Profiled: Server ({server}), Title ('{page_title}'), "
                    f"Security Posture ({len(missing_sec_headers)} missing headers), "
                    f"Endpoints ({len(found_endpoints)} discovered)."
                ),
                confidence=1.0,
                source_event=len(self._events),
            )
            evidence_list.append(ev_success)
            progress_signals.append("verified success")
            event_summary.append("Target security profiling and surface audit completed successfully.")

        self._add_event("probe.completed", f"Iteration {strategy.revision} security analysis completed for {base_url}.")

        # Craft human-readable evidence summary tailored specifically to the site
        if solved:
            summary_text = (
                f"Completed live security analysis of {base_url} (Title: '{page_title}'). "
                f"Detected server: {server}, identified {len(missing_sec_headers)} security header gaps, "
                f"and mapped {len(found_endpoints)} endpoints."
            )
        else:
            summary_text = (
                f"Partial security audit of {base_url} (HTTP {status}, Server: {server}). "
                f"Collected {len(evidence_list)} observations across headers and surface endpoints."
            )

        return InvestigationResult(
            run_id=self.run_id,
            solved=solved,
            evidence=evidence_list,
            evidence_summary=summary_text,
            progress_signals=progress_signals,
            elapsed_seconds=round(res["elapsed"] + 0.5, 2),
            event_summary=event_summary,
            error=None,
        )

    async def subscribe_events(self, run_id: str) -> AsyncIterator[InvestigationEvent]:
        """Stream normalized investigation events."""
        for event in self._events:
            yield event
