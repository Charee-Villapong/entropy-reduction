"""judge unit -- SPEC section 3 (judged) and D3.

Assumed input (judge_input): ``candidates`` plus ``evidence`` keyed by
candidate id.  Each evidence entry carries the three evidence points
(``last_used`` / ``references`` / ``impact``) and the proposed judgement
(``proposed_verdict`` / ``migration_target`` / ``reason``).  How the
verdict itself is produced by the real (agent) judge is a SPEC question;
for this pure unit the proposal is part of the input, and the unit's
contract under test is the evidence gate of D3.
"""

import copy

import pytest

from conftest import FULL_EVIDENCE_DELETE, FULL_EVIDENCE_MERGE


def _by_id(result):
    return {item["candidate_id"]: item for item in result["items"]}


def test_full_evidence_candidates_are_judged(unit, load_config,
                                             make_judge_payload):
    judge = unit("judge")
    result = judge.process(make_judge_payload(), load_config())
    assert result["kind"] == "judged"
    assert result["version"] == 1
    items = _by_id(result)
    assert items["cand-1"]["status"] == "judged"
    assert items["cand-1"]["verdict"] == "delete"
    assert items["cand-1"]["migration_target"] is None
    assert items["cand-1"]["reason"]
    assert items["cand-2"]["status"] == "judged"
    assert items["cand-2"]["verdict"] == "merge"
    assert items["cand-2"]["migration_target"] == "tools/dup-a.py"
    # Every candidate is accounted for exactly once.
    assert set(items) == {"cand-1", "cand-2"}
    assert len(result["items"]) == 2


def test_judged_evidence_is_carried_through(unit, load_config,
                                            make_judge_payload):
    judge = unit("judge")
    result = judge.process(make_judge_payload(), load_config())
    evidence = _by_id(result)["cand-1"]["evidence"]
    assert evidence["last_used"] == "2026-08-01"
    assert evidence["references"] == ["docs/index.md"]
    assert evidence["impact"]


def test_empty_references_list_still_counts_as_evidence(
    unit, load_config, make_judge_payload
):
    """An empty reference list is itself evidence (nothing references
    it); cand-2 in the default payload relies on this."""
    judge = unit("judge")
    result = judge.process(make_judge_payload(), load_config())
    assert _by_id(result)["cand-2"]["status"] == "judged"


@pytest.mark.parametrize("missing_point", ["last_used", "references",
                                           "impact"])
def test_d3_missing_evidence_point_becomes_needs_check(
    unit, load_config, make_judge_payload, missing_point
):
    """D3 (thick): one missing evidence point -> needs_check, verdict null."""
    judge = unit("judge")
    evidence = {
        "cand-1": copy.deepcopy(FULL_EVIDENCE_DELETE),
        "cand-2": copy.deepcopy(FULL_EVIDENCE_MERGE),
    }
    del evidence["cand-1"][missing_point]
    result = judge.process(
        make_judge_payload(evidence=evidence), load_config()
    )
    item = _by_id(result)["cand-1"]
    assert item["status"] == "needs_check"
    assert item["verdict"] is None
    assert item["migration_target"] is None
    # The other, fully evidenced candidate is unaffected.
    assert _by_id(result)["cand-2"]["status"] == "judged"


@pytest.mark.parametrize("missing_point", ["last_used", "references",
                                           "impact"])
def test_d3_null_evidence_point_becomes_needs_check(
    unit, load_config, make_judge_payload, missing_point
):
    """D3: a null evidence point counts as missing."""
    judge = unit("judge")
    evidence = {
        "cand-1": copy.deepcopy(FULL_EVIDENCE_DELETE),
        "cand-2": copy.deepcopy(FULL_EVIDENCE_MERGE),
    }
    evidence["cand-1"][missing_point] = None
    result = judge.process(
        make_judge_payload(evidence=evidence), load_config()
    )
    item = _by_id(result)["cand-1"]
    assert item["status"] == "needs_check"
    assert item["verdict"] is None


def test_d3_candidate_without_any_evidence_becomes_needs_check(
    unit, load_config, make_judge_payload
):
    judge = unit("judge")
    evidence = {"cand-2": copy.deepcopy(FULL_EVIDENCE_MERGE)}
    result = judge.process(
        make_judge_payload(evidence=evidence), load_config()
    )
    item = _by_id(result)["cand-1"]
    assert item["status"] == "needs_check"
    assert item["verdict"] is None


def test_d3_complete_evidence_without_proposal_becomes_needs_check(
    unit, load_config, make_judge_payload
):
    """Evidence alone, with no proposed verdict, cannot be judged."""
    judge = unit("judge")
    entry = copy.deepcopy(FULL_EVIDENCE_DELETE)
    del entry["proposed_verdict"]
    result = judge.process(
        make_judge_payload(
            evidence={
                "cand-1": entry,
                "cand-2": copy.deepcopy(FULL_EVIDENCE_MERGE),
            }
        ),
        load_config(),
    )
    item = _by_id(result)["cand-1"]
    assert item["status"] == "needs_check"
    assert item["verdict"] is None


def test_judged_output_passes_contracts(unit, load_config,
                                        make_judge_payload):
    judge = unit("judge")
    contracts = unit("contracts")
    result = judge.process(make_judge_payload(), load_config())
    assert contracts.validate_judged(result) in (None, True)


def test_invalid_proposed_verdict_raises(unit, load_config,
                                         make_judge_payload):
    judge = unit("judge")
    entry = copy.deepcopy(FULL_EVIDENCE_DELETE)
    entry["proposed_verdict"] = "archive"
    with pytest.raises(ValueError):
        judge.process(
            make_judge_payload(
                evidence={
                    "cand-1": entry,
                    "cand-2": copy.deepcopy(FULL_EVIDENCE_MERGE),
                }
            ),
            load_config(),
        )


def test_merge_without_migration_target_raises(unit, load_config,
                                               make_judge_payload):
    judge = unit("judge")
    entry = copy.deepcopy(FULL_EVIDENCE_MERGE)
    entry["migration_target"] = None
    with pytest.raises(ValueError):
        judge.process(
            make_judge_payload(
                evidence={
                    "cand-1": copy.deepcopy(FULL_EVIDENCE_DELETE),
                    "cand-2": entry,
                }
            ),
            load_config(),
        )


def test_evidence_for_unknown_candidate_raises(unit, load_config,
                                               make_judge_payload):
    judge = unit("judge")
    evidence = {
        "cand-1": copy.deepcopy(FULL_EVIDENCE_DELETE),
        "cand-2": copy.deepcopy(FULL_EVIDENCE_MERGE),
        "cand-999": copy.deepcopy(FULL_EVIDENCE_DELETE),
    }
    with pytest.raises(ValueError):
        judge.process(
            make_judge_payload(evidence=evidence), load_config()
        )


def test_candidates_carrying_verdict_are_rejected(unit, load_config,
                                                  make_judge_payload,
                                                  candidates_payload):
    """Judge input must be pure detect output; a pre-judged candidate
    violates the layer separation."""
    judge = unit("judge")
    candidates_payload["items"][0]["verdict"] = "delete"
    with pytest.raises(ValueError):
        judge.process(
            make_judge_payload(candidates=candidates_payload),
            load_config(),
        )


def test_d10_deterministic(unit, load_config, make_judge_payload):
    judge = unit("judge")
    payload = make_judge_payload()
    first = judge.process(copy.deepcopy(payload), load_config())
    second = judge.process(copy.deepcopy(payload), load_config())
    assert first == second
