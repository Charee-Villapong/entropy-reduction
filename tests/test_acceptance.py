"""Cross-unit acceptance tests -- SPEC section 5, D1-D10.

Unit-specific views live in the per-unit test modules; this file pins
the properties that only make sense across units: standalone execution
(D1), the full pipeline, the absence of real values everywhere (D4),
ledger rows being produced by present only (D9) and determinism of the
whole chain (D10).
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FORBIDDEN_VALUE_FIELDS, REPO_ROOT

PROCESS_UNITS = [
    "linecount",
    "measure",
    "detect",
    "judge",
    "present",
    "execute",
    "preservation",
    "remeasure",
]

DATE_KEYS = {"taken_at", "executed_at", "updated_at", "due_date"}


def _strip_dates(value):
    if isinstance(value, dict):
        return {
            k: _strip_dates(v) for k, v in value.items() if k not in DATE_KEYS
        }
    if isinstance(value, list):
        return [_strip_dates(v) for v in value]
    return value


def _all_keys(value):
    keys = set()
    if isinstance(value, dict):
        for key, sub in value.items():
            keys.add(key)
            keys |= _all_keys(sub)
    elif isinstance(value, list):
        for sub in value:
            keys |= _all_keys(sub)
    return keys


def _run_pipeline(unit, load_config, fixtures):
    measure = unit("measure")
    detect = unit("detect")
    judge = unit("judge")
    present = unit("present")
    execute = unit("execute")
    preservation = unit("preservation")
    remeasure = unit("remeasure")
    config = load_config(overrides={"preservation_pass_line": 0.7})

    snapshot = measure.process(fixtures["measure"](), config)
    candidates = detect.process(fixtures["detect"](), config)
    judged = judge.process(fixtures["judge"](), config)
    rows = present.process(copy.deepcopy(judged), config)

    exec_payload = {"kind": "ledger_rows", "version": 1, "rows": []}
    for item in judged["items"]:
        if item["status"] != "judged":
            continue
        exec_payload["rows"].append(
            {
                "candidate_id": item["candidate_id"],
                "task": item["candidate_id"],
                "status": "未着手",
                "reason": "",
                "check_method": "",
                "due_date": "2026-11-15",
                "link": item["candidate_id"],
                "updated_at": "2026-11-08",
                "target": item["candidate_id"],
                "action": item["verdict"],
                "user_decision": item["verdict"],
            }
        )
    record = execute.process(exec_payload, config)
    pres_result = preservation.process(fixtures["preservation"](), config)
    report = remeasure.process(
        fixtures["remeasure"](before=snapshot, after=snapshot,
                              preservation_result=pres_result),
        config,
    )
    return {
        "snapshot": snapshot,
        "candidates": candidates,
        "judged": judged,
        "rows": rows,
        "record": record,
        "preservation": pres_result,
        "report": report,
    }


@pytest.fixture
def pipeline_fixtures(make_measure_payload, make_detect_payload,
                      make_judge_payload, make_preservation_payload,
                      make_remeasure_payload):
    return {
        "measure": make_measure_payload,
        "detect": make_detect_payload,
        "judge": make_judge_payload,
        "preservation": make_preservation_payload,
        "remeasure": make_remeasure_payload,
    }


# --- D1: loose coupling -----------------------------------------------------


@pytest.mark.parametrize("name", PROCESS_UNITS)
def test_d1_each_unit_imports_standalone_and_has_process(name):
    """D1: each unit loads in a fresh interpreter on its own and
    exposes a callable process()."""
    pythonpath = [str(REPO_ROOT)]
    src = REPO_ROOT / "src"
    if src.is_dir():
        pythonpath.append(str(src))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import entropy_reduction.%s as m; "
            "assert callable(m.process)" % name,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_d1_config_and_contracts_standalone_interfaces(unit):
    config_mod = unit("config")
    contracts = unit("contracts")
    assert callable(config_mod.load_config)
    assert callable(contracts.validate)


def test_d1_outputs_are_plain_json(unit, load_config, pipeline_fixtures):
    """D1: exchange happens as JSON, so every output must serialise."""
    outputs = _run_pipeline(unit, load_config, pipeline_fixtures)
    for name, output in outputs.items():
        json.dumps(output, ensure_ascii=False), name


# --- Pipeline ----------------------------------------------------------------


def test_pipeline_end_to_end(unit, load_config, pipeline_fixtures):
    outputs = _run_pipeline(unit, load_config, pipeline_fixtures)
    assert outputs["snapshot"]["kind"] == "snapshot"
    assert outputs["candidates"]["kind"] == "candidates"
    assert outputs["judged"]["kind"] == "judged"
    assert outputs["rows"]["kind"] == "ledger_rows"
    assert outputs["record"]["kind"] == "execution_record"
    assert outputs["preservation"]["kind"] == "preservation_result"
    assert outputs["report"]["kind"] == "remeasure_report"
    # The pipeline actually executed the judged candidate(s).
    assert outputs["record"]["counts"]["executed"] >= 1
    assert outputs["report"]["preservation_pass"] is True


def test_pipeline_outputs_all_pass_contracts(unit, load_config,
                                             pipeline_fixtures):
    contracts = unit("contracts")
    outputs = _run_pipeline(unit, load_config, pipeline_fixtures)
    validators = {
        "snapshot": contracts.validate_snapshot,
        "candidates": contracts.validate_candidates,
        "judged": contracts.validate_judged,
        "rows": contracts.validate_ledger_rows,
        "record": contracts.validate_execution_record,
        "preservation": contracts.validate_preservation_result,
        "report": contracts.validate_remeasure_report,
    }
    for name, validate in validators.items():
        assert validate(outputs[name]) in (None, True), name


# --- D4 ---------------------------------------------------------------------


def test_d4_no_real_value_fields_anywhere_in_pipeline(
    unit, load_config, pipeline_fixtures
):
    outputs = _run_pipeline(unit, load_config, pipeline_fixtures)
    for name, output in outputs.items():
        leaked = _all_keys(output) & set(FORBIDDEN_VALUE_FIELDS)
        assert leaked == set(), "%s leaks %s" % (name, leaked)


def test_d4_no_sentinel_content_survives_into_candidates(
    unit, load_config, make_detect_payload
):
    """Even if an input item carried file content, detect output --
    an exchange format -- must not reproduce it."""
    detect = unit("detect")
    payload = make_detect_payload(
        items=[
            {
                "target": "docs/big.md",
                "file_type": "md",
                "md_lines": 450,
                "code_lines": 0,
                "last_used_days_ago": 10,
                "signature": "sig-big",
                "content": "SENTINEL-REAL-VALUE",
            }
        ]
    )
    result = detect.process(payload, load_config())
    assert "SENTINEL-REAL-VALUE" not in json.dumps(
        result, ensure_ascii=False
    )


# --- D9 ---------------------------------------------------------------------


def test_d9_only_present_produces_ledger_rows(unit, load_config,
                                              pipeline_fixtures):
    outputs = _run_pipeline(unit, load_config, pipeline_fixtures)
    for name in ("snapshot", "candidates", "judged", "record",
                 "preservation", "report"):
        assert outputs[name]["kind"] != "ledger_rows", name
        assert "rows" not in outputs[name], name
    assert outputs["rows"]["kind"] == "ledger_rows"


# --- D10 --------------------------------------------------------------------


def test_d10_pipeline_is_deterministic_except_dates(
    unit, load_config, pipeline_fixtures
):
    first = _run_pipeline(unit, load_config, pipeline_fixtures)
    second = _run_pipeline(unit, load_config, pipeline_fixtures)
    assert _strip_dates(first) == _strip_dates(second)


# --- D2 / D3 at pipeline level ------------------------------------------------


def test_d2_pipeline_execute_refuses_rows_built_without_decision(
    unit, load_config, judged_payload, make_execute_payload
):
    """Rows straight out of present carry no user_decision; feeding
    them to execute must be refused wholesale (D2)."""
    present = unit("present")
    execute = unit("execute")
    rows = present.process(judged_payload, load_config())
    with pytest.raises(ValueError):
        execute.process(rows, load_config())


def test_d3_pipeline_needs_check_candidate_cannot_be_executed(
    unit, load_config, pipeline_fixtures
):
    outputs = _run_pipeline(unit, load_config, pipeline_fixtures)
    executed_ids = {
        item["candidate_id"] for item in outputs["record"]["items"]
    }
    needs_check_ids = {
        item["candidate_id"]
        for item in outputs["judged"]["items"]
        if item["status"] == "needs_check"
    }
    # The default judge payload is fully evidenced, so this set is
    # empty here; the invariant is that the two sets never overlap.
    assert executed_ids.isdisjoint(needs_check_ids)


def test_d5_repeated_pipeline_measure_does_not_grow_totals(
    unit, load_config, make_measure_payload
):
    """Running measure twice over the same inputs is idempotent."""
    measure = unit("measure")
    payload = make_measure_payload()
    first = measure.process(copy.deepcopy(payload), load_config())
    second = measure.process(copy.deepcopy(payload), load_config())
    assert first["totals"] == second["totals"]
