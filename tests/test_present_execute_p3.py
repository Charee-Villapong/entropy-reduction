"""P3 regression tests (implementation-side additions).

These pin the behaviours SPEC gained after the P0 suite was written:
present's three-tier output (bundling by category, evidence summaries,
incremental presentation, the 2026-11-08 user decision that keep
candidates are counted, not rowed) and execute's executor injection /
filesystem executor with its fail-closed path checks.  The P0 files are
untouched by this module.
"""

import copy

import pytest


def _judged_item(candidate_id, verdict, **overrides):
    item = {
        "candidate_id": candidate_id,
        "status": "judged",
        "evidence": {
            "last_used": "2026-08-01",
            "references": ["docs/index.md"],
            "impact": "影響は限定的",
        },
        "verdict": verdict,
        "migration_target": "tools/dup-a.py" if verdict == "merge" else None,
        "reason": "判定理由",
    }
    item.update(overrides)
    return item


def _candidates(*entries):
    return {
        "kind": "candidates",
        "version": 1,
        "items": [
            {
                "id": cid,
                "source": "weekly",
                "target": target,
                "category": category,
                "title": title,
                "detail": "",
            }
            for cid, target, category, title in entries
        ],
    }


# --- present: bundling / evidence / increments -----------------------------


def test_present_bundles_almost_certain_rows_by_category(unit, load_config):
    present = unit("present")
    payload = {
        "kind": "judged",
        "version": 1,
        "items": [
            _judged_item("cand-c", "delete"),
            _judged_item("cand-a", "merge"),
            _judged_item("cand-b", "delete"),
        ],
        "candidates": _candidates(
            ("cand-a", "tools/a.py", "duplicate", "重複A"),
            ("cand-b", "tools/b.py", "duplicate", "重複B"),
            ("cand-c", "docs/big.md", "bloat", "肥大C"),
        ),
    }
    result = present.process(payload, load_config())
    tasks = [row["task"] for row in result["rows"]]
    assert len(tasks) == 3
    assert tasks[0].startswith("【ほぼ確実・束ね：bloat】")
    assert tasks[1].startswith("【ほぼ確実・束ね：duplicate】")
    assert tasks[2].startswith("【ほぼ確実・束ね：duplicate】")


def test_present_almost_certain_row_carries_evidence(unit, load_config):
    present = unit("present")
    payload = {
        "kind": "judged",
        "version": 1,
        "items": [_judged_item("cand-a", "merge")],
        "candidates": _candidates(("cand-a", "tools/a.py", "duplicate", "重複A")),
    }
    result = present.process(payload, load_config())
    row = result["rows"][0]
    assert "tools/a.py" in row["task"]
    assert row["link"] == "cand-a"
    assert "判定：merge" in row["reason"]
    assert "tools/dup-a.py" in row["reason"]
    assert "2026-08-01" in row["reason"]
    assert "docs/index.md" in row["reason"]


def test_present_skips_already_presented_candidates(unit, load_config):
    present = unit("present")
    payload = {
        "kind": "judged",
        "version": 1,
        "items": [
            _judged_item("cand-a", "delete"),
            _judged_item("cand-b", "delete"),
        ],
        "presented_candidate_ids": ["cand-a"],
    }
    result = present.process(payload, load_config())
    assert [row["link"] for row in result["rows"]] == ["cand-b"]
    assert result["already_presented_count"] == 1
    assert result["excluded_count"] == 0


def test_present_dates_come_from_presented_on(unit, load_config):
    present = unit("present")
    payload = {
        "kind": "judged",
        "version": 1,
        "items": [_judged_item("cand-a", "delete")],
        "presented_on": "2026-11-08",
    }
    result = present.process(payload, load_config())
    row = result["rows"][0]
    assert row["updated_at"] == "2026-11-08"
    assert row["due_date"] == "2026-11-15"


# --- execute: guard details / executor / filesystem ------------------------


def _exec_row(candidate_id, decision, **overrides):
    row = {
        "candidate_id": candidate_id,
        "task": candidate_id,
        "status": "未着手",
        "reason": "",
        "check_method": "",
        "due_date": "2026-11-15",
        "link": candidate_id,
        "updated_at": "2026-11-08",
        "target": f"docs/{candidate_id}.md",
        "action": decision,
        "user_decision": decision,
    }
    row.update(overrides)
    return row


def _exec_payload(*rows):
    return {"kind": "ledger_rows", "version": 1, "rows": list(rows)}


def test_execute_action_mismatch_with_decision_rejected(unit, load_config):
    execute = unit("execute")
    payload = _exec_payload(_exec_row("cand-1", "delete", action="merge"))
    with pytest.raises(ValueError):
        execute.process(payload, load_config())


