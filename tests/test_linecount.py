"""linecount unit -- SPEC section 2 (shared component).

Assumed interface: ``process(payload, config)`` where the payload is
either ``{"text": ..., "target": <name>}`` or ``{"path": <file path>}``
and the result is a dict with ``lines`` (int) and ``category``
(``md`` / ``code`` / ``other``).  The SPEC only says the unit returns
"行数と種別" (a line count and a kind), so these key names are an
assumption flagged in the report.
"""

import pytest


def _run(unit, load_config, payload):
    linecount = unit("linecount")
    return linecount.process(payload, load_config())


def test_counts_lines_with_trailing_newline(unit, load_config):
    result = _run(
        unit, load_config, {"text": "a\nb\nc\n", "target": "notes.md"}
    )
    assert result["lines"] == 3
    assert result["category"] == "md"


def test_counts_lines_without_trailing_newline(unit, load_config):
    result = _run(unit, load_config, {"text": "a\nb", "target": "notes.md"})
    assert result["lines"] == 2


def test_empty_text_is_zero_lines(unit, load_config):
    result = _run(unit, load_config, {"text": "", "target": "notes.md"})
    assert result["lines"] == 0


def test_single_line_without_newline(unit, load_config):
    result = _run(unit, load_config, {"text": "だけ", "target": "notes.md"})
    assert result["lines"] == 1


def test_crlf_text_counts_logical_lines(unit, load_config):
    result = _run(
        unit, load_config, {"text": "a\r\nb\r\n", "target": "notes.md"}
    )
    assert result["lines"] == 2


def test_python_file_is_code_category(unit, load_config):
    result = _run(
        unit, load_config, {"text": "x = 1\ny = 2\n", "target": "tool.py"}
    )
    assert result["lines"] == 2
    assert result["category"] == "code"


def test_unknown_extension_is_other_category(unit, load_config):
    result = _run(
        unit, load_config, {"text": "hello\n", "target": "data.txt"}
    )
    assert result["lines"] == 1
    assert result["category"] == "other"


def test_counts_from_path(unit, load_config, tmp_path):
    linecount = unit("linecount")
    path = tmp_path / "doc.md"
    path.write_text("1行目\n2行目\n3行目\n", encoding="utf-8")
    result = linecount.process({"path": str(path)}, load_config())
    assert result["lines"] == 3
    assert result["category"] == "md"


def test_missing_text_and_path_raises(unit, load_config):
    linecount = unit("linecount")
    with pytest.raises(ValueError):
        linecount.process({"target": "notes.md"}, load_config())


def test_missing_path_file_raises(unit, load_config, tmp_path):
    linecount = unit("linecount")
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        linecount.process(
            {"path": str(tmp_path / "nope.md")}, load_config()
        )


def test_japanese_text_lines(unit, load_config):
    result = _run(
        unit,
        load_config,
        {"text": "一行目\n二行目\n", "target": "日記.md"},
    )
    assert result["lines"] == 2
    assert result["category"] == "md"
