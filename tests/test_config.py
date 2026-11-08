"""config unit -- SPEC section 4 and D6.

Assumed interface (from the task brief): ``entropy_reduction.config``
exposes ``load_config(path)`` returning a Config (dict or object) whose
values are readable by key/attribute.  Configuration errors are expected
to raise ValueError (CLI exit code 2 in SPEC section 2); a missing file
may raise OSError instead.
"""

import json

import pytest

REQUIRED_KEYS = [
    "bloat_md_lines",
    "unused_days",
    "preservation_pass_line",
    "harness_state_dir",
    "ledger_target",
]


def test_load_valid_config_returns_all_required_values(
    unit, tmp_path, cfg_get
):
    config_mod = unit("config")
    path = tmp_path / "config.local.json"
    path.write_text(
        json.dumps(
            {
                "bloat_md_lines": 300,
                "unused_days": 90,
                "preservation_pass_line": 1.0,
                "harness_state_dir": "some/state/dir",
                "ledger_target": "task-ledger",
            }
        ),
        encoding="utf-8",
    )
    config = config_mod.load_config(str(path))
    assert cfg_get(config, "bloat_md_lines") == 300
    assert cfg_get(config, "unused_days") == 90
    assert cfg_get(config, "preservation_pass_line") == 1.0
    assert cfg_get(config, "harness_state_dir") == "some/state/dir"
    assert cfg_get(config, "ledger_target") == "task-ledger"


def test_load_example_shaped_config_fixture(unit, cfg_get):
    config_mod = unit("config")
    from conftest import FIXTURES

    config = config_mod.load_config(str(FIXTURES / "config_valid.json"))
    assert cfg_get(config, "bloat_md_lines") == 300
    assert cfg_get(config, "ledger_target") == "task-ledger"


def test_load_config_missing_file_raises(unit, tmp_path):
    config_mod = unit("config")
    with pytest.raises((FileNotFoundError, OSError, ValueError)):
        config_mod.load_config(str(tmp_path / "does-not-exist.json"))


def test_load_config_invalid_json_raises(unit, tmp_path):
    config_mod = unit("config")
    path = tmp_path / "config.local.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        config_mod.load_config(str(path))


def test_load_config_non_object_json_raises(unit, tmp_path):
    config_mod = unit("config")
    path = tmp_path / "config.local.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        config_mod.load_config(str(path))


@pytest.mark.parametrize("missing_key", REQUIRED_KEYS)
def test_load_config_missing_required_key_raises(load_config, missing_key):
    with pytest.raises(ValueError):
        load_config(drop=(missing_key,))


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("bloat_md_lines", "300"),
        ("bloat_md_lines", -1),
        ("bloat_md_lines", 1.5),
        ("unused_days", "90"),
        ("unused_days", -1),
        ("preservation_pass_line", "1.0"),
        ("harness_state_dir", ""),
        ("harness_state_dir", 123),
        ("ledger_target", ""),
        ("ledger_target", None),
    ],
)
def test_load_config_invalid_value_raises(load_config, key, bad_value):
    with pytest.raises(ValueError):
        load_config(overrides={key: bad_value})


@pytest.mark.parametrize("line", [-0.1, 1.1, 2])
def test_load_config_preservation_pass_line_out_of_range(load_config, line):
    with pytest.raises(ValueError):
        load_config(overrides={"preservation_pass_line": line})


@pytest.mark.parametrize("line", [0, 0.8, 1.0])
def test_load_config_preservation_pass_line_boundaries_ok(
    load_config, cfg_get, line
):
    config = load_config(overrides={"preservation_pass_line": line})
    assert cfg_get(config, "preservation_pass_line") == line


def test_d6_loaded_values_are_the_configured_ones(load_config, cfg_get):
    """D6: values come from the file, not from hard-coded defaults."""
    config = load_config(
        overrides={"bloat_md_lines": 123, "unused_days": 45}
    )
    assert cfg_get(config, "bloat_md_lines") == 123
    assert cfg_get(config, "unused_days") == 45
