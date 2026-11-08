"""execute unit -- SPEC section 3 (execution_record) and D2, D4.

Assumed input: the ``ledger_rows`` format whose rows have gained the
user's decision as ``user_decision`` (SPEC D2 names this field), plus
``candidate_id`` and ``action`` per row.  "Rejects everything" is
observed as: process raises ValueError and returns no execution record
(there is no other channel through which a partial execution could be
reported by this pure unit).
"""

import copy

import pytest


def test_all_decided_rows_are_executed(unit, load_config,
                                       make_execute_payload):
    execute = unit("execute")
    result = execute.process(make_execute_payload(), load_config())
    assert result["kind"] == "execution_record"
    assert result["version"] == 1
    assert result["executed_at"]
    items = {i["candidate_id"]: i for i in result["items"]}
    assert set(items) == {"cand-1", "cand-2"}
    assert items["cand-1"]["action"] == "delete"
    assert items["cand-2"]["action"] == "merge"
    for item in items.values():
        assert item["result"] == "done"
    assert result["counts"]["total"] == 2
    assert result["counts"]["executed"] == 2


def test_execution_record_passes_contracts(unit, load_config,
                                           make_execute_payload):
    execute = unit("execute")
    contracts = unit("contracts")
    result = execute.process(make_execute_payload(), load_config())
    assert contracts.validate_execution_record(result) in (None, True)


def test_execution_record_contains_only_metadata(unit, load_config,
                                                 make_execute_payload):
    """D4: items carry candidate_id / action / result only."""
    execute = unit("execute")
    result = execute.process(make_execute_payload(), load_config())
    for item in result["items"]:
        assert set(item.keys()) == {"candidate_id", "action", "result"}


# --- D2: the execution guard (thick) ---------------------------------------


def _with_bad_row(make_execute_payload, bad_row):
    payload = make_execute_payload()
    payload["rows"].append(bad_row)
    return payload


def _base_bad_row(**overrides):
    row = {
        "candidate_id": "cand-3",
        "task": "未仕分けの行",
        "status": "未着手",
        "reason": "",
        "check_method": "",
        "due_date": "2026-11-15",
        "link": "cand-3",
        "updated_at": "2026-11-08",
        "target": "docs/undecided.md",
        "action": "delete",
    }
    row.update(overrides)
    return row


def test_d2_row_without_user_decision_key_rejects_everything(
    unit, load_config, make_execute_payload
):
    execute = unit("execute")
    payload = _with_bad_row(make_execute_payload, _base_bad_row())
    with pytest.raises(ValueError):
        execute.process(payload, load_config())


@pytest.mark.parametrize("bad_decision", [None, "", "   "])
def test_d2_blank_user_decision_rejects_everything(
    unit, load_config, make_execute_payload, bad_decision
):
    execute = unit("execute")
    payload = _with_bad_row(
        make_execute_payload,
        _base_bad_row(user_decision=bad_decision),
    )
    with pytest.raises(ValueError):
        execute.process(payload, load_config())


def test_d2_unknown_user_decision_rejects_everything(
    unit, load_config, make_execute_payload
):
    execute = unit("execute")
    payload = _with_bad_row(
        make_execute_payload, _base_bad_row(user_decision="maybe")
    )
    with pytest.raises(ValueError):
        execute.process(payload, load_config())


def test_d2_single_undecided_row_among_many_rejects_everything(
    unit, load_config, make_execute_payload
):
    """Even one undecided row in the middle of decided rows stops all."""
    execute = unit("execute")
    payload = make_execute_payload()
    middle = _base_bad_row(candidate_id="cand-x")
    payload["rows"].insert(1, middle)
    with pytest.raises(ValueError):
        execute.process(payload, load_config())


def test_d2_decision_must_match_a_known_action(unit, load_config,
                                               make_execute_payload):
    """A decision that is not one of delete/merge/codify/keep is not a
    user sorting decision and must be rejected, not executed."""
    execute = unit("execute")
    payload = make_execute_payload()
    payload["rows"][0]["user_decision"] = "postpone"
    with pytest.raises(ValueError):
        execute.process(payload, load_config())


@pytest.mark.parametrize("decision", ["delete", "merge", "codify"])
def test_d2_each_actionable_decision_is_accepted(
    unit, load_config, make_execute_payload, decision
):
    execute = unit("execute")
    payload = make_execute_payload()
    payload["rows"] = payload["rows"][:1]
    payload["rows"][0]["user_decision"] = decision
    payload["rows"][0]["action"] = decision
    result = execute.process(payload, load_config())
    assert result["items"][0]["action"] == decision


def test_keep_decision_is_recorded_not_executed_as_deletion(
    unit, load_config, make_execute_payload
):
    execute = unit("execute")
    payload = make_execute_payload()
    payload["rows"] = payload["rows"][:1]
    payload["rows"][0]["user_decision"] = "keep"
    payload["rows"][0]["action"] = "keep"
    result = execute.process(payload, load_config())
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "keep"
    assert result["items"][0]["result"] in ("kept", "skipped", "done")


def test_duplicate_candidate_rows_rejected(unit, load_config,
                                           make_execute_payload):
    """The same candidate must not be executed twice in one run."""
    execute = unit("execute")
    payload = make_execute_payload()
    payload["rows"].append(copy.deepcopy(payload["rows"][0]))
    with pytest.raises(ValueError):
        execute.process(payload, load_config())


def test_empty_rows_rejected(unit, load_config, make_execute_payload):
    execute = unit("execute")
    with pytest.raises(ValueError):
        execute.process(make_execute_payload(rows=[]), load_config())


def test_d10_deterministic_except_executed_at(unit, load_config,
                                              make_execute_payload):
    execute = unit("execute")
    payload = make_execute_payload()
    first = execute.process(copy.deepcopy(payload), load_config())
    second = execute.process(copy.deepcopy(payload), load_config())
    first.pop("executed_at")
    second.pop("executed_at")
    assert first == second
