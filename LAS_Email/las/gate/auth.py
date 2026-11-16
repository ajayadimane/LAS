"""Sender authentication from Authentication-Results / ARC-Authentication-Results.

Only headers stamped by a trusted authserv-id (our MX, e.g. mx.zohomail.com) are believed; any
other Authentication-Results header could have been written by the sender. If no trusted header
exists we fall back to verifying DKIM (dkimpy) and SPF (pyspf) ourselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import authres

from las.common.logging import get_logger
from las.ingest.mime import ParsedMail

log = get_logger(__name__)

_TWO_LEVEL_SUFFIXES = {
    "co.in",
    "org.in",
    "net.in",
    "gov.in",
    "ac.in",
    "firm.in",
    "gen.in",
    "ind.in",
    "edu.in",
    "res.in",
    "nic.in",
    "co.uk",
    "org.uk",
    "com.au",
    "co.jp",
}
_IP_IN_RECEIVED = re.compile(r"\[(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]+)\]")


def org_domain(domain: str) -> str:
    labels = domain.lower().strip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def aligned(a: str | None, b: str | None) -> bool:
    return bool(a and b) and org_domain(a or "") == org_domain(b or "")


@dataclass(frozen=True)
class AuthVerdict:
    spf: str | None
    dkim: str | None
    dmarc: str | None
    passed: bool
    source: str  # 'authentication-results' | 'arc' | 'fallback' | 'none'


class DnsAuthVerifier(Protocol):
    def verify_dkim(self, raw: bytes) -> tuple[str, str | None]: ...
    def check_spf(self, ip: str, mailfrom: str, helo: str) -> str: ...
    def verify_arc(self, raw: bytes) -> str: ...


class LiveDnsAuthVerifier:
    def verify_dkim(self, raw: bytes) -> tuple[str, str | None]:
        import dkim

        try:
            d = dkim.DKIM(raw)
            ok = d.verify()
            domain = d.domain.decode() if d.domain else None
            return ("pass" if ok else "fail"), domain
        except Exception:  # noqa: BLE001 - DNS/crypto errors mean 'not verified'
            return "temperror", None

    def check_spf(self, ip: str, mailfrom: str, helo: str) -> str:
        import spf

        try:
            result, _ = spf.check2(i=ip, s=mailfrom, h=helo)
            return str(result)
        except Exception:  # noqa: BLE001 - DNS errors mean 'not verified'
            return "temperror"

    def verify_arc(self, raw: bytes) -> str:
        import dkim

        try:
            cv, _, _ = dkim.arc_verify(raw)
            return cv.decode() if isinstance(cv, bytes) else str(cv)
        except Exception:  # noqa: BLE001 - any ARC error means 'not verified'
            return "fail"


def _parse(header_name: str, value: str) -> authres.AuthenticationResultsHeader | None:
    try:
        if header_name == "ARC-Authentication-Results":
            # "i=1; mx.zohomail.com; dkim=pass ..." — drop the instance tag.
            value = re.sub(r"^\s*i=\d+\s*;\s*", "", value)
        return authres.AuthenticationResultsHeader.parse(f"Authentication-Results: {value}")
    except Exception:  # noqa: BLE001 - malformed header: ignore it
        return None


def _prop(result: object, ptype: str, name: str) -> str | None:
    for p in getattr(result, "properties", []):
        if p.type == ptype and p.name == name:
            return str(p.value)
    return None


def _evaluate(
    h: authres.AuthenticationResultsHeader, from_domain: str
) -> tuple[str | None, str | None, str | None, bool]:
    spf = dkim_res = dmarc = None
    dkim_aligned = spf_aligned = False
    for r in h.results:
        method, result = r.method.lower(), str(r.result).lower()
        if method == "dmarc":
            dmarc = result
        elif method == "dkim":
            d = (
                _prop(r, "header", "d")
                or (_prop(r, "header", "i") or "").lstrip("@").split("@")[-1]
            )
            if dkim_res != "pass":
                dkim_res = result
            if result == "pass" and aligned(d, from_domain):
                dkim_aligned = True
        elif method == "spf":
            spf = result
            mf = (_prop(r, "smtp", "mailfrom") or "").split("@")[-1]
            if result == "pass" and aligned(mf, from_domain):
                spf_aligned = True
    if dmarc is not None and dmarc not in ("none",):
        passed = dmarc == "pass"
    else:
        passed = dkim_aligned or spf_aligned
    return spf, dkim_res, dmarc, passed


def evaluate_sender(
    mail: ParsedMail,
    raw: bytes,
    *,
    trusted_authserv_ids: list[str],
    verifier: DnsAuthVerifier | None,
) -> AuthVerdict:
    from_domain = mail.from_address.split("@")[-1] if "@" in mail.from_address else ""
    if not from_domain or mail.from_count != 1:
        return AuthVerdict(None, None, None, False, "none")
    trusted = {t.lower() for t in trusted_authserv_ids}

    # Topmost trusted Authentication-Results is the one our MX added.
    for value in mail.all_headers("Authentication-Results"):
        h = _parse("Authentication-Results", value)
        if h is None or str(h.authserv_id).lower() not in trusted:
            continue
        spf, dkim_res, dmarc, passed = _evaluate(h, from_domain)
        if passed:
            return AuthVerdict(spf, dkim_res, dmarc, True, "authentication-results")
        # Forwarded mail can break SPF/DKIM; accept a trusted, cryptographically valid ARC chain.
        if verifier is not None:
            for arc_value in mail.all_headers("ARC-Authentication-Results"):
                ah = _parse("ARC-Authentication-Results", arc_value)
                if ah is None or str(ah.authserv_id).lower() not in trusted:
                    continue
                a_spf, a_dkim, a_dmarc, a_passed = _evaluate(ah, from_domain)
                if a_passed and verifier.verify_arc(raw) == "pass":
                    return AuthVerdict(a_spf, a_dkim, a_dmarc, True, "arc")
        return AuthVerdict(spf, dkim_res, dmarc, False, "authentication-results")

    if verifier is None:
        return AuthVerdict(None, None, None, False, "none")
    dkim_res, d = verifier.verify_dkim(raw)
    spf_res = "none"
    received = mail.all_headers("Received")
    mailfrom = (mail.header("Return-Path") or "").strip("<> ") or mail.from_address
    if received:
        m = _IP_IN_RECEIVED.search(received[0])
        helo_m = re.search(r"from\s+(\S+)", received[0])
        if m:
            spf_res = verifier.check_spf(m.group(1), mailfrom, helo_m.group(1) if helo_m else "")
    passed = (dkim_res == "pass" and aligned(d, from_domain)) or (
        spf_res == "pass" and aligned(mailfrom.split("@")[-1], from_domain)
    )
    return AuthVerdict(spf_res, dkim_res, None, passed, "fallback")
