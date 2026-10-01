import json
from types import SimpleNamespace

import pytest

from apps.backend.services.approval_service import (
    ApprovalDecision,
    ApprovalError,
    InMemoryPendingApprovalStore,
    PausedRun,
    PendingApproval,
    park_paused_run,
    pending_approvals,
)


def item(call_id="c1", name="create_ticket", arguments='{"title": "t"}', origin=None, raw=None):
    """Stand-in for agents.items.ToolApprovalItem (only the attributes the service reads)."""
    return SimpleNamespace(call_id=call_id, name=name, arguments=arguments, tool_origin=origin, raw_item=raw)


def approval(approval_id="c1"):
    return PendingApproval(id=approval_id, tool_name="create_ticket", server_name="ticketing", arguments={})


# --- PendingApproval --------------------------------------------------------


def test_from_item_parses_json_arguments_and_server_name():
    pending = PendingApproval.from_item(item(origin=SimpleNamespace(mcp_server_name="ticketing")))
    assert pending == PendingApproval("c1", "create_ticket", "ticketing", {"title": "t"})


def test_from_item_empty_arguments_become_empty_dict():
    assert PendingApproval.from_item(item(arguments=None)).arguments == {}
    assert PendingApproval.from_item(item(arguments="")).arguments == {}


def test_from_item_keeps_unparseable_arguments_raw():
    assert PendingApproval.from_item(item(arguments="{oops")).arguments == "{oops"


def test_from_item_without_call_id_is_an_error():
    with pytest.raises(ApprovalError):
        PendingApproval.from_item(item(call_id=None))


def test_from_item_without_name_is_unknown():
    assert PendingApproval.from_item(item(name=None)).tool_name == "unknown"


def test_from_item_hosted_server_label_from_dict_or_object():
    assert PendingApproval.from_item(item(raw={"server_label": "hosted-a"})).server_name == "hosted-a"
    assert PendingApproval.from_item(item(raw=SimpleNamespace(server_label="hosted-b"))).server_name == "hosted-b"


def test_from_item_without_any_server_info():
    assert PendingApproval.from_item(item(raw={})).server_name is None
    assert PendingApproval.from_item(item(raw={"server_label": 5})).server_name is None


def test_to_dict_is_json_ready():
    d = approval().to_dict()
    assert d == {"approval_id": "c1", "tool_name": "create_ticket", "server_name": "ticketing", "arguments": {}}
    json.dumps(d)


def test_pending_approvals_maps_every_interruption():
    result = pending_approvals([item("a"), item("b")])
    assert [p.id for p in result] == ["a", "b"]


# --- PausedRun.check --------------------------------------------------------


def paused(*ids):
    return PausedRun(state="{}", approvals=[approval(i) for i in ids])


def test_check_returns_decisions_by_id():
    d1, d2 = ApprovalDecision("a", True), ApprovalDecision("b", False, "no")
    assert paused("a", "b").check([d2, d1]) == {"a": d1, "b": d2}


def test_check_rejects_duplicates():
    with pytest.raises(ApprovalError, match="Duplicate"):
        paused("a").check([ApprovalDecision("a", True), ApprovalDecision("a", False)])


def test_check_rejects_unknown_id():
    with pytest.raises(ApprovalError, match="No pending approval"):
        paused("a").check([ApprovalDecision("a", True), ApprovalDecision("zzz", True)])


def test_check_rejects_missing_decision():
    with pytest.raises(ApprovalError, match="Missing decision"):
        paused("a", "b").check([ApprovalDecision("a", True)])


# --- InMemoryPendingApprovalStore ------------------------------------------


async def test_store_put_has_pop():
    store = InMemoryPendingApprovalStore()
    run = paused("a")
    assert not await store.has("s1")
    await store.put("s1", run)
    assert await store.has("s1")
    assert await store.pop("s1") is run
    assert not await store.has("s1")
    assert await store.pop("s1") is None


async def test_store_keeps_one_run_per_session():
    store = InMemoryPendingApprovalStore()
    first, second = paused("a"), paused("b")
    await store.put("s1", first)
    await store.put("s1", second)
    assert await store.pop("s1") is second


async def test_store_sessions_are_independent():
    store = InMemoryPendingApprovalStore()
    await store.put("s1", paused("a"))
    assert not await store.has("s2")


async def test_store_entries_expire(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("apps.backend.services.approval_service.time.monotonic", lambda: now[0])
    store = InMemoryPendingApprovalStore(ttl_seconds=10)
    await store.put("s1", paused("a"))
    now[0] += 9.9
    assert await store.has("s1")
    now[0] += 0.2
    assert not await store.has("s1")
    assert await store.pop("s1") is None


# --- park_paused_run --------------------------------------------------------


async def agen(*events):
    for e in events:
        yield e


async def test_park_paused_run_passes_events_through_and_stores_the_pause():
    store = InMemoryPendingApprovalStore()
    run = paused("a")
    seen = [e async for e in park_paused_run(agen("text", run), "s1", store)]
    assert seen == ["text", run]
    assert await store.pop("s1") is run


async def test_park_paused_run_stores_nothing_when_stream_completes():
    store = InMemoryPendingApprovalStore()
    seen = [e async for e in park_paused_run(agen("a", "b"), "s1", store)]
    assert seen == ["a", "b"]
    assert not await store.has("s1")
