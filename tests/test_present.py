"""present unit -- SPEC section 3 (ledger_rows) and D9.

Assumed input: the ``judged`` exchange format itself.  Row keys are
assumed to be the English names ``task`` / ``status`` / ``reason`` /
``check_method`` / ``due_date`` / ``link`` / ``updated_at`` for the seven
ledger elements named in SPEC section 3 (タスク／状態／止まってる理由・
前提条件／前提の確認方法／期日・次回確認日／関連リンク／最終更新).
Tests that check the needs_check marker search all row values, so they
do not depend on which element carries the marker.

Spec change (2026-11-08, the user's decision): a candidate judged as
verdict=keep is out of scope (対象外) -- it does NOT become a ledger
row; only its count is reported.  This supersedes the original P0
premise "every candidate becomes a row" for keep candidates.  The
fixture below contains no keep candidate, so the row-count test keeps
its original form; the new premise is pinned by
test_keep_verdict_candidate_is_counted_not_rowed.
"""

import copy

import pytest

NEEDS_CHECK_MARKER = "要確認（証拠不足）"


def _row_text(row):
    return " ".join(str(v) for v in row.values())


def test_all_candidates_become_rows(unit, load_config, judged_payload):
    """D9: judged and needs_check candidates alike become ledger rows.

    (The fixture holds no verdict=keep candidate; keep candidates are
    excluded from rows per the 2026-11-08 spec change.)
    """
    present = unit("present")
    result = present.process(judged_payload, load_config())
    assert result["kind"] == "ledger_rows"
    assert result["version"] == 1
    assert len(result["rows"]) == len(judged_payload["items"])


def test_rows_have_seven_elements(unit, load_config, judged_payload):
    present = unit("present")
    result = present.process(judged_payload, load_config())
    for row in result["rows"]:
        assert len(row) == 7


def test_rows_identify_the_candidate(unit, load_config, judged_payload):
    present = unit("present")
    result = present.process(judged_payload, load_config())
    texts = [_row_text(row) for row in result["rows"]]
    assert any(
        "cand-1" in t or "docs/big.md" in t for t in texts
    )
    assert any(
        "cand-2" in t or "tools/dup-b.py" in t for t in texts
    )


def test_needs_check_row_is_marked(unit, load_config, judged_payload):
    """D9 / SPEC 3: needs_check rows must say 要確認（証拠不足）. """
    present = unit("present")
    result = present.process(judged_payload, load_config())
    needs_rows = [
        row
        for row in result["rows"]
        if "cand-2" in _row_text(row) or "tools/dup-b.py" in _row_text(row)
    ]
    assert len(needs_rows) == 1
    assert NEEDS_CHECK_MARKER in _row_text(needs_rows[0])


def test_judged_row_is_not_marked_as_needs_check(unit, load_config,
                                                 judged_payload):
    present = unit("present")
    result = present.process(judged_payload, load_config())
    judged_rows = [
        row
        for row in result["rows"]
        if "cand-1" in _row_text(row) or "docs/big.md" in _row_text(row)
    ]
    assert len(judged_rows) == 1
    assert NEEDS_CHECK_MARKER not in _row_text(judged_rows[0])


def test_present_output_passes_contracts(unit, load_config,
                                         judged_payload):
    present = unit("present")
    contracts = unit("contracts")
    result = present.process(judged_payload, load_config())
    assert contracts.validate_ledger_rows(result) in (None, True)


def test_empty_judged_gives_empty_rows(unit, load_config):
    present = unit("present")
    result = present.process(
        {"kind": "judged", "version": 1, "items": []}, load_config()
    )
    assert result["kind"] == "ledger_rows"
    assert result["rows"] == []


def test_d10_deterministic_except_dates(unit, load_config,
                                        judged_payload):
    present = unit("present")
    first = present.process(copy.deepcopy(judged_payload), load_config())
    second = present.process(copy.deepcopy(judged_payload), load_config())
    for rows in (first["rows"], second["rows"]):
        for row in rows:
            row.pop("updated_at", None)
            row.pop("due_date", None)
    assert first == second


def test_wrong_input_kind_raises(unit, load_config, candidates_payload):
    present = unit("present")
    with pytest.raises(ValueError):
        present.process(candidates_payload, load_config())


def test_missing_items_raises(unit, load_config):
    present = unit("present")
    with pytest.raises(ValueError):
        present.process({"kind": "judged", "version": 1}, load_config())


def test_keep_verdict_candidate_is_counted_not_rowed(
    unit, load_config, judged_payload
):
    """Spec change (2026-11-08, user's decision): verdict=keep is
    対象外 -- no ledger row, only the count is reported."""
    present = unit("present")
    payload = copy.deepcopy(judged_payload)
    payload["items"].append(
        {
            "candidate_id": "cand-3",
            "status": "judged",
            "evidence": {
                "last_used": "2026-11-01",
                "references": ["docs/index.md"],
                "impact": "現役で参照も多い",
            },
            "verdict": "keep",
            "migration_target": None,
            "reason": "現役のため保持",
        }
    )
    result = present.process(payload, load_config())
    texts = [_row_text(row) for row in result["rows"]]
    assert len(result["rows"]) == 2
    assert not any("cand-3" in t for t in texts)
    assert result["excluded_count"] == 1
