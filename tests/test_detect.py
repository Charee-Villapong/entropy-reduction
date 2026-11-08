"""detect unit -- SPEC section 3 (candidates), sections 4-5, D6/D9/D10.

Assumed input (detect_input): ``snapshot``, ``inventory_candidates``
(candidate-shaped dicts handed over from the inventory run, re-emitted
with source ``inventory``) and ``items`` (the weekly item list; each item
has ``target`` / ``file_type`` / ``md_lines`` / ``code_lines`` /
``last_used_days_ago`` / ``signature``).  Threshold detections from the
item list get source ``weekly``; two items sharing a ``signature``
produce exactly one ``duplicate`` candidate.  Threshold semantics use
clearly above/below values except where a boundary is named.
"""

import copy

import pytest


def _targets_by_category(result):
    grouped = {}
    for item in result["items"]:
        grouped.setdefault(item["category"], []).append(item["target"])
    return grouped


def test_detects_bloat_dead_and_duplicate(unit, load_config,
                                          make_detect_payload):
    detect = unit("detect")
    result = detect.process(make_detect_payload(), load_config())
    assert result["kind"] == "candidates"
    assert result["version"] == 1
    grouped = _targets_by_category(result)
    assert grouped.get("bloat") == ["docs/big.md"]
    assert grouped.get("dead") == ["tools/old.py"]
    # One duplicate candidate for the pair, not two.
    assert len(grouped.get("duplicate", [])) == 1
    assert grouped["duplicate"][0] in ("tools/dup-a.py", "tools/dup-b.py")
    # The small, recently used, unique file produces nothing.
    all_targets = [i["target"] for i in result["items"]]
    assert "docs/small.md" not in all_targets
    assert len(result["items"]) == 3


def test_detected_items_have_exact_candidate_shape(unit, load_config,
                                                   make_detect_payload):
    detect = unit("detect")
    result = detect.process(make_detect_payload(), load_config())
    for item in result["items"]:
        assert set(item.keys()) == {
            "id", "source", "target", "category", "title", "detail",
        }
        assert item["source"] in ("inventory", "weekly", "embedding")
        assert item["category"] in ("duplicate", "dead", "bloat", "other")
        assert item["title"]
        assert item["detail"]


def test_detect_output_has_no_judgement_fields(unit, load_config,
                                               make_detect_payload):
    """SPEC 3 / D9: layer 1 must not judge -- no verdict-like field."""
    detect = unit("detect")
    result = detect.process(make_detect_payload(), load_config())
    for item in result["items"]:
        for forbidden in ("verdict", "status", "evidence", "reason",
                          "migration_target", "user_decision"):
            assert forbidden not in item


def test_detect_output_passes_contracts(unit, load_config,
                                        make_detect_payload):
    detect = unit("detect")
    contracts = unit("contracts")
    result = detect.process(make_detect_payload(), load_config())
    assert contracts.validate_candidates(result) in (None, True)


def test_candidate_ids_are_unique(unit, load_config, make_detect_payload):
    detect = unit("detect")
    result = detect.process(make_detect_payload(), load_config())
    ids = [i["id"] for i in result["items"]]
    assert len(ids) == len(set(ids))


def test_d6_lowering_bloat_threshold_adds_candidates(
    unit, load_config, make_detect_payload
):
    """D6: bloat_md_lines comes from config; changing it changes output."""
    detect = unit("detect")
    strict = detect.process(make_detect_payload(), load_config())
    loose = detect.process(
        make_detect_payload(),
        load_config(overrides={"bloat_md_lines": 100}),
    )
    assert "docs/small.md" not in _targets_by_category(strict).get(
        "bloat", []
    )
    assert "docs/small.md" in _targets_by_category(loose).get("bloat", [])


def test_d6_raising_bloat_threshold_removes_candidates(
    unit, load_config, make_detect_payload
):
    detect = unit("detect")
    result = detect.process(
        make_detect_payload(),
        load_config(overrides={"bloat_md_lines": 1000}),
    )
    assert "bloat" not in _targets_by_category(result)


def test_d6_changing_unused_days_changes_dead_detection(
    unit, load_config, make_detect_payload
):
    """D6: unused_days comes from config."""
    detect = unit("detect")
    result = detect.process(
        make_detect_payload(),
        load_config(overrides={"unused_days": 300}),
    )
    assert "dead" not in _targets_by_category(result)


def test_inventory_candidates_pass_through_with_inventory_source(
    unit, load_config, make_detect_payload
):
    detect = unit("detect")
    payload = make_detect_payload(
        items=[],
        inventory_candidates=[
            {
                "target": "docs/from-inventory.md",
                "category": "bloat",
                "title": "棚卸し候補",
                "detail": "棚卸しランからの引き継ぎ",
            }
        ],
    )
    result = detect.process(payload, load_config())
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["source"] == "inventory"
    assert item["target"] == "docs/from-inventory.md"
    assert item["category"] == "bloat"
    assert item["id"]  # detect assigns an id


def test_empty_inputs_give_empty_candidates(unit, load_config,
                                            make_detect_payload):
    detect = unit("detect")
    result = detect.process(
        make_detect_payload(items=[], inventory_candidates=[]),
        load_config(),
    )
    assert result["kind"] == "candidates"
    assert result["items"] == []


def test_item_exactly_at_bloat_threshold_is_not_decided_here(
    unit, load_config, make_detect_payload
):
    """Boundary semantics (> vs >=) are a SPEC question; this test only
    pins the clearly-below side so the suite does not invent the rule."""
    detect = unit("detect")
    payload = make_detect_payload(
        items=[
            {
                "target": "docs/just-under.md",
                "file_type": "md",
                "md_lines": 299,
                "code_lines": 0,
                "last_used_days_ago": 1,
                "signature": "sig-under",
            }
        ]
    )
    result = detect.process(payload, load_config())
    assert result["items"] == []


def test_d10_deterministic_including_ids(unit, load_config,
                                         make_detect_payload):
    detect = unit("detect")
    payload = make_detect_payload()
    first = detect.process(copy.deepcopy(payload), load_config())
    second = detect.process(copy.deepcopy(payload), load_config())
    assert first == second


def test_missing_snapshot_raises(unit, load_config, make_detect_payload):
    detect = unit("detect")
    payload = make_detect_payload()
    del payload["snapshot"]
    with pytest.raises(ValueError):
        detect.process(payload, load_config())


def test_invalid_inventory_category_raises(unit, load_config,
                                           make_detect_payload):
    detect = unit("detect")
    payload = make_detect_payload(
        items=[],
        inventory_candidates=[
            {
                "target": "docs/x.md",
                "category": "nonsense",
                "title": "x",
                "detail": "x",
            }
        ],
    )
    with pytest.raises(ValueError):
        detect.process(payload, load_config())


def test_item_missing_target_raises(unit, load_config,
                                    make_detect_payload):
    detect = unit("detect")
    payload = make_detect_payload(
        items=[{"file_type": "md", "md_lines": 999}]
    )
    with pytest.raises(ValueError):
        detect.process(payload, load_config())
