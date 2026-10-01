"""Tests for the alarm / ticketing simulator API (simulator_app.py)."""

import copy

import pytest
from fastapi.testclient import TestClient

import simulator_app
from simulator_app import DATA_STORE, app


@pytest.fixture(autouse=True)
def fresh_data():
    """The app mutates DATA_STORE (ticket create/update); reload it around every test."""
    simulator_app.load_data_from_json()
    yield
    simulator_app.load_data_from_json()


@pytest.fixture
def client():
    return TestClient(app)


# --- system -----------------------------------------------------------------


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["service"] == "alarm-and-ticketing-simulator"


def test_data_reload_restores_original_data(client):
    n_tickets = len(DATA_STORE["tickets"])
    client.post("/tickets", json={"title": "t", "asset_id": "CMP-201"})
    assert len(DATA_STORE["tickets"]) == n_tickets + 1

    body = client.post("/data/reload").json()
    assert body["status"] == "reloaded"
    assert body["tickets_count"] == n_tickets
    assert body["alarms_count"] == len(DATA_STORE["alarms"])


def test_load_data_missing_file_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(simulator_app, "MOCK_DATA_PATH", tmp_path / "nope.json")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        simulator_app.load_data_from_json()


def test_load_data_falls_back_to_cwd(monkeypatch, tmp_path):
    (tmp_path / "mock_data.json").write_text('{"assets": [{"asset_id": "X"}]}', encoding="utf-8")
    monkeypatch.setattr(simulator_app, "MOCK_DATA_PATH", tmp_path / "missing.json")
    monkeypatch.chdir(tmp_path)
    simulator_app.load_data_from_json()
    assert DATA_STORE["assets"] == [{"asset_id": "X"}]
    assert DATA_STORE["alarms"] == []  # absent keys default to empty


def test_trace_headers_echoed(client):
    r = client.get("/health", headers={"trace_id": "abc", "x-client-id": "me", "x-metadata-tag": "t"})
    assert (r.headers["trace_id"], r.headers["x-client-id"], r.headers["x-metadata-tag"]) == ("abc", "me", "t")


def test_trace_headers_defaults(client):
    r = client.get("/health")
    assert r.headers["trace_id"].startswith("trace-")
    assert r.headers["x-client-id"] == "simulator-client"
    assert r.headers["x-metadata-tag"] == "auto"


def test_trace_id_accepts_dashed_header(client):
    assert client.get("/health", headers={"trace-id": "dash"}).headers["trace_id"] == "dash"


# --- assets -----------------------------------------------------------------


def test_asset_search_all_and_limit(client):
    body = client.get("/assets/search").json()
    assert body["total"] == len(DATA_STORE["assets"])
    body = client.get("/assets/search", params={"limit": 2}).json()
    assert len(body["results"]) == 2
    assert body["total"] == len(DATA_STORE["assets"])


def test_asset_search_by_query_is_case_insensitive(client):
    body = client.get("/assets/search", params={"query": "cmp-201"}).json()
    assert [a["asset_id"] for a in body["results"]] == ["CMP-201"]


def test_asset_search_by_unit_and_site(client):
    by_unit = client.get("/assets/search", params={"unit": "unit 2", "limit": 100}).json()
    assert by_unit["total"] > 0
    assert all(a["unit"] == "Unit 2" for a in by_unit["results"])
    assert client.get("/assets/search", params={"site": "nowhere"}).json()["total"] == 0
    assert client.get("/assets/search", params={"site": "eastrefinery"}).json()["total"] > 0


def test_asset_metadata(client):
    assert client.get("/assets/cmp-201/metadata").json()["asset_id"] == "CMP-201"


def test_asset_metadata_404(client):
    assert client.get("/assets/NOPE/metadata").status_code == 404


# --- alarms -----------------------------------------------------------------


def test_get_alarms_defaults_sorted_by_start_time_desc(client):
    body = client.get("/alarms").json()
    times = [a["start_time"] for a in body["data"]]
    assert times == sorted(times, reverse=True)
    assert body["pagination"]["total_count"] == len(DATA_STORE["alarms"])


