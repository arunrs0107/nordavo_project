"""Helpdesk API client: rate limiting, retries, and the three error cases from the brief.

- 503: nothing was saved, so retry with exponential backoff + jitter.
- 429: wait the Retry-After seconds, retry. We also throttle ourselves below 5 req/s.
- 409: the ticket is already handled. Never retried; raised as AlreadyHandled.
- Timeouts / dropped connections on a POST are *ambiguous* (the server may have saved it).
  We retry, and if the retry gets a 409 we flag it, so the caller knows our own first
  attempt probably landed rather than someone else's answer.
"""
import random
import time
from typing import Any, Callable, Dict, List, Optional

import requests


class HelpdeskError(Exception):
    pass


class AlreadyHandled(HelpdeskError):
    def __init__(self, ticket_path: str, ambiguous: bool = False):
        super().__init__(f"already handled: {ticket_path}")
        self.ambiguous = ambiguous  # True if an earlier attempt of ours may have landed


class ApiError(HelpdeskError):
    def __init__(self, status: int, body: Any, path: str):
        super().__init__(f"{status} on {path}: {body}")
        self.status = status
        self.body = body


def _list_from(payload: Any, *keys: str) -> List[dict]:
    """The API returns either a bare list or an object wrapping one; accept both."""
    if isinstance(payload, list):
        return payload
    for key in keys:
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    raise HelpdeskError(f"Unexpected response shape: {str(payload)[:200]}")


class Helpdesk:
    def __init__(
        self,
        base_url: str,
        key: str,
        email: str,
        name: Optional[str] = None,
        max_rps: float = 4.0,  # the limit is 5/s; stay clear of it
        max_attempts: int = 8,
        timeout: float = 20.0,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}", "X-Candidate-Email": email}
        if name:
            self.headers["X-Candidate-Name"] = name
        self.session = session or requests.Session()
        self.min_interval = 1.0 / max_rps
        self.max_attempts = max_attempts
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._last_call = -1e9

    
    def _throttle(self) -> None:
        wait = self.min_interval - (self._clock() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = self._clock()

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.5 * 2 ** (attempt - 1)) * random.uniform(0.5, 1.5)

    def request(self, method: str, path: str, *, params=None, json=None) -> Any:
        url = f"{self.base_url}{path}"
        ambiguous = False  # becomes True after a timeout on a call that writes
        last_problem = ""
        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            try:
                resp = self.session.request(
                    method, url, headers=self.headers, params=params, json=json, timeout=self.timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_problem = f"network error: {exc.__class__.__name__}"
                if method == "POST":
                    ambiguous = True
                self._sleep(self._backoff(attempt))
                continue

            status = resp.status_code
            if status == 429:
                try:
                    retry_after = float(resp.headers.get("Retry-After", 1))
                except ValueError:
                    retry_after = 1.0
                last_problem = "429 rate limited"
                self._sleep(retry_after + 0.1)
                continue
            if status == 503:
                last_problem = "503 service unavailable"
                self._sleep(self._backoff(attempt))
                continue
            if status == 409:
                raise AlreadyHandled(path, ambiguous=ambiguous)
            if status >= 400:
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text[:300]
                raise ApiError(status, body, path)
            return resp.json() if resp.content else {}

        raise HelpdeskError(f"Giving up on {method} {path} after {self.max_attempts} attempts ({last_problem})")

   
    def status(self) -> dict:
        return self.request("GET", "/status")

    def examples(self) -> List[dict]:
        return _list_from(self.request("GET", "/examples"), "examples", "items", "data")

    def list_open(self) -> List[dict]:
        """All open tickets. Collected up front so handling tickets cannot disturb paging."""
        tickets: List[dict] = []
        cursor: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"limit": 10, "status": "open"}
            if cursor:
                params["cursor"] = cursor
            page = self.request("GET", "/tickets", params=params)
            tickets.extend(_list_from(page, "tickets", "items", "data"))
            cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not cursor:
                return tickets

    def get_ticket(self, ticket_id: str) -> dict:
        return self.request("GET", f"/tickets/{ticket_id}")

    
    def reply(self, ticket_id: str, payload: dict) -> dict:
        return self.request("POST", f"/tickets/{ticket_id}/reply", json=payload)

    def handoff(self, ticket_id: str, payload: dict) -> dict:
        return self.request("POST", f"/tickets/{ticket_id}/handoff", json=payload)

    def close(self, ticket_id: str, payload: dict) -> dict:
        return self.request("POST", f"/tickets/{ticket_id}/close", json=payload)

    def reset_practice(self) -> dict:
        return self.request("POST", "/practice/reset")
