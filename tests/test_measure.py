"""measure unit -- SPEC section 3 (snapshot) and D5.

Assumed input (measure_input): ``taken_at``, ``targets`` (each with
``name`` / ``code_lines`` / ``md_lines``) and ``counts`` (``crons`` /
``hooks`` / ``custom_skills`` / ``ledger_open_rows`` / ``open_issues``).
``totals.repos`` is the number of distinct target names.  Inventory run
state files are read from the directory given by the config key
``harness_state_dir`` and surface in ``snapshot.sources``.
"""

import copy

import pytest

from conftest import FIXTURES


def test_snapshot_shape_and_totals(unit, load_config, make_measure_payload):
    measure = unit("measure")
    result = measure.process(make_measure_payload(), load_config())
    assert result["kind"] == "snapshot"
    assert result["version"] == 1
    assert result["taken_at"] == "2026-11-08"
    assert result["totals"] == {
        "repos": 2,
        "code_lines": 200,
        "md_lines": 60,
        "crons": 3,
        "hooks": 2,
        "custom_skills": 4,
        "ledger_open_rows": 5,
        "open_issues": 6,
    }
    assert isinstance(result["sources"], list)


def test_snapshot_result_passes_contracts(unit, load_config,
                                          make_measure_payload):
    measure = unit("measure")
    contracts = unit("contracts")
    result = measure.process(make_measure_payload(), load_config())
    assert contracts.validate_snapshot(result) in (None, True)


def test_d5_duplicate_target_counted_once(unit, load_config,
                                          make_measure_payload):
    """D5: the same target name must never be counted twice."""
    measure = unit("measure")
    payload = make_measure_payload(
        targets=[
            {"name": "repo-a", "code_lines": 120, "md_lines": 40},
            {"name": "repo-a", "code_lines": 120, "md_lines": 40},
            {"name": "repo-a", "code_lines": 120, "md_lines": 40},
            {"name": "repo-b", "code_lines": 80, "md_lines": 20},
        ]
    )
    result = measure.process(payload, load_config())
    assert result["totals"]["repos"] == 2
    assert result["totals"]["code_lines"] == 200
    assert result["totals"]["md_lines"] == 60


def test_d5_duplicate_target_with_different_counts_first_wins(
    unit, load_config, make_measure_payload
):
    """Assumption: for a repeated name the first occurrence is kept."""
    measure = unit("measure")
    payload = make_measure_payload(
        targets=[
            {"name": "repo-a", "code_lines": 120, "md_lines": 40},
            {"name": "repo-a", "code_lines": 999, "md_lines": 999},
        ]
    )
    result = measure.process(payload, load_config())
    assert result["totals"]["repos"] == 1
    assert result["totals"]["code_lines"] == 120
    assert result["totals"]["md_lines"] == 40


def test_harness_run_state_is_read_into_sources(unit, load_config,
                                                 make_measure_payload):
    """D5: inventory (harness) run state files are accepted as input."""
    measure = unit("measure")
    config = load_config(
        overrides={"harness_state_dir": str(FIXTURES / "harness_state")}
    )
    result = measure.process(make_measure_payload(), config)
    harness_sources = [
        s for s in result["sources"] if s.get("type") == "harness_run"
    ]
    assert len(harness_sources) == 1
    assert harness_sources[0]["run_id"] == "run-20261108"
    assert harness_sources[0]["candidates_by_state"] == {
        "needs_review": 2,
        "done": 5,
    }


def test_no_harness_state_means_no_harness_source(unit, load_config,
                                                  make_measure_payload):
    measure = unit("measure")
    result = measure.process(make_measure_payload(), load_config())
    assert [
        s for s in result["sources"] if s.get("type") == "harness_run"
    ] == []


def test_empty_targets_give_zero_repo_totals(unit, load_config,
                                             make_measure_payload):
    measure = unit("measure")
    result = measure.process(
        make_measure_payload(targets=[]), load_config()
    )
    assert result["totals"]["repos"] == 0
    assert result["totals"]["code_lines"] == 0
    assert result["totals"]["md_lines"] == 0
    # Non-repo counts still come through.
    assert result["totals"]["crons"] == 3


def test_missing_counts_keys_default_to_zero(unit, load_config,
                                             make_measure_payload):
    measure = unit("measure")
    result = measure.process(
        make_measure_payload(counts={}), load_config()
    )
    for key in ("crons", "hooks", "custom_skills", "ledger_open_rows",
                "open_issues"):
        assert result["totals"][key] == 0


def test_taken_at_is_carried_through(unit, load_config,
                                     make_measure_payload):
    measure = unit("measure")
    result = measure.process(
        make_measure_payload(taken_at="2026-12-01"), load_config()
    )
    assert result["taken_at"] == "2026-12-01"


def test_d10_deterministic_output(unit, load_config, make_measure_payload):
    measure = unit("measure")
    payload = make_measure_payload()
    first = measure.process(copy.deepcopy(payload), load_config())
    second = measure.process(copy.deepcopy(payload), load_config())
    assert first == second


def test_missing_targets_raises(unit, load_config, make_measure_payload):
    measure = unit("measure")
    payload = make_measure_payload()
    del payload["targets"]
    with pytest.raises(ValueError):
        measure.process(payload, load_config())


def test_target_missing_name_raises(unit, load_config,
                                    make_measure_payload):
    measure = unit("measure")
    payload = make_measure_payload(
        targets=[{"code_lines": 10, "md_lines": 5}]
    )
    with pytest.raises(ValueError):
        measure.process(payload, load_config())


def test_negative_line_count_raises(unit, load_config,
                                    make_measure_payload):
    measure = unit("measure")
    payload = make_measure_payload(
        targets=[{"name": "repo-a", "code_lines": -1, "md_lines": 0}]
    )
    with pytest.raises(ValueError):
        measure.process(payload, load_config())


def test_non_integer_line_count_raises(unit, load_config,
                                       make_measure_payload):
    measure = unit("measure")
    payload = make_measure_payload(
        targets=[{"name": "repo-a", "code_lines": "120", "md_lines": 0}]
    )
    with pytest.raises(ValueError):
        measure.process(payload, load_config())
