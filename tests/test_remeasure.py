"""remeasure unit -- SPEC section 3 (remeasure_report) and D8.

Assumed input (remeasure_input): ``before`` and ``after`` snapshots
plus a ``preservation_result``.
"""

import copy

import pytest


def test_report_has_all_four_parts(unit, load_config,
                                   make_remeasure_payload):
    """D8: preservation pass / reachability / reduced / totals trend."""
    remeasure = unit("remeasure")
    result = remeasure.process(make_remeasure_payload(), load_config())
    assert result["kind"] == "remeasure_report"
    assert result["version"] == 1
    assert result["preservation_pass"] is True
    assert result["reachability"] == pytest.approx(1.0)
    assert result["reduced"] == {"code_lines": 100, "md_lines": 150}
    assert result["totals_trend"]["before"]["code_lines"] == 1000
    assert result["totals_trend"]["before"]["md_lines"] == 500
    assert result["totals_trend"]["after"]["code_lines"] == 900
    assert result["totals_trend"]["after"]["md_lines"] == 350


def test_report_passes_contracts(unit, load_config,
                                 make_remeasure_payload):
    remeasure = unit("remeasure")
    contracts = unit("contracts")
    result = remeasure.process(make_remeasure_payload(), load_config())
    assert contracts.validate_remeasure_report(result) in (None, True)


def test_failed_preservation_is_reported_as_failed(
    unit, load_config, make_remeasure_payload
):
    remeasure = unit("remeasure")
    payload = make_remeasure_payload(
        preservation_result={
            "kind": "preservation_result",
            "version": 1,
            "preservation_rate": 0.5,
            "reachability": 0.9,
            "pass": False,
        }
    )
    result = remeasure.process(payload, load_config())
    assert result["preservation_pass"] is False
    assert result["reachability"] == pytest.approx(0.9)


def test_no_change_gives_zero_reduction(unit, load_config,
                                        make_remeasure_payload, snapshot):
    remeasure = unit("remeasure")
    payload = make_remeasure_payload(
        before=copy.deepcopy(snapshot), after=copy.deepcopy(snapshot)
    )
    result = remeasure.process(payload, load_config())
    assert result["reduced"] == {"code_lines": 0, "md_lines": 0}


def test_d10_deterministic(unit, load_config, make_remeasure_payload):
    remeasure = unit("remeasure")
    payload = make_remeasure_payload()
    first = remeasure.process(copy.deepcopy(payload), load_config())
    second = remeasure.process(copy.deepcopy(payload), load_config())
    assert first == second


def test_missing_before_raises(unit, load_config, make_remeasure_payload):
    remeasure = unit("remeasure")
    payload = make_remeasure_payload()
    del payload["before"]
    with pytest.raises(ValueError):
        remeasure.process(payload, load_config())


def test_missing_preservation_result_raises(unit, load_config,
                                            make_remeasure_payload):
    remeasure = unit("remeasure")
    payload = make_remeasure_payload()
    del payload["preservation_result"]
    with pytest.raises(ValueError):
        remeasure.process(payload, load_config())


def test_invalid_snapshot_in_input_raises(unit, load_config,
                                          make_remeasure_payload):
    remeasure = unit("remeasure")
    payload = make_remeasure_payload()
    payload["after"]["totals"]["code_lines"] = "900"
    with pytest.raises(ValueError):
        remeasure.process(payload, load_config())
