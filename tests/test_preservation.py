"""preservation unit -- SPEC section 3 (preservation_result), D6, D7.

Assumed input (preservation_input): ``questions`` (each with ``id`` /
``category`` / ``item_id``; category may be ``accident`` / ``exception``
/ other), ``answers`` (each with ``question_id`` / ``correct``) and
``item_locations`` (item id -> list of locations, ``md`` and/or
``code``; an empty list or a missing entry means unreachable).
"""

import pytest


def test_rates_are_computed(unit, load_config, make_preservation_payload):
    preservation = unit("preservation")
    result = preservation.process(
        make_preservation_payload(),
        load_config(overrides={"preservation_pass_line": 0.7}),
    )
    assert result["kind"] == "preservation_result"
    assert result["version"] == 1
    # 3 of 4 answers correct.
    assert result["preservation_rate"] == pytest.approx(0.75)
    # All four items are reachable from md or code.
    assert result["reachability"] == pytest.approx(1.0)
    assert result["pass"] is True


def test_result_passes_contracts(unit, load_config,
                                 make_preservation_payload):
    preservation = unit("preservation")
    contracts = unit("contracts")
    result = preservation.process(
        make_preservation_payload(), load_config()
    )
    assert contracts.validate_preservation_result(result) in (None, True)


def test_d7_rate_below_pass_line_fails(unit, load_config,
                                       make_preservation_payload):
    """Default config pass line is 1.0 (config.example.json), so a
    0.75 rate must fail even with perfect reachability."""
    preservation = unit("preservation")
    result = preservation.process(
        make_preservation_payload(), load_config()
    )
    assert result["preservation_rate"] == pytest.approx(0.75)
    assert result["pass"] is False


def test_d7_rate_exactly_at_pass_line_passes(unit, load_config,
                                             make_preservation_payload):
    """SPEC 3: pass is rate >= pass line (以上)."""
    preservation = unit("preservation")
    result = preservation.process(
        make_preservation_payload(),
        load_config(overrides={"preservation_pass_line": 0.75}),
    )
    assert result["pass"] is True


def test_d7_reachability_below_one_fails_even_with_perfect_rate(
    unit, load_config, make_preservation_payload
):
    """D7: reachability must be exactly 100% -- no partial credit."""
    preservation = unit("preservation")
    payload = make_preservation_payload(
        answers=[
            {"question_id": "q1", "correct": True},
            {"question_id": "q2", "correct": True},
            {"question_id": "q3", "correct": True},
            {"question_id": "q4", "correct": True},
        ],
        item_locations={
            "rule-1": ["md"],
            "rule-2": ["code"],
            "rule-3": ["md"],
            "rule-4": [],
        },
    )
    result = preservation.process(
        payload, load_config(overrides={"preservation_pass_line": 0.5})
    )
    assert result["preservation_rate"] == pytest.approx(1.0)
    assert result["reachability"] == pytest.approx(0.75)
    assert result["pass"] is False


def test_item_missing_from_locations_counts_as_unreachable(
    unit, load_config, make_preservation_payload
):
    preservation = unit("preservation")
    payload = make_preservation_payload(
        item_locations={
            "rule-1": ["md"],
            "rule-2": ["code"],
            "rule-3": ["md"],
            # rule-4 absent entirely
        }
    )
    result = preservation.process(payload, load_config())
    assert result["reachability"] == pytest.approx(0.75)
    assert result["pass"] is False


def test_codified_item_counts_as_reachable(unit, load_config,
                                           make_preservation_payload):
    """Reachability includes items that only survive as code (符号化)."""
    preservation = unit("preservation")
    payload = make_preservation_payload(
        item_locations={
            "rule-1": ["code"],
            "rule-2": ["code"],
            "rule-3": ["code"],
            "rule-4": ["code"],
        }
    )
    result = preservation.process(payload, load_config())
    assert result["reachability"] == pytest.approx(1.0)


def test_d6_pass_line_comes_from_config(unit, load_config,
                                        make_preservation_payload):
    """D6: same answers, different configured line -> different pass."""
    preservation = unit("preservation")
    payload = make_preservation_payload()
    strict = preservation.process(copy_payload(payload), load_config())
    loose = preservation.process(
        copy_payload(payload),
        load_config(overrides={"preservation_pass_line": 0.7}),
    )
    assert strict["pass"] is False
    assert loose["pass"] is True
    assert strict["preservation_rate"] == loose["preservation_rate"]


def copy_payload(payload):
    import copy

    return copy.deepcopy(payload)


# --- D7: the question set must include accident/exception questions ------


def test_d7_question_set_without_accident_or_exception_is_error(
    unit, load_config, make_preservation_payload
):
    preservation = unit("preservation")
    payload = make_preservation_payload(
        questions=[
            {"id": "q1", "category": "normal", "item_id": "rule-1"},
            {"id": "q2", "category": "normal", "item_id": "rule-2"},
        ],
        answers=[
            {"question_id": "q1", "correct": True},
            {"question_id": "q2", "correct": True},
        ],
        item_locations={"rule-1": ["md"], "rule-2": ["md"]},
    )
    with pytest.raises(ValueError):
        preservation.process(payload, load_config())


def test_d7_accident_only_question_set_is_accepted(
    unit, load_config, make_preservation_payload
):
    """One accident question is enough; exception is not also required."""
    preservation = unit("preservation")
    payload = make_preservation_payload(
        questions=[
            {"id": "q1", "category": "accident", "item_id": "rule-1"},
            {"id": "q2", "category": "normal", "item_id": "rule-2"},
        ],
        answers=[
            {"question_id": "q1", "correct": True},
            {"question_id": "q2", "correct": True},
        ],
        item_locations={"rule-1": ["md"], "rule-2": ["code"]},
    )
    result = preservation.process(payload, load_config())
    assert result["preservation_rate"] == pytest.approx(1.0)
    assert result["pass"] is True


def test_d7_exception_only_question_set_is_accepted(
    unit, load_config, make_preservation_payload
):
    preservation = unit("preservation")
    payload = make_preservation_payload(
        questions=[
            {"id": "q1", "category": "exception", "item_id": "rule-1"},
        ],
        answers=[{"question_id": "q1", "correct": True}],
        item_locations={"rule-1": ["md"]},
    )
    result = preservation.process(payload, load_config())
    assert result["pass"] is True


def test_d7_empty_question_set_is_error(unit, load_config,
                                        make_preservation_payload):
    preservation = unit("preservation")
    payload = make_preservation_payload(
        questions=[], answers=[], item_locations={}
    )
    with pytest.raises(ValueError):
        preservation.process(payload, load_config())


def test_empty_item_locations_is_error(unit, load_config,
                                       make_preservation_payload):
    """Reachability over zero items is undefined, not 100%."""
    preservation = unit("preservation")
    payload = make_preservation_payload(item_locations={})
    with pytest.raises(ValueError):
        preservation.process(payload, load_config())


def test_all_wrong_answers_give_zero_rate(unit, load_config,
                                          make_preservation_payload):
    preservation = unit("preservation")
    payload = make_preservation_payload(
        answers=[
            {"question_id": "q1", "correct": False},
            {"question_id": "q2", "correct": False},
            {"question_id": "q3", "correct": False},
            {"question_id": "q4", "correct": False},
        ]
    )
    result = preservation.process(payload, load_config())
    assert result["preservation_rate"] == pytest.approx(0.0)
    assert result["pass"] is False
