"""GBP/NPR exchange rate — fetched from Nepal Rastra Bank, cached in Redis.

NRB forex API (https://www.nrb.org.np/api/forex/v1/rates) requires
`from`/`to`/`page`/`per_page` query params and publishes once per day.
Response shape:

    {"data": {"payload": [
        {"date": "2026-07-08", "published_on": "2026-07-08 00:00:23",
         "rates": [{"currency": {"iso3": "GBP", "name": "...", "unit": 1},
                    "buy": "202.84", "sell": "203.64"}, ...]}
    ]}}

Entries within the requested date range come back oldest-first, so the last
element of `payload` is the most recent published rate.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import redis
import requests

from codesandbox.config import get_settings

log = logging.getLogger(__name__)

_CACHE_KEY = "billing:fx:gbp_npr"
_STALE_FALLBACK_KEY = _CACHE_KEY + ":last_known"
# Set (short TTL) after a failed NRB fetch so every page render doesn't
# re-pay the full network timeout while NRB is unreachable — without this,
# any page showing a NPR amount (billing, the header balance) blocked for
# the whole connect timeout on every request until a fetch succeeded.
_FETCH_FAILED_KEY = _CACHE_KEY + ":fetch_failed"
_FETCH_FAILED_TTL_SECONDS = 300

_redis_client: "redis.Redis | None" = None


def _get_redis() -> "redis.Redis":
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis_client


def _fetch_from_nrb() -> tuple[Decimal, date] | None:
    settings = get_settings()
    today = date.today()
    frm = today - timedelta(days=7)
    try:
        resp = requests.get(
            settings.nrb_forex_url,
            params={"from": frm.isoformat(), "to": today.isoformat(), "page": 1, "per_page": 10},
            timeout=(2, 5),  # (connect, read) — a dead network fails in 2s, not 5+
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("NRB forex fetch failed: %s", exc)
        return None

    payload = (data.get("data") or {}).get("payload") or []
    if not payload:
        return None

    latest = payload[-1]  # ascending order — last entry is the most recent publish
    for entry in latest.get("rates", []):
        currency = entry.get("currency") or {}
        if currency.get("iso3") != "GBP":
            continue
        try:
            buy = Decimal(str(entry["buy"]))
            sell = Decimal(str(entry["sell"]))
            unit = int(currency.get("unit") or 1) or 1
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None
        mid_rate = (buy + sell) / 2 / unit
        try:
            rate_date = date.fromisoformat(latest.get("date", ""))
        except ValueError:
            rate_date = today
        return mid_rate, rate_date

    return None


def get_gbp_npr_rate() -> Decimal:
    """1 GBP = <rate> NPR, mid-market (avg of NRB's buy/sell), Redis-cached.

    Falls back to the last successfully-fetched rate (no expiry) if NRB is
    unreachable when the TTL'd cache expires — a slightly-stale display
    rate beats a broken billing page or a blocked topup.
    """
    r = _get_redis()
    cached = r.get(_CACHE_KEY)
    if cached:
        try:
            return Decimal(json.loads(cached)["rate"])
        except (KeyError, ValueError, InvalidOperation, TypeError):
            pass

    fetched = None
    if not r.get(_FETCH_FAILED_KEY):
        fetched = _fetch_from_nrb()
        if fetched is None:
            r.set(_FETCH_FAILED_KEY, "1", ex=_FETCH_FAILED_TTL_SECONDS)
    if fetched is not None:
        rate, rate_date = fetched
        payload = json.dumps({"rate": str(rate), "date": rate_date.isoformat()})
        ttl = get_settings().nrb_forex_cache_ttl_seconds
        r.set(_CACHE_KEY, payload, ex=ttl)
        r.set(_STALE_FALLBACK_KEY, payload)  # no expiry — last-known-good
        return rate

    stale = r.get(_STALE_FALLBACK_KEY)
    if stale:
        try:
            parsed = json.loads(stale)
            log.warning("Using stale NRB rate from %s (live fetch failed)", parsed.get("date"))
            return Decimal(parsed["rate"])
        except (KeyError, ValueError, InvalidOperation, TypeError):
            pass

    raise RuntimeError("GBP/NPR exchange rate unavailable and no cached fallback exists")


def gbp_to_npr(amount_gbp: Decimal) -> Decimal:
    return (amount_gbp * get_gbp_npr_rate()).quantize(Decimal("0.01"))


def npr_to_gbp(amount_npr: Decimal) -> Decimal:
    rate = get_gbp_npr_rate()
    if rate <= 0:
        raise RuntimeError("Invalid GBP/NPR rate")
    return (amount_npr / rate).quantize(Decimal("0.01"))
