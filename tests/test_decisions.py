"""decisions unit -- 仕分けマーカー読み取り（Issue #19 仕分け記録形式）.

入力 payload は ``rows``（台帳から読み戻した行の列。各行は台帳の
7要素を持つ）／``candidates``（candidates 形式）／``judged``
（judged 形式）を持つ。候補行は ``link`` が candidates の ``id`` と
一致する行だけで、仕分けは ``reason`` の先頭のマーカー
（``[削除]``／``[統合]``／``[符号化]``／``[保持]``）から読み取る。
出力は execute へ渡せる ledger_rows 形式（7要素＋``candidate_id``／
``target``／``action``／``user_decision``、統合・符号化では
``migration_target``）とし、仕分けが取れなかった候補行は理由コード
つきで ``skipped`` に記録する。
"""

import pytest


def _candidates(*entries):
    return {
        "kind": "candidates",
        "version": 1,
        "items": [
            {
                "id": cid,
                "source": "weekly",
                "target": target,
                "category": "other",
                "title": f"候補 {cid}",
                "detail": "detail",
            }
            for cid, target in entries
        ],
    }


def _judged_item(candidate_id, verdict, migration_target=None):
    return {
        "candidate_id": candidate_id,
        "status": "judged",
        "evidence": {
            "last_used": "2026-08-01",
            "references": [],
            "impact": "影響は限定的",
        },
        "verdict": verdict,
        "migration_target": migration_target,
        "reason": "判定理由",
    }


def _needs_check_item(candidate_id):
    return {
        "candidate_id": candidate_id,
        "status": "needs_check",
        "evidence": {
            "last_used": "2026-08-01",
            "references": [],
            "impact": None,
        },
        "verdict": None,
        "migration_target": None,
        "reason": "証拠が不足している",
    }


def _judged(*items):
    return {"kind": "judged", "version": 1, "items": list(items)}


def _row(link, reason):
    return {
        "task": f"タスク {link}",
        "status": "未着手",
        "reason": reason,
        "check_method": "証拠を確認する",
        "due_date": "2026-11-15",
        "link": link,
        "updated_at": "2026-11-08",
    }


def _payload(rows, candidates, judged):
    return {"rows": rows, "candidates": candidates, "judged": judged}


def _single_payload(reason, verdict="delete", migration_target=None):
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", verdict, migration_target))
    return _payload([_row("cand-1", reason)], candidates, judged)


# --- 4マーカー各々の変換 ---------------------------------------------------


@pytest.mark.parametrize(
    "marker,verdict,migration_target",
    [
        ("[削除]", "delete", None),
        ("[統合]", "merge", "tools/dest.py"),
        ("[符号化]", "codify", "tools/code.py"),
        ("[保持]", "keep", None),
    ],
)
def test_each_marker_converts(unit, load_config, marker, verdict, migration_target):
    decisions = unit("decisions")
    payload = _single_payload(
        f"{marker} 仕分けメモ", verdict=verdict, migration_target=migration_target
    )
    result = decisions.process(payload, load_config())
    assert result["kind"] == "ledger_rows"
    assert result["version"] == 1
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["candidate_id"] == "cand-1"
    assert row["link"] == "cand-1"
    assert row["target"] == "docs/target.md"
    assert row["action"] == verdict
    assert row["user_decision"] == verdict
    if verdict in ("merge", "codify"):
        assert row["migration_target"] == migration_target
    assert result["skipped"] == []


# --- マーカーの位置・未知マーカー・空白 -------------------------------------


def test_marker_not_at_start_is_invalid(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload("メモ [削除] してください")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [{"candidate_id": "cand-1", "reason_code": "no_marker"}]


def test_unknown_marker_is_recorded_as_unknown(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload("[保留] あとで決める")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "unknown_marker"}
    ]


def test_no_marker_is_recorded_as_no_marker(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload("まだ仕分けしていない")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [{"candidate_id": "cand-1", "reason_code": "no_marker"}]


@pytest.mark.parametrize("prefix", [" ", "  ", "\t", "　", "　 "])
def test_leading_whitespace_is_allowed(unit, load_config, prefix):
    decisions = unit("decisions")
    payload = _single_payload(f"{prefix}[削除] 削除する")
    result = decisions.process(payload, load_config())
    assert len(result["rows"]) == 1
    assert result["rows"][0]["user_decision"] == "delete"


# --- 対象行の特定 -----------------------------------------------------------


def test_unrelated_link_row_is_ignored(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    rows = [
        _row("other-task-1", "[削除] 候補とは無関係な一般行"),
        _row("cand-1", "[削除] 削除する"),
    ]
    result = decisions.process(_payload(rows, candidates, judged), load_config())
    assert len(result["rows"]) == 1
    assert result["rows"][0]["candidate_id"] == "cand-1"
    # 無関係な行は件数（skipped）にも入れない。
    assert result["skipped"] == []


# --- 移行先（migration_target）の解決 ---------------------------------------


def test_merge_with_migration_target_includes_it(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload(
        "[統合] 統合する", verdict="merge", migration_target="tools/dest.py"
    )
    result = decisions.process(payload, load_config())
    assert len(result["rows"]) == 1
    assert result["rows"][0]["migration_target"] == "tools/dest.py"


def test_merge_without_migration_target_is_skipped(unit, load_config):
    decisions = unit("decisions")
    # 判定が delete の候補には移行先が無いため、[統合] でも移行先を
    # 捏造せず missing_migration_target で除外する。
    payload = _single_payload("[統合] 統合する", verdict="delete")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "missing_migration_target"}
    ]


def test_merge_for_needs_check_candidate_is_skipped(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_needs_check_item("cand-1"))
    payload = _payload([_row("cand-1", "[統合] 統合する")], candidates, judged)
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "missing_migration_target"}
    ]