@pytest.mark.parametrize("field", ["severity", "status", "asset_id", "unit", "site"])
def test_get_alarms_filters(client, field):
    sample = DATA_STORE["alarms"][0][field]
    body = client.get("/alarms", params={field: sample.upper()}).json()
    assert body["data"]
    assert all(a[field] == sample for a in body["data"])
    assert body["pagination"]["total_count"] == sum(1 for a in DATA_STORE["alarms"] if a[field] == sample)


def test_get_alarms_pagination(client):
    total = len(DATA_STORE["alarms"])
    p1 = client.get("/alarms", params={"page": 1, "page_size": 5}).json()
    p2 = client.get("/alarms", params={"page": 2, "page_size": 5}).json()
    assert len(p1["data"]) == 5
    assert p1["pagination"]["total_pages"] == (total + 4) // 5
    assert {a["alarm_id"] for a in p1["data"]}.isdisjoint(a["alarm_id"] for a in p2["data"])


def test_get_alarms_empty_result_has_one_page(client):
    body = client.get("/alarms", params={"asset_id": "NOPE"}).json()
    assert body["data"] == []
    assert body["pagination"]["total_pages"] == 1


def test_get_alarms_numeric_sort(client):
    params = {"sort_by": "priority_score", "sort_order": "asc", "page_size": 100}
    scores = [a["priority_score"] for a in client.get("/alarms", params=params).json()["data"]]
    assert scores == sorted(scores)


def test_get_alarms_unknown_sort_field_is_400(client):
    r = client.get("/alarms", params={"sort_by": "bogus"})
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_get_alarms_sort_handles_missing_values(client):
    # active alarms have end_time = None; they must sort without error
    r = client.get("/alarms", params={"sort_by": "end_time", "page_size": 100})
    assert r.status_code == 200
    assert len(r.json()["data"]) == len(DATA_STORE["alarms"])


def test_get_alarm_by_id(client):
    assert client.get("/alarms/alm-9021").json()["alarm_id"] == "ALM-9021"
    assert client.get("/alarms/ALM-0000").status_code == 404


def test_alarm_summary_counts(client):
    body = client.post("/alarms/summary", json={}).json()
    sev = body["severity_breakdown"]
    assert body["total_alarms"] == len(DATA_STORE["alarms"]) == sum(sev.values())
    assert body["kpis"]["alarm_count"] == body["total_alarms"]


def test_alarm_summary_filters_and_trace_metadata(client):
    body = client.post(
        "/alarms/summary",
        json={"asset_ids": ["cmp-201"], "severity": ["CRITICAL"]},
        headers={"trace_id": "t1", "x-client-id": "c1", "x-metadata-tag": "m1"},
    ).json()
    expected = [a for a in DATA_STORE["alarms"] if a["asset_id"] == "CMP-201" and a["severity"] == "critical"]
    assert body["total_alarms"] == len(expected)
    assert body["trace_metadata"] == {"trace_id": "t1", "x_client_id": "c1", "x_metadata_tag": "m1"}


def test_alarm_summary_empty_selection_has_zero_recurring_rate(client):
    body = client.post("/alarms/summary", json={"asset_ids": ["NOPE"]}).json()
    assert body["total_alarms"] == 0
    assert body["summary"] == []
    assert body["kpis"]["recurring_rate"] == 0.0


def test_alarm_trends_echoes_request(client):
    body = client.post("/alarms/trends", json={"asset_ids": ["CMP-201"], "bucket": "hourly"}).json()
    assert body["asset_ids"] == ["CMP-201"]
    assert body["bucket"] == "hourly"
    assert body["time_range"] == {}
    assert body["data"]


def test_alarm_trends_with_time_range(client):
    tr = {"start_time": "2026-01-01T00:00:00Z", "end_time": "2026-02-01T00:00:00Z"}
    assert client.post("/alarms/trends", json={"time_range": tr}).json()["time_range"] == tr


def test_alarm_correlation(client):
    body = client.post("/alarms/correlation", json={"lag_window_minutes": 30}, headers={"trace_id": "c"}).json()
    assert body["lag_window_minutes"] == 30
    assert body["correlations"] == DATA_STORE["correlations"]
    assert body["trace_metadata"]["trace_id"] == "c"