def test_execute_uses_injected_executor(unit, load_config):
    execute = unit("execute")
    seen = []

    def executor(row):
        seen.append(row["candidate_id"])
        return "done"

    payload = _exec_payload(_exec_row("cand-1", "delete"), _exec_row("cand-2", "keep"))
    result = execute.process(payload, load_config(), executor=executor)
    assert seen == ["cand-1"]
    items = {i["candidate_id"]: i for i in result["items"]}
    assert items["cand-1"]["result"] == "done"
    assert items["cand-2"]["result"] == "kept"
    assert result["counts"] == {"total": 2, "executed": 1}


def test_execute_executor_failure_is_recorded_not_raised(unit, load_config):
    execute = unit("execute")

    def executor(row):
        raise OSError("対象が無い")

    payload = _exec_payload(_exec_row("cand-1", "delete"))
    result = execute.process(payload, load_config(), executor=executor)
    assert result["items"][0]["result"] == "failed"
    assert result["counts"]["executed"] == 0


def _git_add_all(root):
    """Make every file under *root* git-tracked (init + add)."""
    import subprocess

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)


def test_execute_filesystem_delete_and_merge(unit, load_config, tmp_path):
    execute = unit("execute")
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "gone.md").write_text("old\n", encoding="utf-8")
    (root / "docs" / "src.md").write_text("src\n", encoding="utf-8")
    (root / "docs" / "dst.md").write_text("dst\n", encoding="utf-8")
    # 実書き換えの対象は git 管理下のファイルに限るため、追跡対象にする。
    _git_add_all(root)
    config = load_config(overrides={"execute_root": str(root)})
    payload = _exec_payload(
        _exec_row("cand-1", "delete", target="docs/gone.md"),
        _exec_row(
            "cand-2",
            "merge",
            target="docs/src.md",
            migration_target="docs/dst.md",
        ),
    )
    result = execute.process(payload, config)
    assert result["counts"] == {"total": 2, "executed": 2}
    assert not (root / "docs" / "gone.md").exists()
    assert not (root / "docs" / "src.md").exists()
    assert (root / "docs" / "dst.md").read_text(encoding="utf-8") == "dst\nsrc\n"


def test_execute_filesystem_escape_rejected_before_any_execution(
    unit, load_config, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    inside = root / "docs" / "keep.md"
    inside.write_text("keep\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    config = load_config(overrides={"execute_root": str(root)})
    payload = _exec_payload(
        _exec_row("cand-1", "delete", target="docs/keep.md"),
        _exec_row("cand-2", "delete", target="../outside.md"),
    )
    with pytest.raises(ValueError):
        execute.process(payload, config)
    # Fail-closed: not even the valid first row was executed.
    assert inside.exists()
    assert outside.exists()


def test_execute_filesystem_missing_target_recorded_failed(unit, load_config, tmp_path):
    execute = unit("execute")
    root = tmp_path / "root"
    root.mkdir()
    config = load_config(overrides={"execute_root": str(root)})
    payload = _exec_payload(_exec_row("cand-1", "delete", target="docs/not-there.md"))
    result = execute.process(payload, config)
    assert result["items"][0]["result"] == "failed"
    assert result["counts"]["executed"] == 0


def test_present_output_rows_cannot_be_executed_directly(
    unit, load_config, judged_payload
):
    """D2: rows straight out of present have no user_decision (P0 pins
    this too; kept here as the P3 wiring guarantee)."""
    present = unit("present")
    execute = unit("execute")
    rows = present.process(copy.deepcopy(judged_payload), load_config())
    with pytest.raises(ValueError):
        execute.process(rows, load_config())


def test_execute_filesystem_root_itself_is_rejected(unit, load_config, tmp_path):
    """QA finding: the root itself is not "under" the root -- a target
    resolving to execute_root ("." / "docs/..") must be refused before
    anything runs, so the whole tree can never be deleted."""
    execute = unit("execute")
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    inside = root / "docs" / "keep.md"
    inside.write_text("keep\n", encoding="utf-8")
    config = load_config(overrides={"execute_root": str(root)})
    for bad_target in (".", "docs/.."):
        payload = _exec_payload(_exec_row("cand-1", "delete", target=bad_target))
        with pytest.raises(ValueError):
            execute.process(payload, config)
        assert root.exists()
        assert inside.exists()


def test_execute_row_without_action_rejected(unit, load_config):
    execute = unit("execute")
    row = _exec_row("cand-1", "delete")
    del row["action"]
    with pytest.raises(ValueError):
        execute.process(_exec_payload(row), load_config())
