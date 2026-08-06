"""Scrappa Google Hotels client.

One request costs one credit, so the ledger is charged around every call --
including failures, which the provider still counts. The API key is read from
the environment and never logged or echoed into the output.
"""

import os

import requests
from dotenv import load_dotenv

from . import config

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


class QuotaExhausted(RuntimeError):
    pass


class SourceError(RuntimeError):
    pass


class ScrappaClient:
    def __init__(self, ledger, timeout=120):
        key = os.environ.get("SCRAPPA_API_KEY")
        if not key:
            raise SourceError("SCRAPPA_API_KEY is not set")
        self._key = key
        self.ledger = ledger
        self.timeout = timeout
        self._session = requests.Session()

    def _headers(self):
        return {"Accept": "application/json", "X-API-KEY": self._key}

    def search(self, query, check_in, check_out):
        """One listing call. Returns the `properties` array."""
        if self.ledger.remaining < 1:
            raise QuotaExhausted("no credits remaining this month")

        params = dict(config.FIXED_PARAMS)
        params.update({
            "q": query,
            "check_in_date": check_in,
            "check_out_date": check_out,
        })

        # Charge before dispatch: a request that fails in flight has still been
        # counted upstream, and under-counting is what silently overruns quota.
        self.ledger.charge(1)
        try:
            resp = self._session.get(
                config.SEARCH_URL, params=params,
                headers=self._headers(), timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SourceError(f"request failed: {exc.__class__.__name__}") from None

        if resp.status_code in (401, 403):
            raise SourceError(f"auth rejected (HTTP {resp.status_code})")
        if resp.status_code == 429:
            raise QuotaExhausted("rate limited or quota exhausted (HTTP 429)")
        if resp.status_code >= 400:
            raise SourceError(f"HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError:
            raise SourceError("non-JSON response") from None

        return payload.get("properties") or []
