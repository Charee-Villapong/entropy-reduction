"""execute の git ガードと対象名ベースの基点解決（SPEC §7.3）。

実書き換えの対象は git 管理下（追跡対象）のファイルに限る。実在する
対象が追跡対象でなければ、その行は書き換えず result を
``skipped_not_in_git`` として記録する。対象パスの先頭セグメントが
設定 ``execute_target_bases`` に対応付けられていれば、その基点配下
として解決する。
"""

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が必要です")


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


def _init_repo(root, tracked_files):
    """root を git リポジトリにし、tracked_files を追跡対象にする。"""
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", str(root))
    for relative in tracked_files:
        _git("-C", str(root), "add", "--", relative)


def _row(candidate_id, decision, target, migration_target=None):
    row = {
        "candidate_id": candidate_id,
        "task": f"対象 {target}",
        "status": "未着手",
        "reason": "",
        "check_method": "",
        "due_date": "2026-11-15",
        "link": candidate_id,
        "updated_at": "2026-11-13",
        "target": target,
        "action": decision,
        "user_decision": decision,
    }
    if migration_target is not None:
        row["migration_target"] = migration_target
    return row


def _results(execute, payload, config):
    result = execute.process(payload, config)
    return {item["candidate_id"]: item["result"] for item in result["items"]}


def test_tracked_file_is_deleted(unit, load_config, make_execute_payload, tmp_path):
    execute = unit("execute")
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    target = root / "docs" / "gone.md"
    target.write_text("old\n", encoding="utf-8")
    _init_repo(root, ["docs/gone.md"])
    config = load_config(overrides={"execute_root": str(root)})
    payload = make_execute_payload(rows=[_row("cand-1", "delete", "docs/gone.md")])
    assert _results(execute, payload, config) == {"cand-1": "done"}
    assert not target.exists()


def test_existing_file_outside_git_is_skipped(
    unit, load_config, make_execute_payload, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    target = root / "docs" / "keep.md"
    target.write_text("keep\n", encoding="utf-8")
    config = load_config(overrides={"execute_root": str(root)})
    payload = make_execute_payload(rows=[_row("cand-1", "delete", "docs/keep.md")])
    assert _results(execute, payload, config) == {"cand-1": "skipped_not_in_git"}
    assert target.exists()


def test_untracked_file_inside_repo_is_skipped(
    unit, load_config, make_execute_payload, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "tracked.md").write_text("tracked\n", encoding="utf-8")
    untracked = root / "docs" / "untracked.md"
    untracked.write_text("untracked\n", encoding="utf-8")
    _init_repo(root, ["docs/tracked.md"])
    config = load_config(overrides={"execute_root": str(root)})
    payload = make_execute_payload(rows=[_row("cand-1", "delete", "docs/untracked.md")])
    assert _results(execute, payload, config) == {"cand-1": "skipped_not_in_git"}
    assert untracked.exists()


def test_skipped_row_does_not_block_other_rows(
    unit, load_config, make_execute_payload, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / "docs").mkdir(parents=True)
    gone = repo / "docs" / "gone.md"
    gone.write_text("old\n", encoding="utf-8")
    stray = root / "stray.md"
    stray.write_text("stray\n", encoding="utf-8")
    _init_repo(repo, ["docs/gone.md"])
    config = load_config(overrides={"execute_root": str(root)})
    payload = make_execute_payload(
        rows=[
            _row("cand-1", "delete", "repo/docs/gone.md"),
            _row("cand-2", "delete", "stray.md"),
        ]
    )
    assert _results(execute, payload, config) == {
        "cand-1": "done",
        "cand-2": "skipped_not_in_git",
    }
    assert not gone.exists()
    assert stray.exists()


def test_merge_into_untracked_destination_is_skipped(
    unit, load_config, make_execute_payload, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    source = root / "docs" / "src.md"
    source.write_text("src\n", encoding="utf-8")
    destination = root / "docs" / "dst.md"
    destination.write_text("dst\n", encoding="utf-8")
    _init_repo(root, ["docs/src.md"])
    config = load_config(overrides={"execute_root": str(root)})
    payload = make_execute_payload(
        rows=[
            _row(
                "cand-1",
                "merge",
                "docs/src.md",
                migration_target="docs/dst.md",
            )
        ]
    )
    assert _results(execute, payload, config) == {"cand-1": "skipped_not_in_git"}
    assert source.exists()
    assert destination.read_text(encoding="utf-8") == "dst\n"


def test_directory_with_untracked_file_inside_is_skipped(
    unit, load_config, make_execute_payload, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "root"
    tree = root / "tree"
    tree.mkdir(parents=True)
    (tree / "tracked.md").write_text("tracked\n", encoding="utf-8")
    (tree / "untracked.md").write_text("untracked\n", encoding="utf-8")
    _init_repo(root, ["tree/tracked.md"])
    config = load_config(overrides={"execute_root": str(root)})
    payload = make_execute_payload(rows=[_row("cand-1", "delete", "tree")])
    assert _results(execute, payload, config) == {"cand-1": "skipped_not_in_git"}
    assert tree.exists()


def test_target_bases_resolves_named_target(
    unit, load_config, make_execute_payload, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "mirrors"
    root.mkdir()
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    target = skills / "demo" / "SKILL.md"
    target.write_text("skill\n", encoding="utf-8")
    _init_repo(skills, ["demo/SKILL.md"])
    config = load_config(
        overrides={
            "execute_root": str(root),
            "execute_target_bases": {"custom-skills": str(skills)},
        }
    )
    payload = make_execute_payload(
        rows=[_row("cand-1", "delete", "custom-skills/demo/SKILL.md")]
    )
    assert _results(execute, payload, config) == {"cand-1": "done"}
    assert not target.exists()


def test_target_bases_still_enforces_containment(
    unit, load_config, make_execute_payload, tmp_path
):
    execute = unit("execute")
    root = tmp_path / "mirrors"
    root.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    config = load_config(
        overrides={
            "execute_root": str(root),
            "execute_target_bases": {"custom-skills": str(skills)},
        }
    )
    payload = make_execute_payload(
        rows=[_row("cand-1", "delete", "custom-skills/../outside.md")]
    )
    with pytest.raises(ValueError):
        execute.process(payload, config)
    assert outside.exists()


def test_execute_target_bases_must_be_a_dict(load_config):
    with pytest.raises(ValueError):
        load_config(overrides={"execute_target_bases": ["x"]})


def test_execute_target_bases_values_must_be_non_empty_strings(load_config):
    with pytest.raises(ValueError):
        load_config(overrides={"execute_target_bases": {"custom-skills": ""}})


def test_execute_root_must_be_a_non_empty_string(load_config):
    with pytest.raises(ValueError):
        load_config(overrides={"execute_root": ""})
