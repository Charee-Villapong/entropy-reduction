"""contracts unit -- SPEC section 3 and D4.

Assumed interface: ``entropy_reduction.contracts`` exposes a generic
``validate(payload)`` (dispatching on ``kind``) plus one function per
exchange format: ``validate_snapshot``, ``validate_candidates``,
``validate_judged``, ``validate_ledger_rows``,
``validate_execution_record``, ``validate_preservation_result`` and
``validate_remeasure_report``.  A violation raises ValueError
(SPEC section 2).  A valid payload returns None or True.
"""

import copy

import pytest

from conftest import (
    CANDIDATES,
    EXECUTION_RECORD,
    FORBIDDEN_VALUE_FIELDS,
    JUDGED,
    LEDGER_ROWS,
    PRESERVATION_RESULT,
    REMEASURE_REPORT,
    SNAPSHOT,
)

VALID_BY_KIND = {
    "snapshot": SNAPSHOT,
    "candidates": CANDIDATES,
    "judged": JUDGED,
    "ledger_rows": LEDGER_ROWS,
    "execution_record": EXECUTION_RECORD,
    "preservation_result": PRESERVATION_RESULT,
    "remeasure_report": REMEASURE_REPORT,
}


def _ok(result):
    return result is None or result is True


@pytest.mark.parametrize("kind", sorted(VALID_BY_KIND))
def test_validate_accepts_each_valid_format(unit, kind):
    contracts = unit("contracts")
    assert _ok(contracts.validate(copy.deepcopy(VALID_BY_KIND[kind])))


@pytest.mark.parametrize("kind", sorted(VALID_BY_KIND))
def test_validate_specific_function_accepts_valid(unit, kind):
    contracts = unit("contracts")
    func = getattr(contracts, "validate_%s" % kind)
    assert _ok(func(copy.deepcopy(VALID_BY_KIND[kind])))


def test_validate_rejects_missing_kind(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    del payload["kind"]
    with pytest.raises(ValueError):
        contracts.validate(payload)


def test_validate_rejects_unknown_kind(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    payload["kind"] = "something_else"
    with pytest.raises(ValueError):
        contracts.validate(payload)


@pytest.mark.parametrize("bad_version", [None, 0, 2, "1"])
def test_validate_rejects_bad_version(unit, bad_version):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    if bad_version is None:
        del payload["version"]
    else:
        payload["version"] = bad_version
    with pytest.raises(ValueError):
        contracts.validate(payload)


def test_validate_rejects_non_dict_payload(unit):
    contracts = unit("contracts")
    with pytest.raises(ValueError):
        contracts.validate(["not", "a", "dict"])


# --- snapshot -------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["repos", "code_lines", "md_lines", "crons", "hooks",
                "custom_skills", "ledger_open_rows", "open_issues"]
)
def test_snapshot_missing_total_key_rejected(unit, missing):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    del payload["totals"][missing]
    with pytest.raises(ValueError):
        contracts.validate_snapshot(payload)


def test_snapshot_non_integer_total_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    payload["totals"]["code_lines"] = "200"
    with pytest.raises(ValueError):
        contracts.validate_snapshot(payload)


def test_snapshot_negative_total_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    payload["totals"]["md_lines"] = -1
    with pytest.raises(ValueError):
        contracts.validate_snapshot(payload)


@pytest.mark.parametrize("bad_date", ["2026/11/08", "08-11-2026", "x", 20261108])
def test_snapshot_bad_taken_at_rejected(unit, bad_date):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    payload["taken_at"] = bad_date
    with pytest.raises(ValueError):
        contracts.validate_snapshot(payload)


def test_snapshot_missing_sources_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(SNAPSHOT)
    del payload["sources"]
    with pytest.raises(ValueError):
        contracts.validate_snapshot(payload)


# --- candidates -----------------------------------------------------------


@pytest.mark.parametrize(
    "field,bad",
    [
        ("source", "manual"),
        ("category", "unknown"),
    ],
)
def test_candidates_invalid_enum_rejected(unit, field, bad):
    contracts = unit("contracts")
    payload = copy.deepcopy(CANDIDATES)
    payload["items"][0][field] = bad
    with pytest.raises(ValueError):
        contracts.validate_candidates(payload)


@pytest.mark.parametrize(
    "missing", ["id", "source", "target", "category", "title", "detail"]
)
def test_candidates_missing_item_field_rejected(unit, missing):
    contracts = unit("contracts")
    payload = copy.deepcopy(CANDIDATES)
    del payload["items"][0][missing]
    with pytest.raises(ValueError):
        contracts.validate_candidates(payload)


def test_candidates_must_not_carry_verdict(unit):
    """SPEC 3: the detect layer must not include any judgement field."""
    contracts = unit("contracts")
    payload = copy.deepcopy(CANDIDATES)
    payload["items"][0]["verdict"] = "delete"
    with pytest.raises(ValueError):
        contracts.validate_candidates(payload)