# --- 重複・0件・スキップの記録形式 -------------------------------------------


def test_duplicate_marker_rows_both_pass_through(unit, load_config):
    """同一候補にマーカーつき行が複数あれば黙って間引かず両方を通す
    （重複の全体拒否は execute の D2 ガードの役割）。"""
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    rows = [_row("cand-1", "[削除] 1行目"), _row("cand-1", "[削除] 2行目")]
    result = decisions.process(_payload(rows, candidates, judged), load_config())
    assert len(result["rows"]) == 2
    assert [r["candidate_id"] for r in result["rows"]] == ["cand-1", "cand-1"]


def test_zero_decided_rows_is_normal_output(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    result = decisions.process(
        _payload([_row("cand-1", "未仕分け")], candidates, judged), load_config()
    )
    assert result["kind"] == "ledger_rows"
    assert result["rows"] == []
    assert len(result["skipped"]) == 1


def test_skipped_metadata_has_only_candidate_id_and_reason_code(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload("本文に秘情報っぽい文字列を含む未仕分け行")
    result = decisions.process(payload, load_config())
    for entry in result["skipped"]:
        assert set(entry.keys()) == {"candidate_id", "reason_code"}


# --- 設定による対応表の差し替え ---------------------------------------------


def test_config_replaces_marker_table(unit, load_config):
    decisions = unit("decisions")
    config = load_config(
        overrides={"decision_markers": {"[消す]": "delete", "[残す]": "keep"}}
    )
    candidates = _candidates(("cand-1", "docs/a.md"), ("cand-2", "docs/b.md"))
    judged = _judged(_judged_item("cand-1", "delete"), _judged_item("cand-2", "keep"))
    rows = [_row("cand-1", "[消す] 消す"), _row("cand-2", "[削除] 旧形式")]
    result = decisions.process(_payload(rows, candidates, judged), config)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["candidate_id"] == "cand-1"
    assert result["rows"][0]["user_decision"] == "delete"
    # 差し替え後の対応表に無い [削除] は未知マーカーになる。
    assert result["skipped"] == [
        {"candidate_id": "cand-2", "reason_code": "unknown_marker"}
    ]


def test_invalid_decision_markers_config_raises(load_config):
    with pytest.raises(ValueError):
        load_config(overrides={"decision_markers": {}})


@pytest.mark.parametrize(
    "bad_markers",
    [
        {},
        {"": "delete"},
        {"[削除]": "postpone"},
        {"[削除]": None},
        ["[削除]"],
        "delete",
    ],
)
def test_invalid_decision_markers_passed_to_process_raises(
    unit, load_config, bad_markers
):
    decisions = unit("decisions")
    config = dict(load_config())
    config["decision_markers"] = bad_markers
    payload = _single_payload("[削除] 削除する")
    with pytest.raises(ValueError):
        decisions.process(payload, config)


# --- 出力の検証と execute への結合 -------------------------------------------


def test_output_passes_contracts_and_chains_to_execute(unit, load_config):
    decisions = unit("decisions")
    contracts = unit("contracts")
    execute = unit("execute")
    config = load_config()
    candidates = _candidates(("cand-1", "docs/a.md"), ("cand-2", "docs/b.md"))
    judged = _judged(_judged_item("cand-1", "delete"), _judged_item("cand-2", "keep"))
    rows = [_row("cand-1", "[削除] 削除する"), _row("cand-2", "[保持] 残す")]
    result = decisions.process(_payload(rows, candidates, judged), config)
    assert contracts.validate_ledger_rows(result) in (None, True)

    # 設定に execute_root が無いため記録のみ executor になる。
    record = execute.process(result, config)
    assert record["kind"] == "execution_record"
    items = {i["candidate_id"]: i for i in record["items"]}
    assert items["cand-1"]["action"] == "delete"
    assert items["cand-1"]["result"] == "done"
    assert items["cand-2"]["action"] == "keep"
    assert record["counts"]["total"] == 2