def test_flood_analysis(client):
    body = client.post("/alarms/flood-analysis", json={}).json()
    assert body["unit"] == "Unit 2"
    assert body["flood_events_count"] == len(body["flood_windows"]) == len(DATA_STORE["flood_windows"])
    assert {"start", "end"} <= body["flood_windows"][0].keys()  # relied on by Postman CHAIN-02


def test_rationalization_candidates(client):
    assert client.post("/alarms/rationalization-candidates", json={}).json() == {
        "candidates": DATA_STORE["rationalization_candidates"]
    }


@pytest.mark.parametrize(
    "score,urgency,level",
    [(94.5, "immediate", "P1"), (91.0, "immediate", "P1"), (82.0, "urgent", "P2"), (75.0, "urgent", "P2"), (45.0, "routine", "P3")],
)
def test_priority_score_bands(client, score, urgency, level):
    alarm = next(a for a in DATA_STORE["alarms"] if a["priority_score"] == score)
    body = client.post("/alarms/priority-score", json={"alarm_id": alarm["alarm_id"].lower()}).json()
    assert body["alarm_id"] == alarm["alarm_id"]
    assert (body["urgency"], body["recommended_priority_level"]) == (urgency, level)


def test_priority_score_critical_factors(client):
    alarm = next(a for a in DATA_STORE["alarms"] if a["severity"] == "critical")
    factors = client.post("/alarms/priority-score", json={"alarm_id": alarm["alarm_id"]}).json()["factors"]
    assert factors["asset_criticality_weight"] == 1.5
    assert factors["safety_trip_proximity"] == "high"


def test_priority_score_unknown_alarm_uses_default(client):
    body = client.post("/alarms/priority-score", json={"alarm_id": "ALM-0"}).json()
    assert body["priority_score"] == 75.0
    assert body["recommended_priority_level"] == "P2"


def test_priority_score_requires_alarm_id(client):
    assert client.post("/alarms/priority-score", json={}).status_code == 422


def test_operator_recommendations_known_alarm(client):
    alarm_id = next(iter(DATA_STORE["recommendations"]))
    snapshot = copy.deepcopy(DATA_STORE["recommendations"])
    body = client.post(
        "/recommendations/operator-actions", json={"alarm_id": alarm_id.lower()}, headers={"trace_id": "r"}
    ).json()
    assert body["alarm_id"] == alarm_id
    assert body["trace_metadata"]["trace_id"] == "r"
    assert DATA_STORE["recommendations"] == snapshot  # response is a copy, store untouched


def test_operator_recommendations_unknown_alarm_gets_default(client):
    body = client.post("/recommendations/operator-actions", json={"alarm_id": "zzz"}).json()
    assert body["alarm_id"] == "ZZZ"
    assert body["asset_id"] == "UNKNOWN"
    assert body["immediate_actions"]


def test_calculation_generate_then_execute(client):
    gen = client.post("/calculation-code/generate", json={"calculation_type": "mean"}).json()
    assert gen["calculation_id"].startswith("CALC-")
    assert "calculate_mean" in gen["code_snippet"]
    run = client.post(
        "/calculation-code/execute", json={"calculation_id": gen["calculation_id"]}, headers={"trace_id": "x"}
    ).json()
    assert run["calculation_id"] == gen["calculation_id"]
    assert run["status"] == "completed"
    assert run["trace_metadata"]["trace_id"] == "x"


def test_calculation_generate_ids_are_unique(client):
    ids = {client.post("/calculation-code/generate", json={}).json()["calculation_id"] for _ in range(5)}
    assert len(ids) == 5


def test_kpi_definitions(client):
    assert client.get("/analytics/kpi-definitions").json() == {"kpis": DATA_STORE["kpi_definitions"]}


# --- tickets ----------------------------------------------------------------


def test_list_tickets_and_filters(client):
    all_tickets = client.get("/tickets").json()
    assert all_tickets["total"] == len(DATA_STORE["tickets"])
    open_only = client.get("/tickets", params={"status": "OPEN"}).json()
    assert open_only["tickets"] and all(t["status"] == "open" for t in open_only["tickets"])
    assert len(client.get("/tickets", params={"limit": 1}).json()["tickets"]) == 1


