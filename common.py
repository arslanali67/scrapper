"""Shared code for both scrapers. The Treg class is the only way any scraper talks to treg."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
ENDPOINTS = json.loads((ROOT / "endpoints.json").read_text(encoding="utf-8"))


class TregError(Exception):
    """A call that failed without breaking the run: skip this lead and continue."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class BudgetReached(Exception):
    """The next call could push spend past --max-cost. Stop the run cleanly."""


class OutOfBalance(Exception):
    """treg balance is empty (402). Stop the run; the message holds the top-up URL."""


def load_token():
    tok = os.environ.get("TREG_TOKEN")
    env = ROOT / ".env"
    if not tok and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TREG_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    if not tok:
        raise SystemExit("TREG_TOKEN missing: put it in .env (see .env.example)")
    return tok


class Treg:
    RETRY_DELAYS = (1, 4, 10)  # seconds between attempts on 429 / 5xx / connect errors

    def __init__(self, max_cost_usd, log_dir=ROOT / "runs", transport=None, token=None):
        self.budget_micro = round(max_cost_usd * 1e6)
        self.spent_micro = 0
        self.client = httpx.Client(base_url="https://treg.to", timeout=180, transport=transport,
                                   headers={"X-Treg-Token": token or load_token()})
        Path(log_dir).mkdir(exist_ok=True)
        self.log_path = Path(log_dir) / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl"

    @property
    def spent_usd(self):
        return self.spent_micro / 1e6

    def call(self, capability, params=None):
        """Call the endpoint chosen for `capability` in endpoints.json. Returns the parsed JSON."""
        ep = ENDPOINTS[capability]
        cap_micro = round(ep["max_cost_usd"] * 1e6)
        # Worst case is the per-call cap, so checking it here means --max-cost can never be overshot.
        if self.spent_micro + cap_micro > self.budget_micro:
            raise BudgetReached(f"budget ${self.budget_micro / 1e6:.2f} reached (spent ${self.spent_usd:.4f})")
        headers = {"X-Treg-Route-Max-Cost": str(ep["max_cost_usd"]), **ep.get("headers", {})}
        get = ep["method"] == "GET"
        req = dict(params=params if get else None, json=None if get else params, headers=headers,
                   timeout=ep.get("timeout_s", 180))
        t0 = time.time()
        for delay in (*self.RETRY_DELAYS, None):
            try:
                r = self.client.request(ep["method"], f"/call/{ep['id']}", **req)
            except httpx.ConnectError as e:  # nothing reached treg, safe to retry
                if delay is None:
                    raise TregError(f"{capability}: cannot reach treg ({e})")
                time.sleep(delay)
                continue
            except httpx.TimeoutException:
                # The call may still finish (and bill) server-side, so never retry it.
                self._log(capability, ep["id"], "timeout", 0, None, t0)
                raise TregError(f"{capability}: timed out")
            if (r.status_code == 429 or r.status_code >= 500) and r.status_code not in (502, 503) and delay is not None:
                time.sleep(delay)
                continue
            break
        cost = int(r.headers.get("X-Treg-Cost-Micro") or 0)
        self.spent_micro += cost
        try:
            body = r.json()
        except ValueError:
            body = {"_text": r.text[:500]}
        served = r.headers.get("X-Treg-Served-By") or (body.get("_treg") or {}).get("served_by") \
            if isinstance(body, dict) else None
        self._log(capability, ep["id"], r.status_code, cost, served, t0)
        if r.status_code < 400:
            return body
        detail = body.get("detail", body) if isinstance(body, dict) else body
        err = detail.get("error") if isinstance(detail, dict) else None
        if r.status_code == 402 and err != "route_max_cost":
            topup = detail.get("topup_url", "") if isinstance(detail, dict) else ""
            raise OutOfBalance(f"treg balance is empty. Top up: {topup or 'treg topup'}")
        # 502 too large, 503 provider out, 402 route_max_cost, 4xx bad input: not charged / skip lead.
        raise TregError(f"{capability}: HTTP {r.status_code} {str(detail)[:200]}", r.status_code)

    def _log(self, capability, endpoint, status, cost, served_by, t0):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "capability": capability, "endpoint": endpoint,
               "status": status, "cost_micro": cost, "served_by": served_by, "secs": round(time.time() - t0, 1)}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def estimate(calls):
    """Dry-run cost in USD: {capability: expected number of calls} x price_usd from endpoints.json."""
    return sum(ENDPOINTS[cap]["price_usd"] * n for cap, n in calls.items())
