import time
from typing import Any

import requests

from config import (
    BINANCE_BASE_URL,
    HTTP_RETRY,
    HTTP_RETRY_BACKOFF,
    HTTP_TIMEOUT,
)
from binance_eth.log import get_logger

log = get_logger(__name__)


class BinanceClient:
    def __init__(self, base_url: str = BINANCE_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "binance-eth-analyzer/0.1"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last_err: Exception | None = None
        for attempt in range(1, HTTP_RETRY + 1):
            try:
                resp = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_err = exc
                wait = HTTP_RETRY_BACKOFF ** attempt
                log.warning(
                    "GET %s failed (attempt %d/%d): %s — retry in %.1fs",
                    path, attempt, HTTP_RETRY, exc, wait,
                )
                time.sleep(wait)
        raise RuntimeError(f"GET {path} failed after {HTTP_RETRY} attempts: {last_err}")

    def get_price(self, symbol: str) -> float:
        data = self._get("/api/v3/ticker/price", {"symbol": symbol})
        return float(data["price"])

    def get_24hr(self, symbol: str) -> dict[str, Any]:
        return self._get("/api/v3/ticker/24hr", {"symbol": symbol})

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        return self._get("/api/v3/klines", params)