def test_list_tickets_by_asset_and_severity(client):
    t = DATA_STORE["tickets"][0]
    by_asset = client.get("/tickets", params={"asset_id": t["asset_id"].lower()}).json()
    assert all(x["asset_id"] == t["asset_id"] for x in by_asset["tickets"])
    by_sev = client.get("/tickets", params={"severity": t["severity"]}).json()
    assert all(x["severity"] == t["severity"] for x in by_sev["tickets"])


def test_list_tickets_by_multiple_asset_ids(client):
    ids = sorted({t["asset_id"] for t in DATA_STORE["tickets"]})[:2]
    body = client.get("/tickets", params={"asset_ids": f" {ids[0]} , {ids[-1].lower()},"}).json()
    assert body["total"] == sum(1 for t in DATA_STORE["tickets"] if t["asset_id"] in set(ids))


def test_search_tickets_ranks_by_relevance(client):
    body = client.get("/tickets/search", params={"query": "anti-surge valve linkage"}).json()
    assert body["count"] == len(body["results"]) > 0
    scores = [r["relevance_score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert "relevance_score" not in DATA_STORE["tickets"][0]  # store not polluted


def test_search_tickets_asset_filter_and_no_match(client):
    assert client.get("/tickets/search", params={"query": "zzzzqqq"}).json()["count"] == 0
    body = client.get("/tickets/search", params={"query": "pressure", "asset_id": "NOPE"}).json()
    assert body["count"] == 0


def test_search_tickets_requires_query(client):
    assert client.get("/tickets/search").status_code == 422


def test_get_ticket(client):
    assert client.get("/tickets/inc-1042").json()["ticket_id"] == "INC-1042"
    assert client.get("/tickets/INC-0").status_code == 404


def test_create_ticket(client):
    before = len(DATA_STORE["tickets"])
    r = client.post(
        "/tickets",
        json={"title": "Seal leak", "asset_id": "CMP-201", "alarm_id": "ALM-9021"},
        headers={"trace_id": "tr-9"},
    )
    assert r.status_code == 201
    ticket = r.json()["ticket"]
    assert ticket["ticket_id"] == f"INC-{1000 + before + 1}"
    assert (ticket["status"], ticket["severity"], ticket["priority"]) == ("open", "high", "P2")
    assert ticket["audit_trail"][0]["trace_id"] == "tr-9"
    assert DATA_STORE["tickets"][0]["ticket_id"] == ticket["ticket_id"]  # newest first
    assert client.get(f"/tickets/{ticket['ticket_id']}").status_code == 200


def test_create_ticket_validates_required_fields(client):
    assert client.post("/tickets", json={"title": "no asset"}).status_code == 422


def test_update_ticket_close_sets_resolved_at(client):
    open_ticket = next(t for t in DATA_STORE["tickets"] if t["status"] == "open")
    r = client.patch(
        f"/tickets/{open_ticket['ticket_id']}",
        json={"status": "Closed", "resolution_notes": "Fixed", "priority": "P3", "assigned_to": "Ops", "assigned_user": "Sam"},
    )
    ticket = r.json()["ticket"]
    assert ticket["status"] == "Closed"
    assert ticket["resolved_at"]
    assert (ticket["resolution_notes"], ticket["priority"], ticket["assigned_to"], ticket["assigned_user"]) == (
        "Fixed", "P3", "Ops", "Sam",
    )


def test_update_ticket_non_terminal_status_does_not_resolve(client):
    open_ticket = next(t for t in DATA_STORE["tickets"] if t["status"] == "open")
    ticket = client.patch(f"/tickets/{open_ticket['ticket_id']}", json={"status": "in_progress"}).json()["ticket"]
    assert ticket["status"] == "in_progress"
    assert not ticket.get("resolved_at")


def test_update_ticket_work_notes_append_to_audit_trail(client):
    tid = DATA_STORE["tickets"][0]["ticket_id"]
    ticket = client.patch(f"/tickets/{tid}", json={"work_notes": "called vendor"}).json()["ticket"]
    assert ticket["audit_trail"][-1]["note"] == "called vendor"


def test_update_ticket_404(client):
    assert client.patch("/tickets/INC-0", json={"status": "closed"}).status_code == 404
