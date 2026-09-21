from src.flags import FlagStore
from src.recovery import rollback
from src.rollout import Rollout


def _started_rollout():
    store = FlagStore()
    rollout = Rollout("rollout-42", "search_v2", ["acme", "globex"], store)
    rollout.apply(store)
    return store, rollout


def test_final_policy_preserves_new_flag_and_acknowledged_tenant():
    store, rollout = _started_rollout()
    store.set("acme", "billing_v2", True, actor="control-plane")
    store.set("globex", "billing_v2", True, actor="control-plane")
    rollout.acknowledge("acme")
    before_audit = len(store.audit)

    result = rollback(store, rollout)

    assert result == {"status": "partial", "restored": ["globex"], "skipped": ["acme"]}
    assert store.read("acme", "search_v2") is True
    assert store.read("globex", "search_v2") is False
    assert store.read("acme", "billing_v2") is True
    assert store.read("globex", "billing_v2") is True
    assert store.snapshot("initech") == {"search_v2": False, "billing_v2": False}
    assert len(store.audit) == before_audit + 1
    assert store.audit[-1]["actor"] == "rollback:rollout-42"
    assert store.audit[-1]["flag"] == "search_v2"


def test_partial_rollback_is_idempotent_and_reports_the_same_cohort():
    store, rollout = _started_rollout()
    rollout.acknowledge("acme")
    first = rollback(store, rollout)
    audit_count = len(store.audit)

    second = rollback(store, rollout)

    assert second == first
    assert len(store.audit) == audit_count
    assert rollout.status == "partial"
    assert first["restored"] == ["globex"]
    assert first["skipped"] == ["acme"]


def test_only_applied_tenants_are_eligible_for_compensation():
    store, rollout = _started_rollout()
    result = rollback(store, rollout)
    assert result["restored"] == ["acme", "globex"]
    assert result["skipped"] == []
    assert store.read("initech", "search_v2") is False