def test_candidates_must_not_carry_even_null_verdict(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(CANDIDATES)
    payload["items"][0]["verdict"] = None
    with pytest.raises(ValueError):
        contracts.validate_candidates(payload)


# --- judged (D3 shape) ------------------------------------------------------


def test_judged_needs_check_with_verdict_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    payload["items"][1]["verdict"] = "delete"  # needs_check must stay null
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


def test_judged_status_judged_with_null_verdict_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    payload["items"][0]["verdict"] = None
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


def test_judged_with_incomplete_evidence_rejected(unit):
    """D3: a judged item must carry all three evidence points."""
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    payload["items"][0]["evidence"]["impact"] = None
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


@pytest.mark.parametrize("evidence_key", ["last_used", "references", "impact"])
def test_judged_missing_evidence_key_rejected(unit, evidence_key):
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    del payload["items"][0]["evidence"][evidence_key]
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


def test_judged_invalid_verdict_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    payload["items"][0]["verdict"] = "archive"
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


def test_judged_invalid_status_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    payload["items"][0]["status"] = "done"
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


def test_judged_delete_with_migration_target_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    payload["items"][0]["migration_target"] = "docs/other.md"
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


def test_judged_merge_without_migration_target_rejected(unit):
    contracts = unit("contracts")
    payload = copy.deepcopy(JUDGED)
    payload["items"][0]["verdict"] = "merge"
    payload["items"][0]["migration_target"] = None
    with pytest.raises(ValueError):
        contracts.validate_judged(payload)


# --- ledger_rows ------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["task", "status", "reason", "check_method", "due_date", "link",
     "updated_at"],
)
def test_ledger_row_missing_element_rejected(unit, missing):
    contracts = unit("contracts")
    payload = copy.deepcopy(LEDGER_ROWS)
    del payload["rows"][0][missing]
    with pytest.raises(ValueError):
        contracts.validate_ledger_rows(payload)


# --- preservation_result ----------------------------------------------------


@pytest.mark.parametrize("field,bad", [("preservation_rate", 1.5),
                                       ("preservation_rate", -0.1),
                                       ("reachability", 2.0)])
def test_preservation_result_out_of_range_rejected(unit, field, bad):
    contracts = unit("contracts")
    payload = copy.deepcopy(PRESERVATION_RESULT)
    payload[field] = bad
    with pytest.raises(ValueError):
        contracts.validate_preservation_result(payload)


def test_preservation_result_pass_with_reachability_below_one_rejected(unit):
    """SPEC 3: pass requires reachability == 1.0, no exceptions."""
    contracts = unit("contracts")
    payload = copy.deepcopy(PRESERVATION_RESULT)
    payload["reachability"] = 0.9
    payload["pass"] = True
    with pytest.raises(ValueError):
        contracts.validate_preservation_result(payload)


# --- remeasure_report -------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["preservation_pass", "reachability", "reduced", "totals_trend"],
)
def test_remeasure_report_missing_part_rejected(unit, missing):
    """D8: the report always carries the same four parts."""
    contracts = unit("contracts")
    payload = copy.deepcopy(REMEASURE_REPORT)
    del payload[missing]
    with pytest.raises(ValueError):
        contracts.validate_remeasure_report(payload)


@pytest.mark.parametrize("missing", ["code_lines", "md_lines"])
def test_remeasure_report_reduced_missing_line_kind_rejected(unit, missing):
    contracts = unit("contracts")
    payload = copy.deepcopy(REMEASURE_REPORT)
    del payload["reduced"][missing]
    with pytest.raises(ValueError):
        contracts.validate_remeasure_report(payload)


# --- D4: no real values in any exchange format ------------------------------


@pytest.mark.parametrize("kind", sorted(VALID_BY_KIND))
@pytest.mark.parametrize("field", FORBIDDEN_VALUE_FIELDS)
def test_d4_top_level_real_value_field_rejected(unit, kind, field):
    contracts = unit("contracts")
    payload = copy.deepcopy(VALID_BY_KIND[kind])
    payload[field] = "SENTINEL-REAL-VALUE"
    with pytest.raises(ValueError):
        contracts.validate(payload)


@pytest.mark.parametrize(
    "kind,list_key",
    [("candidates", "items"), ("judged", "items"),
     ("ledger_rows", "rows"), ("execution_record", "items")],
)
@pytest.mark.parametrize("field", ["content", "file_content", "password",
                                   "token", "secret"])
def test_d4_nested_real_value_field_rejected(unit, kind, list_key, field):
    contracts = unit("contracts")
    payload = copy.deepcopy(VALID_BY_KIND[kind])
    payload[list_key][0][field] = "SENTINEL-REAL-VALUE"
    with pytest.raises(ValueError):
        contracts.validate(payload)
