"""Offline checks for common.py (no network, no spend). Run: python test_common.py"""
import json
import tempfile

import httpx

import common
from common import BudgetReached, OutOfBalance, Treg, TregError, estimate

common.Treg.RETRY_DELAYS = (0, 0, 0)
LOG_DIR = tempfile.mkdtemp()


def treg(handler, budget=1.0):
    return Treg(budget, log_dir=LOG_DIR, transport=httpx.MockTransport(handler), token="test-token")


def reply(status=200, body=None, cost=0):
    return httpx.Response(status, json=body if body is not None else {"ok": True},
                          headers={"X-Treg-Cost-Micro": str(cost)})


def test_budget_zero_never_sends():
    def handler(req):
        raise AssertionError("no request may be sent with --max-cost 0")
    try:
        treg(handler, budget=0).call("maps_search", {"query": "x"})
    except BudgetReached:
        return
    raise AssertionError("expected BudgetReached")


def test_success_headers_cost_log():
    seen = {}

    def handler(req):
        seen.update(path=req.url.path, token=req.headers["X-Treg-Token"],
                    cap=req.headers["X-Treg-Route-Max-Cost"], body=json.loads(req.content))
        return reply(body={"rows": [1]}, cost=150)
    t = treg(handler)
    assert t.call("maps_search", {"query": "dentists", "limit": 100}) == {"rows": [1]}
    assert seen == {"path": "/call/crawl4ai.google.serp.maps", "token": "test-token", "cap": "0.002",
                    "body": {"query": "dentists", "limit": 100}}
    assert t.spent_micro == 150
    rec = json.loads(t.log_path.read_text().splitlines()[-1])
    assert rec["capability"] == "maps_search" and rec["cost_micro"] == 150 and "test-token" not in json.dumps(rec)


def test_budget_stops_before_overshoot():
    t = treg(lambda req: reply(cost=150), budget=0.002)  # maps cap is $0.002
    t.call("maps_search", {})  # 0 + 2000 <= 2000
    try:
        t.call("maps_search", {})  # 150 + 2000 > 2000
    except BudgetReached:
        return
    raise AssertionError("expected BudgetReached")


def test_retry_429_then_ok():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return reply(429) if n["calls"] < 3 else reply(body={"done": 1})
    assert treg(handler).call("serp", {"q": "x"}) == {"done": 1} and n["calls"] == 3


def test_503_skips_without_retry():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return reply(503, {"detail": "provider out"})
    try:
        treg(handler).call("serp", {"q": "x"})
    except TregError as e:
        assert e.status == 503 and n["calls"] == 1
        return
    raise AssertionError("expected TregError")


def test_402_balance_vs_route_cap():
    try:
        treg(lambda r: reply(402, {"error": "insufficient_balance", "topup_url": "https://treg.to/topup"})).call("serp", {})
        raise AssertionError("expected OutOfBalance")
    except OutOfBalance as e:
        assert "https://treg.to/topup" in str(e)
    try:
        treg(lambda r: reply(402, {"detail": {"error": "route_max_cost"}})).call("company_emails_by_domain", {})
        raise AssertionError("expected TregError")
    except TregError as e:
        assert e.status == 402


def test_get_sends_query_params():
    seen = {}

    def handler(req):
        seen.update(method=req.method, q=dict(req.url.params), body=req.content)
        return reply()
    treg(handler).call("company_emails_by_domain", {"domain": "acme.com", "limit": 3})
    assert seen == {"method": "GET", "q": {"domain": "acme.com", "limit": "3"}, "body": b""}


def test_estimate():
    assert abs(estimate({"maps_search": 10, "email_find_person": 100}) - (10 * 0.00015 + 100 * 0.000495)) < 1e-12


def test_cell_and_normalizers():
    from common import cell, domain_of, normalize_phone
    assert cell(None) == "" and cell(["a@x.com", "b@x.com", "a@x.com", None]) == "a@x.com | b@x.com"
    assert cell("=HYPERLINK(\"http://evil\")").startswith("'=") and cell("@SUM(A1)") == "'@SUM(A1)"
    assert cell("+15124741234") == "+15124741234" and cell("-12.5") == "-12.5"
    assert domain_of("https://www.Acme.com/about?x=1") == "acme.com" and domain_of("jo@Acme.co.uk") == "acme.co.uk"
    assert domain_of("") == "" and domain_of("not a url") == ""
    assert normalize_phone("(512) 474-1234") == "+1 512-474-1234" == normalize_phone("+15124741234")
    assert normalize_phone("12345") == "" and normalize_phone("020 7123 4567", "GB") == "+44 20 7123 4567"
    assert cell("+1 512-474-1234") == "+1 512-474-1234"  # not mistaken for a formula


def test_lead_writer_resume():
    from pathlib import Path
    from common import LeadWriter, PEOPLE_COLUMNS
    path = Path(tempfile.mkdtemp()) / "leads.csv"
    with LeadWriter(path, PEOPLE_COLUMNS, "person_linkedin_url") as w:
        assert w.write({"person_linkedin_url": "u1", "full_name": "Ana Ruiz", "all_emails": ["a@x.com", "b@x.com"]})
        assert w.write({"person_linkedin_url": "u2", "full_name": "José Ñúñez"})
    # "Crash" + rerun: a new writer on the same file sees both keys and skips them.
    w = LeadWriter(path, PEOPLE_COLUMNS, "person_linkedin_url")
    assert w.has("u1") and w.has("u2") and not w.has("u3")
    assert w.write({"person_linkedin_url": "u2", "full_name": "dup"}) is False
    assert w.write({"person_linkedin_url": "u3", "full_name": "New"})
    w.close()
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\xef\xbb\xbf") == 1  # one BOM, at the start
    lines = raw.decode("utf-8-sig").splitlines()
    assert len(lines) == 4 and lines[0].startswith("company_name,") and "José Ñúñez" in lines[2]
    assert "a@x.com | b@x.com" in lines[1]
    try:
        LeadWriter(path, PEOPLE_COLUMNS[:-1], "person_linkedin_url")
        raise AssertionError("expected SystemExit for different columns")
    except SystemExit:
        pass


def test_shared_columns_first():
    from common import COMPANY_COLUMNS, GOOGLE_COLUMNS, PEOPLE_COLUMNS, SHARED_COLUMNS
    assert len(SHARED_COLUMNS) == 12
    for cols in (GOOGLE_COLUMNS, PEOPLE_COLUMNS, COMPANY_COLUMNS):
        assert cols[:12] == SHARED_COLUMNS and len(cols) == len(set(cols))


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} checks passed")
