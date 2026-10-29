"""v4 Brand (P1-1): a brief from a mostly blocked site is flagged unreadable, so the UI shows B02's recovery panel.
A page that failed on one attempt and was read on another counts as read. All inputs SYNTHETIC."""
from landfall import brand, db


def _source(pid, n, url, status, at):
    db.x("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)", f"src_{n}", pid, url, url, status, at, "", "", None, 0)


def _brief(pid, states, route="site"):
    facts = [db.put_record("claim", pid, {"state": s})["id"] for s in states]
    return db.put_record("brief", pid, {"route": route, "facts": facts, "read_at": "2026-10-27T12:00:00+00:00"}) | {
        "project_id": pid}


def test_most_pages_blocked_and_nothing_sourced_is_unreadable():
    _source("p1", 1, "https://x.example/", 200, "2026-10-27T11:00:00+00:00")
    for i in range(2, 6):
        _source("p1", i, f"https://x.example/{i}", 429, "2026-10-27T11:00:00+00:00")
    _source("p1", 9, "https://x.example/later", 429, "2026-10-27T13:00:00+00:00")  # after the brief: not this read
    r = brand.read_summary(_brief("p1", []))
    assert (r["pages_read"], r["pages_failed"], r["why"], r["unreadable"]) == (1, 4, "HTTP 429", True)


def test_a_supported_fact_or_a_description_keeps_the_brief():
    for i in range(1, 5):
        _source("p2", i, f"https://x.example/{i}", 429 if i > 1 else 200, "2026-10-27T11:00:00+00:00")
    assert brand.read_summary(_brief("p2", ["SUPPORTED", "TO_CHECK"]))["unreadable"] is False
    assert brand.read_summary(_brief("p2", [], route="description"))["unreadable"] is False


def test_a_page_read_on_a_later_attempt_counts_as_read():
    _source("p3", 1, "https://x.example/a", 429, "2026-10-27T10:00:00+00:00")
    _source("p3", 2, "https://x.example/a", 200, "2026-10-27T11:00:00+00:00")
    r = brand.read_summary(_brief("p3", []))
    assert (r["pages_read"], r["pages_failed"], r["unreadable"]) == (1, 0, False)


def test_reader_rows_say_failed_when_pages_were_found_but_blocked():
    assert brand.row_outcome([], 3) == ("FAILED", "3 pages found but couldn't be read")
    assert brand.row_outcome([], 0) == ("SKIPPED", "No page of this kind found")
    assert brand.row_outcome(["/about"], 2) == ("DONE", "/about")


def test_the_same_fact_under_the_same_label_is_one_row():
    """Owner, 28 Oct 2026 (Death Wish Coffee): "Founded" three times. The most specific value is kept, the others merge
    into it; a different label or a fact that says more stays its own row."""
    facts = [{"field": "founded", "value": "2012", "state": "SUPPORTED", "origin": "WEB"},
             {"field": "founded", "value": "Founded in 2012 in Saratoga Springs, New York", "state": "SUPPORTED", "origin": None},
             {"field": "founded", "value": "2012", "state": "SUPPORTED", "origin": "SOURCE"},
             {"field": "other", "label": "Also on the site", "value": "2012", "state": "SUPPORTED", "origin": "WEB"},
             {"field": "founded", "value": "2012 in Albany", "state": "TO_CHECK", "origin": "WEB"}]
    assert brand.merge_targets(facts) == [1, 1, 1, 3, 4]
    site, web = {**facts[2], "value": "Founded 2012"}, facts[0]  # equally specific: the brand's own site wins over the web
    assert brand.merge_targets([web, site]) == [1, 1]
