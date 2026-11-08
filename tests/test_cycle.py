"""cycle wiring layer -- P4 design (定期タスク化), acceptance tests.

Written from the P4 cycle design document only, before any
implementation exists.  The design pins the CLI form

    python -m entropy_reduction.cycle <subcommand>
    prepare --counts <counts.json> [--workdir <dir>] [--today YYYY-MM-DD]
    assemble-present --workdir <dir> --judged <judged.json> [--today ...]
    complete --workdir <dir> [--today ...]

the written files (measure_input.json / snapshot.json / candidates.json
/ prepare_summary.json / ledger_rows.json / judged.json /
cycle_report.json), the prepare_summary and cycle_report shapes, the
metrics definitions, the stable-key format and the behaviour of the
named helpers ``scan_target`` / ``convert_inventory_candidates`` /
``compute_metrics`` / ``stable_key``.

Where the design does not pin an exact signature or shape, this module
uses (and documents here) the following assumptions; they are reported
back as design questions with this suite:

* ``scan_target(target, today)`` -- ``target`` is an enumeration entry
  ``{"name": ..., "path": ...}`` (the harness discover_targets shape)
  and ``today`` is a ``YYYY-MM-DD`` string.  It returns a dict with
  ``name`` / ``code_lines`` / ``md_lines`` / ``items`` / ``documents``
  / ``documents_skipped``.
* ``convert_inventory_candidates(config)`` -- ``config`` is the usual
  config dict (its ``harness_state_dir`` is read).  It returns a list
  of detect ``inventory_candidates`` dicts (``target`` / ``category``
  / ``title`` / ``detail``).
* ``compute_metrics(items, candidates)`` returns a dict with
  ``scanned_items`` / ``dead_items`` / ``dead_rate`` /
  ``duplicate_involved_items`` / ``duplicate_rate``.
* ``stable_key(candidate)`` takes a candidate dict and returns the
  ``source|target|category|title`` join.
* The assemble-present stdout summary field ``rows`` is a count (int),
  like the other summary fields.
* In ``complete``, candidates that became ledger rows -- including
  ``needs_check`` rows -- are the ones recorded as presented.
* CLI failures caused by ValueError surface as a non-zero exit code
  (the design's common exit-code rule); the exact 1-vs-2 split for
  cycle-level ValueErrors is not pinned by these tests.

No test loads an embedding model: prepare fixtures contain no md
files, so the detect documents list is empty and its embedding path
never runs; md handling is exercised through ``scan_target`` only,
which does not call detect.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from conftest import REPO_ROOT, VALID_CONFIG

TODAY = "2026-11-08"

EMBEDDING_CONFIG = {
    "embedding_model": "cl-nagoya/ruri-v3-310m",
    "embedding_pca_components": 64,
    "embedding_top_k": 3,
    "embedding_relative_margin": 0.03,
    "embedding_min_similarity": 0.5,
    "embedding_chunk_chars": 800,
}

VALID_COUNTS = {
    "crons": 1,
    "hooks": 2,
    "custom_skills": 3,
    "ledger_open_rows": 4,
    "open_issues": 5,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_file(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _cycle_config(tmp_path, *, harness_config_path, cycle_state_dir,
                  harness_state_dir, drop=(), overrides=None):
    """Write a full cycle config file and return its path."""
    data = dict(VALID_CONFIG)
    data.update(EMBEDDING_CONFIG)
    data.update(
        {
            "harness_state_dir": str(harness_state_dir),
            "harness_config_path": str(harness_config_path),
            "cycle_state_dir": str(cycle_state_dir),
        }
    )
    if overrides:
        data.update(overrides)
    for key in drop:
        data.pop(key, None)
    return _write_json(tmp_path / "config.local.json", data)


def _run_cycle(args, config_path, cwd):
    """Run the cycle CLI in a subprocess; return the CompletedProcess."""
    pythonpath = [str(REPO_ROOT), str(REPO_ROOT / "src")]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env["ENTROPY_REDUCTION_CONFIG"] = str(config_path)
    return subprocess.run(
        [sys.executable, "-m", "entropy_reduction.cycle", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=120,
    )


def _assert_cli_failed(proc):
    """A CLI failure must be the implementation rejecting the input,
    not the module being absent (which is the pre-implementation
    state of this suite)."""
    assert proc.returncode != 0
    assert "No module named" not in proc.stderr


def _stdout_json(proc):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _set_mtime(path, moment):
    ts = moment.timestamp()
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# prepare: target enumeration and scanning through the CLI
# ---------------------------------------------------------------------------


def _make_prepare_env(tmp_path):
    """A small fake world for prepare: mirrors + extra - exclude.

    All scanned files are code/other (no md), so documents stay empty
    and the embedding path never runs.
    """
    mirrors = tmp_path / "mirrors"
    _write_file(mirrors / "repo-a" / "main.py", "a\nb\nc\n")
    _write_file(mirrors / "repo-a" / "util.py", "x\ny\n")
    _write_file(mirrors / "repo-a" / "notes.txt", "1\n2\n3\n4\n5\n6\n7\n")
    (mirrors / "repo-a" / ".git").mkdir(parents=True)
    _write_file(mirrors / "repo-b" / "app.js", "1\n2\n3\n4\n")
    (mirrors / "repo-b" / ".git").mkdir(parents=True)
    _write_file(mirrors / "not-a-repo" / "data.py", "nope\n")
    _write_file(mirrors / "excluded-repo" / "skip.py", "skip\n")
    (mirrors / "excluded-repo" / ".git").mkdir(parents=True)
    _write_file(mirrors / "loose.py", "loose\n")  # a file, not a directory

    extra = tmp_path / "extra-one"
    _write_file(extra / "tool.py", "only\n")  # extra needs no .git

    harness_config = _write_json(
        tmp_path / "harness" / "config.json",
        {
            "targets": {
                "mirrors_dir": str(mirrors),
                "extra_targets": [{"name": "extra-one", "path": str(extra)}],
                "exclude": ["excluded-repo"],
            }
        },
    )
    harness_state = tmp_path / "harness_state"
    harness_state.mkdir()
    cycle_state = tmp_path / "cycle_state"
    config_path = _cycle_config(
        tmp_path,
        harness_config_path=harness_config,
        cycle_state_dir=cycle_state,
        harness_state_dir=harness_state,
    )
    counts_path = _write_json(tmp_path / "counts.json", dict(VALID_COUNTS))
    return {
        "mirrors": mirrors,
        "extra": extra,
        "harness_config": harness_config,
        "harness_state": harness_state,
        "cycle_state": cycle_state,
        "config_path": config_path,
        "counts_path": counts_path,
        "workdir": tmp_path / "work",
    }


def _run_prepare(env, tmp_path, counts_path=None, workdir="__default__",
                 today=TODAY):
    args = ["prepare", "--counts", str(counts_path or env["counts_path"])]
    if workdir == "__default__":
        args += ["--workdir", str(env["workdir"])]
    elif workdir is not None:
        args += ["--workdir", str(workdir)]
    if today is not None:
        args += ["--today", today]
    return _run_cycle(args, env["config_path"], tmp_path)


def test_prepare_enumerates_targets_sorted_and_totals(tmp_path):
    """mirrors (.git only) + extra - exclude, sorted by name; code
    lines are totalled and other files are not counted."""
    env = _make_prepare_env(tmp_path)
    proc = _run_prepare(env, tmp_path)
    _stdout_json(proc)

    measure_input = _read_json(env["workdir"] / "measure_input.json")
    assert measure_input["taken_at"] == TODAY
    names = [t["name"] for t in measure_input["targets"]]
    assert names == ["extra-one", "repo-a", "repo-b"]
    by_name = {t["name"]: t for t in measure_input["targets"]}
    assert by_name["repo-a"]["code_lines"] == 5
    assert by_name["repo-a"]["md_lines"] == 0
    assert by_name["repo-b"]["code_lines"] == 4
    assert by_name["extra-one"]["code_lines"] == 1

    snapshot = _read_json(env["workdir"] / "snapshot.json")
    assert snapshot["kind"] == "snapshot"
    assert snapshot["totals"] == {
        "repos": 3,
        "code_lines": 10,
        "md_lines": 0,
        "crons": 1,
        "hooks": 2,
        "custom_skills": 3,
        "ledger_open_rows": 4,
        "open_issues": 5,
    }

    candidates = _read_json(env["workdir"] / "candidates.json")
    assert candidates["kind"] == "candidates"
    assert candidates["items"] == []

    summary = _read_json(env["workdir"] / "prepare_summary.json")
    assert summary["run_date"] == TODAY
    assert summary["totals"] == snapshot["totals"]
    assert summary["sources"] == snapshot["sources"]
    assert summary["candidates_total"] == 0
    assert summary["documents_skipped"] == 0
    assert summary["metrics"]["scanned_items"] == 0
    assert summary["metrics"]["dead_rate"] is None
    assert summary["metrics"]["duplicate_rate"] is None


def test_prepare_mirrors_dir_not_a_directory_gives_only_extra(tmp_path):
    """Design: if mirrors_dir is not a directory, the mirror targets
    are empty (harness behaviour); extra targets still apply."""
    env = _make_prepare_env(tmp_path)
    _write_json(
        env["harness_config"],
        {
            "targets": {
                "mirrors_dir": str(tmp_path / "no-such-dir"),
                "extra_targets": [
                    {"name": "extra-one", "path": str(env["extra"])}
                ],
                "exclude": [],
            }
        },
    )
    proc = _run_prepare(env, tmp_path)
    _stdout_json(proc)
    measure_input = _read_json(env["workdir"] / "measure_input.json")
    assert [t["name"] for t in measure_input["targets"]] == ["extra-one"]
    snapshot = _read_json(env["workdir"] / "snapshot.json")
    assert snapshot["totals"]["repos"] == 1
    assert snapshot["totals"]["code_lines"] == 1


def test_prepare_missing_harness_config_file_fails(tmp_path):
    """Design: no harness config file -> ValueError."""
    env = _make_prepare_env(tmp_path)
    env["harness_config"].unlink()
    # The cycle config still points at the (now missing) file.
    proc = _run_prepare(env, tmp_path)
    _assert_cli_failed(proc)


def test_prepare_default_workdir_is_under_cycle_state_dir(tmp_path):
    """Design: the default workdir is <cycle_state_dir>/runs/<today>."""
    env = _make_prepare_env(tmp_path)
    proc = _run_prepare(env, tmp_path, workdir=None)
    _stdout_json(proc)
    default_workdir = env["cycle_state"] / "runs" / TODAY
    assert (default_workdir / "snapshot.json").is_file()
    assert (default_workdir / "prepare_summary.json").is_file()


# ---------------------------------------------------------------------------
# scan_target
# ---------------------------------------------------------------------------


def _scan(unit, name, path, today=TODAY):
    cycle = unit("cycle")
    return cycle.scan_target({"name": name, "path": str(path)}, today)


def test_scan_target_aggregates_code_and_md_ignores_other(tmp_path, unit):
    root = tmp_path / "repo-x"
    _write_file(root / "a.py", "x\ny\nz\n")
    _write_file(root / "sub" / "d.py", "only\n")
    _write_file(root / "b.md", "hello\nworld\n")
    _write_file(root / "c.txt", "ignored\nlines\nhere\n")
    _write_file(root / "data.xyz", "also ignored\n")
    result = _scan(unit, "repo-x", root)
    assert result["name"] == "repo-x"
    assert result["code_lines"] == 4
    assert result["md_lines"] == 2
    assert result["documents_skipped"] == 0


def test_scan_target_prunes_directories(tmp_path, unit):
    """Pruned directories (.git / __pycache__ / node_modules / .venv /
    caches) are not descended into at all."""
    root = tmp_path / "repo-x"
    _write_file(root / "keep.py", "one\n")
    for pruned in (".git", "__pycache__", "node_modules", ".venv",
                   ".pytest_cache", ".mypy_cache"):
        _write_file(root / pruned / "hidden.py", "h\n" * 50)
        _write_file(root / pruned / "hidden.md", "h\n" * 50)
    _write_file(root / "sub" / "__pycache__" / "hidden.py", "h\n" * 50)
    result = _scan(unit, "repo-x", root)
    assert result["code_lines"] == 1
    assert result["md_lines"] == 0
    assert result["items"] == []
    assert result["documents"] == []


def test_scan_target_item_shape_signature_and_document(tmp_path, unit):
    root = tmp_path / "repo-x"
    body = "hello\nworld\n"
    md_path = _write_file(root / "docs" / "b.md", body)
    _set_mtime(md_path, datetime(2026, 11, 3, 12, 0, 0))
    result = _scan(unit, "repo-x", root)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["target"] == "repo-x/docs/b.md"
    assert item["md_lines"] == 2
    assert item["signature"] == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()
    assert isinstance(item["last_used_days_ago"], int)
    assert result["documents"] == [
        {"target": "repo-x/docs/b.md", "text": body}
    ]


def test_scan_target_signature_matches_for_identical_content(tmp_path, unit):
    root = tmp_path / "repo-x"
    _write_file(root / "one.md", "same body\n")
    _write_file(root / "sub" / "two.md", "same body\n")
    _write_file(root / "three.md", "different body\n")
    result = _scan(unit, "repo-x", root)
    by_target = {i["target"]: i for i in result["items"]}
    assert (
        by_target["repo-x/one.md"]["signature"]
        == by_target["repo-x/sub/two.md"]["signature"]
    )
    assert (
        by_target["repo-x/one.md"]["signature"]
        != by_target["repo-x/three.md"]["signature"]
    )


def test_scan_target_last_used_from_mtime_fallback(tmp_path, unit):
    """A non-git target uses the file mtime, measured against today
    23:59:59 local, floored, minimum 0."""
    root = tmp_path / "repo-x"
    old = _write_file(root / "old.md", "old\n")
    _set_mtime(old, datetime(2026, 11, 3, 12, 0, 0))
    fresh = _write_file(root / "fresh.md", "fresh\n")
    _set_mtime(fresh, datetime(2026, 11, 8, 0, 0, 0))
    future = _write_file(root / "future.md", "future\n")
    _set_mtime(future, datetime(2026, 12, 25, 12, 0, 0))
    result = _scan(unit, "repo-x", root)
    by_target = {i["target"]: i for i in result["items"]}
    # 2026-11-03 12:00 -> 2026-11-08 23:59:59 is 5 days and change.
    assert by_target["repo-x/old.md"]["last_used_days_ago"] == 5
    assert by_target["repo-x/fresh.md"]["last_used_days_ago"] == 0
    assert by_target["repo-x/future.md"]["last_used_days_ago"] == 0


def test_scan_target_undecodable_md_is_skipped(tmp_path, unit):
    """An md file that is not UTF-8 is neither item nor document; it
    is counted in documents_skipped and excluded from md_lines."""
    root = tmp_path / "repo-x"
    _write_file(root / "good.md", "fine\n")
    broken = root / "broken.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"\xff\xfe\x00not utf-8\n")
    result = _scan(unit, "repo-x", root)
    assert result["documents_skipped"] == 1
    assert result["md_lines"] == 1
    assert [i["target"] for i in result["items"]] == ["repo-x/good.md"]
    assert [d["target"] for d in result["documents"]] == ["repo-x/good.md"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_scan_target_last_used_from_git_commit(tmp_path, unit):
    """In a git repository the last commit time wins over an old
    mtime; an uncommitted file falls back to its mtime."""
    cycle = unit("cycle")
    root = tmp_path / "repo-git"
    root.mkdir()
    committed = _write_file(root / "committed.md", "committed\n")
    _set_mtime(committed, datetime.now() - timedelta(days=40))

    def git(*args):
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("add", "committed.md")
    git("commit", "-q", "-m", "init")

    uncommitted = _write_file(root / "draft.md", "draft\n")
    today = date.today()
    three_days_ago_noon = datetime.combine(
        today - timedelta(days=3), time(12, 0, 0)
    )
    _set_mtime(uncommitted, three_days_ago_noon)

    result = cycle.scan_target(
        {"name": "repo-git", "path": str(root)}, today.isoformat()
    )
    by_target = {i["target"]: i for i in result["items"]}
    # Committed just now: 0 days ago despite the 40-day-old mtime.
    assert by_target["repo-git/committed.md"]["last_used_days_ago"] == 0
    # No commit for this file: mtime fallback (3 days and change).
    assert by_target["repo-git/draft.md"]["last_used_days_ago"] == 3


# ---------------------------------------------------------------------------
# convert_inventory_candidates
# ---------------------------------------------------------------------------


def _inventory_candidate(target, title, viewpoint, state):
    return {
        "id": "C-000",
        "target": target,
        "title": title,
        "viewpoint": viewpoint,
        "state": state,
    }


def test_convert_inventory_candidates_maps_and_filters_terminal(
    tmp_path, unit, load_config
):
    """Terminal states (reported / rejected_as_spec) are excluded;
    viewpoint maps skill-md-bloat -> bloat, md-redundancy ->
    duplicate, anything else -> other; detail names run/viewpoint/
    state."""
    cycle = unit("cycle")
    state_dir = tmp_path / "state"
    _write_json(
        state_dir / "run-a.json",
        {
            "run_id": "run-a",
            "candidates": [
                _inventory_candidate(
                    "repo-a/SKILL.md", "肥大したスキル", "skill-md-bloat",
                    "found",
                ),
                _inventory_candidate(
                    "repo-a/dup.md", "重複した文書", "md-redundancy",
                    "characterized",
                ),
                _inventory_candidate(
                    "repo-a/misc.py", "その他の候補", "refactoring",
                    "found",
                ),
                _inventory_candidate(
                    "repo-a/done.md", "報告済み", "skill-md-bloat",
                    "reported",
                ),
                _inventory_candidate(
                    "repo-a/spec.md", "仕様として却下", "md-redundancy",
                    "rejected_as_spec",
                ),
            ],
        },
    )
    config = load_config(overrides={"harness_state_dir": str(state_dir)})
    result = cycle.convert_inventory_candidates(config)
    assert result == [
        {
            "target": "repo-a/SKILL.md",
            "category": "bloat",
            "title": "肥大したスキル",
            "detail": "棚卸しラン run-a の候補（観点：skill-md-bloat／状態：found）",
        },
        {
            "target": "repo-a/dup.md",
            "category": "duplicate",
            "title": "重複した文書",
            "detail": (
                "棚卸しラン run-a の候補"
                "（観点：md-redundancy／状態：characterized）"
            ),
        },
        {
            "target": "repo-a/misc.py",
            "category": "other",
            "title": "その他の候補",
            "detail": "棚卸しラン run-a の候補（観点：refactoring／状態：found）",
        },
    ]


def test_convert_inventory_candidates_dedupes_by_target_title(
    tmp_path, unit, load_config
):
    """Duplicate (target, title) pairs keep only the first one, in
    file-name order."""
    cycle = unit("cycle")
    state_dir = tmp_path / "state"
    _write_json(
        state_dir / "a-run.json",
        {
            "run_id": "a-run",
            "candidates": [
                _inventory_candidate(
                    "repo-a/x.md", "同じ候補", "skill-md-bloat", "found"
                )
            ],
        },
    )
    _write_json(
        state_dir / "b-run.json",
        {
            "run_id": "b-run",
            "candidates": [
                _inventory_candidate(
                    "repo-a/x.md", "同じ候補", "md-redundancy", "found"
                ),
                _inventory_candidate(
                    "repo-a/y.md", "別の候補", "md-redundancy", "found"
                ),
            ],
        },
    )
    config = load_config(overrides={"harness_state_dir": str(state_dir)})
    result = cycle.convert_inventory_candidates(config)
    assert len(result) == 2
    assert result[0]["target"] == "repo-a/x.md"
    assert result[0]["category"] == "bloat"  # the a-run (first) one wins
    assert "a-run" in result[0]["detail"]
    assert result[1]["target"] == "repo-a/y.md"


def test_convert_inventory_candidates_missing_state_dir_gives_empty(
    tmp_path, unit, load_config
):
    cycle = unit("cycle")
    config = load_config(
        overrides={"harness_state_dir": str(tmp_path / "no-such-dir")}
    )
    assert cycle.convert_inventory_candidates(config) == []


def test_convert_inventory_candidates_broken_json_raises(
    tmp_path, unit, load_config
):
    cycle = unit("cycle")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "broken.json").write_text("{not json", encoding="utf-8")
    config = load_config(overrides={"harness_state_dir": str(state_dir)})
    with pytest.raises(ValueError):
        cycle.convert_inventory_candidates(config)


# ---------------------------------------------------------------------------
# counts validation (prepare input)
# ---------------------------------------------------------------------------


def _bad_counts_cases():
    missing = dict(VALID_COUNTS)
    del missing["hooks"]
    extra = dict(VALID_COUNTS, unexpected=1)
    negative = dict(VALID_COUNTS, crons=-1)
    boolean = dict(VALID_COUNTS, crons=True)
    non_integer = dict(VALID_COUNTS, crons=1.5)
    return [missing, extra, negative, boolean, non_integer]


@pytest.mark.parametrize(
    "bad_counts",
    _bad_counts_cases(),
    ids=["missing-key", "extra-key", "negative", "bool", "non-integer"],
)
def test_prepare_counts_validation_rejects_bad_counts(tmp_path, bad_counts):
    """counts must have exactly the five keys, each a non-negative
    integer (bool is not an integer); anything else is ValueError."""
    env = _make_prepare_env(tmp_path)
    bad_path = _write_json(tmp_path / "bad_counts.json", bad_counts)
    proc = _run_prepare(env, tmp_path, counts_path=bad_path)
    _assert_cli_failed(proc)


# ---------------------------------------------------------------------------
# compute_metrics / stable_key
# ---------------------------------------------------------------------------


def _item(target, signature):
    return {
        "target": target,
        "md_lines": 10,
        "last_used_days_ago": 0,
        "signature": signature,
    }


def _candidate(target, category, source):
    return {
        "id": "cand-x",
        "source": source,
        "target": target,
        "category": category,
        "title": "t",
        "detail": "d",
    }


def test_compute_metrics_dead_rate_counts_only_weekly_dead(unit):
    cycle = unit("cycle")
    items = [_item(f"t{i}", f"s{i}") for i in range(4)]
    candidates = [
        _candidate("t0", "dead", "weekly"),
        _candidate("t1", "dead", "inventory"),  # not weekly: not counted
        _candidate("t2", "bloat", "weekly"),  # not dead: not counted
    ]
    metrics = cycle.compute_metrics(items, candidates)
    assert metrics["scanned_items"] == 4
    assert metrics["dead_items"] == 1
    assert metrics["dead_rate"] == 0.25


def test_compute_metrics_duplicate_is_union_of_signature_and_embedding(
    unit,
):
    """Duplicate-involved = items sharing a signature (group of >= 2)
    UNION targets of embedding duplicate candidates; overlaps count
    once, and non-embedding duplicates do not add targets."""
    cycle = unit("cycle")
    items = [
        _item("t1", "s1"),
        _item("t2", "s1"),  # signature group {t1, t2}
        _item("t3", "s2"),
        _item("t4", "s3"),
        _item("t5", "s3"),  # signature group {t4, t5}
    ]
    candidates = [
        _candidate("t3", "duplicate", "embedding"),  # new target
        _candidate("t1", "duplicate", "embedding"),  # already in a group
        _candidate("t4", "duplicate", "weekly"),  # not embedding: no add
        _candidate("t3", "duplicate", "inventory"),  # not embedding
    ]
    metrics = cycle.compute_metrics(items, candidates)
    assert metrics["scanned_items"] == 5
    assert metrics["duplicate_involved_items"] == 5
    assert metrics["duplicate_rate"] == 1.0


def test_compute_metrics_zero_scanned_gives_none_rates(unit):
    cycle = unit("cycle")
    metrics = cycle.compute_metrics([], [])
    assert metrics["scanned_items"] == 0
    assert metrics["dead_items"] == 0
    assert metrics["duplicate_involved_items"] == 0
    assert metrics["dead_rate"] is None
    assert metrics["duplicate_rate"] is None


def test_compute_metrics_rounds_rates_to_four_decimals(unit):
    cycle = unit("cycle")
    items = [_item("t1", "s1"), _item("t2", "s1"), _item("t3", "s3")]
    candidates = [_candidate("t1", "dead", "weekly")]
    metrics = cycle.compute_metrics(items, candidates)
    assert metrics["dead_rate"] == 0.3333  # 1/3
    assert metrics["duplicate_rate"] == 0.6667  # 2/3 (signature pair)


def test_stable_key_format(unit):
    cycle = unit("cycle")
    candidate = _candidate("repo-a/docs/x.md", "dead", "weekly")
    candidate["title"] = "古い文書"
    assert (
        cycle.stable_key(candidate)
        == "weekly|repo-a/docs/x.md|dead|古い文書"
    )


# ---------------------------------------------------------------------------
# assemble-present
# ---------------------------------------------------------------------------


def _judged_item(candidate_id, status, verdict, **overrides):
    item = {
        "candidate_id": candidate_id,
        "status": status,
        "evidence": {
            "last_used": "2026-08-01",
            "references": ["docs/index.md"],
            "impact": "影響は限定的",
        },
        "verdict": verdict,
        "migration_target": None,
        "reason": "判定理由",
    }
    if status == "needs_check":
        item["evidence"] = {
            "last_used": "2026-08-01",
            "references": [],
            "impact": None,
        }
    item.update(overrides)
    return item


def _candidates_payload(*entries):
    return {
        "kind": "candidates",
        "version": 1,
        "items": [
            {
                "id": cid,
                "source": source,
                "target": target,
                "category": category,
                "title": title,
                "detail": "detail",
            }
            for cid, source, target, category, title in entries
        ],
    }


ASSEMBLE_CANDIDATES = _candidates_payload(
    ("cand-1", "weekly", "repo-a/docs/old.md", "dead", "古い文書"),
    ("cand-2", "weekly", "repo-a/docs/big.md", "bloat", "大きな文書"),
    ("cand-3", "weekly", "repo-a/docs/keep.md", "other", "現役の文書"),
    ("cand-4", "inventory", "repo-a/docs/check.md", "other", "確認が要る文書"),
)

ASSEMBLE_JUDGED = {
    "kind": "judged",
    "version": 1,
    "items": [
        _judged_item("cand-1", "judged", "delete"),
        _judged_item("cand-2", "judged", "delete"),
        _judged_item("cand-3", "judged", "keep"),
        _judged_item("cand-4", "needs_check", None),
    ],
}


def _make_assemble_env(tmp_path, *, with_presented):
    workdir = tmp_path / "work"
    _write_json(workdir / "candidates.json", ASSEMBLE_CANDIDATES)
    judged_path = _write_json(tmp_path / "judged_in.json", ASSEMBLE_JUDGED)
    cycle_state = tmp_path / "cycle_state"
    if with_presented:
        # The presented record is from an earlier run: its
        # candidate_id differs, but its stable key matches cand-1.
        _write_json(
            cycle_state / "presented.json",
            {
                "version": 1,
                "presented": [
                    {
                        "key": "weekly|repo-a/docs/old.md|dead|古い文書",
                        "candidate_id": "cand-99",
                        "run_date": "2026-11-01",
                    }
                ],
            },
        )
    harness_config = _write_json(tmp_path / "harness" / "config.json", {})
    config_path = _cycle_config(
        tmp_path,
        harness_config_path=harness_config,
        cycle_state_dir=cycle_state,
        harness_state_dir=tmp_path / "harness_state",
    )
    return {
        "workdir": workdir,
        "judged_path": judged_path,
        "cycle_state": cycle_state,
        "config_path": config_path,
    }


def _run_assemble(env, tmp_path):
    return _run_cycle(
        [
            "assemble-present",
            "--workdir", str(env["workdir"]),
            "--judged", str(env["judged_path"]),
            "--today", TODAY,
        ],
        env["config_path"],
        tmp_path,
    )


def test_assemble_present_skips_already_presented_by_stable_key(tmp_path):
    """A candidate whose stable key was presented before is converted
    to presented_candidate_ids and not re-presented, even though its
    per-run candidate id has changed."""
    env = _make_assemble_env(tmp_path, with_presented=True)
    proc = _run_assemble(env, tmp_path)
    summary = _stdout_json(proc)
    assert summary["rows"] == 2
    assert summary["almost_certain"] == 1
    assert summary["needs_check"] == 1
    assert summary["excluded_count"] == 1
    assert summary["already_presented_count"] == 1

    ledger = _read_json(env["workdir"] / "ledger_rows.json")
    links = [row["link"] for row in ledger["rows"]]
    assert links == ["cand-2", "cand-4"]
    assert ledger["rows"][0]["task"].startswith("【ほぼ確実")
    assert ledger["rows"][1]["task"].startswith("【要確認")

    # complete relies on the judged copy in the workdir.
    assert _read_json(env["workdir"] / "judged.json") == ASSEMBLE_JUDGED


def test_assemble_present_without_presented_state_rows_all(tmp_path):
    """With no presented.json, nothing counts as already presented."""
    env = _make_assemble_env(tmp_path, with_presented=False)
    proc = _run_assemble(env, tmp_path)
    summary = _stdout_json(proc)
    assert summary["rows"] == 3
    assert summary["almost_certain"] == 2
    assert summary["needs_check"] == 1
    assert summary["excluded_count"] == 1
    assert summary["already_presented_count"] == 0
    ledger = _read_json(env["workdir"] / "ledger_rows.json")
    assert [row["link"] for row in ledger["rows"]] == [
        "cand-1", "cand-2", "cand-4",
    ]


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

COMPLETE_CANDIDATES = _candidates_payload(
    ("cand-1", "weekly", "repo-a/docs/a.md", "dead", "文書A"),
    ("cand-2", "weekly", "repo-a/docs/b.md", "bloat", "文書B"),
    ("cand-3", "weekly", "repo-a/docs/c.md", "other", "文書C"),
)

COMPLETE_JUDGED = {
    "kind": "judged",
    "version": 1,
    "items": [
        _judged_item("cand-1", "judged", "delete"),
        _judged_item("cand-2", "needs_check", None),
        _judged_item("cand-3", "judged", "keep"),
    ],
}

COMPLETE_TOTALS = {
    "repos": 3,
    "code_lines": 100,
    "md_lines": 40,
    "crons": 1,
    "hooks": 2,
    "custom_skills": 3,
    "ledger_open_rows": 4,
    "open_issues": 5,
}

COMPLETE_METRICS = {
    "scanned_items": 4,
    "dead_items": 1,
    "dead_rate": 0.25,
    "duplicate_involved_items": 2,
    "duplicate_rate": 0.5,
}


def _ledger_rows_payload(rows, excluded=0, already=0):
    payload = {
        "kind": "ledger_rows",
        "version": 1,
        "rows": rows,
        "excluded_count": excluded,
        "already_presented_count": already,
    }
    return payload


def _row(task, link):
    row = {
        "task": task,
        "status": "未着手",
        "reason": "",
        "check_method": "",
        "due_date": "2026-11-15",
        "link": link,
        "updated_at": TODAY,
    }
    return row


def _bracketed(text):
    return chr(0x3010) + text + chr(0x3011)


def _make_complete_env(tmp_path):
    workdir = tmp_path / "work"
    _write_json(workdir / "candidates.json", COMPLETE_CANDIDATES)
    _write_json(workdir / "judged.json", COMPLETE_JUDGED)
    rows = [
        _row(_bracketed("ほぼ確実・束ね：dead") + "文書A", "cand-1"),
        _row(_bracketed("要確認") + "文書B", "cand-2"),
    ]
    _write_json(
        workdir / "ledger_rows.json",
        _ledger_rows_payload(rows, excluded=1, already=0),
    )
    snapshot = {
        "kind": "snapshot",
        "version": 1,
        "taken_at": TODAY,
        "totals": dict(COMPLETE_TOTALS),
        "sources": [],
    }
    _write_json(workdir / "snapshot.json", snapshot)
    summary = {
        "run_date": TODAY,
        "totals": dict(COMPLETE_TOTALS),
        "sources": [],
        "candidates_total": 3,
        "metrics": dict(COMPLETE_METRICS),
        "documents_skipped": 0,
    }
    _write_json(workdir / "prepare_summary.json", summary)
    _write_json(workdir / "append_result.json", {"appended_rows": 2})
    cycle_state = tmp_path / "cycle_state"
    presented = {
        "version": 1,
        "presented": [
            {
                "key": "weekly|other/x.md|dead|旧候補",
                "candidate_id": "cand-old",
                "run_date": "2026-11-01",
            }
        ],
    }
    _write_json(cycle_state / "presented.json", presented)
    harness_config = _write_json(tmp_path / "harness" / "config.json", {})
    config_path = _cycle_config(
        tmp_path,
        harness_config_path=harness_config,
        cycle_state_dir=cycle_state,
        harness_state_dir=tmp_path / "harness_state",
    )
    env = {
        "workdir": workdir,
        "cycle_state": cycle_state,
        "config_path": config_path,
    }
    return env


def _run_complete(env, tmp_path):
    args = ["complete", "--workdir", str(env["workdir"]), "--today", TODAY]
    return _run_cycle(args, env["config_path"], tmp_path)


def test_complete_mismatched_append_count_fails(tmp_path):
    """appended_rows must equal the ledger row count; a partial
    append must not complete."""
    env = _make_complete_env(tmp_path)
    _write_json(env["workdir"] / "append_result.json", {"appended_rows": 1})
    proc = _run_complete(env, tmp_path)
    _assert_cli_failed(proc)


def test_complete_report_presented_trend_history_no_execution(tmp_path):
    env = _make_complete_env(tmp_path)
    proc = _run_complete(env, tmp_path)
    _stdout_json(proc)

    report = _read_json(env["workdir"] / "cycle_report.json")
    assert report["kind"] == "cycle_report"
    assert report["version"] == 1
    assert report["run_date"] == TODAY
    assert report["candidates"] == {
        "total": 3,
        "almost_certain": 1,
        "needs_check": 1,
        "excluded": 1,
        "already_presented": 0,
        "appended_rows": 2,
    }
    expected_metrics = dict(COMPLETE_METRICS)
    expected_metrics["reachability"] = None  # no last_preservation.json
    assert report["metrics"] == expected_metrics
    assert report["totals_trend"] == {
        "before": None,  # no earlier snapshot
        "after": COMPLETE_TOTALS,
    }
    assert report["execution"] == {
        "performed": False,
        "note": "削減の実行なし（本人の仕分け済み行なし）",
    }
    assert report["sources"] == []

    # Presented state: the two rowed candidates are appended with
    # their stable keys; the keep candidate is not; the old entry
    # stays.
    presented = _read_json(env["cycle_state"] / "presented.json")
    assert presented["version"] == 1
    entries = {e["candidate_id"]: e for e in presented["presented"]}
    assert set(entries) == {"cand-old", "cand-1", "cand-2"}
    assert entries["cand-1"] == {
        "key": "weekly|repo-a/docs/a.md|dead|文書A",
        "candidate_id": "cand-1",
        "run_date": TODAY,
    }
    assert entries["cand-2"] == {
        "key": "weekly|repo-a/docs/b.md|bloat|文書B",
        "candidate_id": "cand-2",
        "run_date": TODAY,
    }

    # The snapshot is archived under the run date.
    archived = _read_json(
        env["cycle_state"] / "snapshots" / f"{TODAY}.json"
    )
    assert archived["totals"] == COMPLETE_TOTALS

    # Exactly one history line, with exactly the documented keys.
    history_lines = (
        (env["cycle_state"] / "history.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(history_lines) == 1
    history = json.loads(history_lines[0])
    assert history == {
        "run_date": TODAY,
        "candidates_total": 3,
        "appended_rows": 2,
        "totals": COMPLETE_TOTALS,
    }


def test_complete_trend_uses_latest_snapshot_before_run_date(tmp_path):
    env = _make_complete_env(tmp_path)
    snapshots = env["cycle_state"] / "snapshots"
    older = dict(COMPLETE_TOTALS, code_lines=90)
    latest_before = dict(COMPLETE_TOTALS, code_lines=95)
    future = dict(COMPLETE_TOTALS, code_lines=999)
    for name, totals in (
        ("2026-11-01", older),
        ("2026-11-05", latest_before),
        ("2026-12-01", future),
    ):
        snap = {
            "kind": "snapshot",
            "version": 1,
            "taken_at": name,
            "totals": totals,
            "sources": [],
        }
        _write_json(snapshots / f"{name}.json", snap)
    proc = _run_complete(env, tmp_path)
    _stdout_json(proc)
    report = _read_json(env["workdir"] / "cycle_report.json")
    assert report["totals_trend"]["before"] == latest_before
    assert report["totals_trend"]["after"] == COMPLETE_TOTALS


def test_complete_with_execute_result_and_reachability(tmp_path):
    env = _make_complete_env(tmp_path)
    _write_json(
        env["workdir"] / "execute_result.json",
        {"executed_count": 1, "note_for_test": "実行済み"},
    )
    _write_json(
        env["cycle_state"] / "last_preservation.json",
        {"reachability": 0.98, "preservation_rate": 1.0, "pass": False},
    )
    proc = _run_complete(env, tmp_path)
    _stdout_json(proc)
    report = _read_json(env["workdir"] / "cycle_report.json")
    assert report["execution"]["performed"] is True
    assert report["execution"]["executed_count"] == 1
    assert report["execution"]["note_for_test"] == "実行済み"
    assert report["metrics"]["reachability"] == 0.98


def test_complete_zero_rows_without_append_result_succeeds(tmp_path):
    """With no rows, append_result.json is not required and
    appended_rows is reported as 0."""
    env = _make_complete_env(tmp_path)
    _write_json(
        env["workdir"] / "ledger_rows.json",
        _ledger_rows_payload([], excluded=1, already=0),
    )
    judged = {
        "kind": "judged",
        "version": 1,
        "items": [_judged_item("cand-3", "judged", "keep")],
    }
    _write_json(env["workdir"] / "judged.json", judged)
    candidates = _candidates_payload(
        ("cand-3", "weekly", "repo-a/docs/c.md", "other", "文書C")
    )
    _write_json(env["workdir"] / "candidates.json", candidates)
    (env["workdir"] / "append_result.json").unlink()
    proc = _run_complete(env, tmp_path)
    _stdout_json(proc)
    report = _read_json(env["workdir"] / "cycle_report.json")
    assert report["candidates"]["appended_rows"] == 0
    assert report["candidates"]["total"] == 1
    presented_path = env["cycle_state"] / "presented.json"
    if presented_path.is_file():
        ids = {
            e["candidate_id"]
            for e in _read_json(presented_path)["presented"]
        }
        assert "cand-3" not in ids


def test_complete_unknown_judged_candidate_fails_closed(tmp_path):
    """A judged candidate_id that is not in candidates.json is a
    ValueError (fail-closed), not a silent skip."""
    env = _make_complete_env(tmp_path)
    judged = {
        "kind": "judged",
        "version": 1,
        "items": [_judged_item("cand-unknown", "judged", "delete")],
    }
    _write_json(env["workdir"] / "judged.json", judged)
    rows = [_row(_bracketed("ほぼ確実") + "不明な候補", "cand-unknown")]
    _write_json(
        env["workdir"] / "ledger_rows.json",
        _ledger_rows_payload(rows),
    )
    _write_json(env["workdir"] / "append_result.json", {"appended_rows": 1})
    proc = _run_complete(env, tmp_path)
    _assert_cli_failed(proc)


# ---------------------------------------------------------------------------
# cycle config keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key", ["harness_config_path", "cycle_state_dir"]
)
def test_prepare_missing_cycle_config_key_fails(tmp_path, missing_key):
    """The cycle config keys are mandatory with no fallback default:
    dropping either is a ValueError."""
    env = _make_prepare_env(tmp_path)
    config_path = _cycle_config(
        tmp_path / "alt",
        harness_config_path=env["harness_config"],
        cycle_state_dir=env["cycle_state"],
        harness_state_dir=env["harness_state"],
        drop=(missing_key,),
    )
    proc = _run_cycle(
        [
            "prepare",
            "--counts", str(env["counts_path"]),
            "--workdir", str(env["workdir"]),
            "--today", TODAY,
        ],
        config_path,
        tmp_path,
    )
    _assert_cli_failed(proc)
