"""decisions unit -- 仕分け列の読み取り（Issue #19 仕分け記録形式）.

入力 payload は ``rows``（台帳から読み戻した行の列。各行は台帳の
7要素＋仕分け列の値 ``decision_label`` を持つ）／``candidates``
（candidates 形式）／``judged``（judged 形式）を持つ。候補行は
``link`` が candidates の ``id`` と一致する行だけで、仕分けは
``decision_label``（プルダウンの選択値 ``削除``／``統合``／
``符号化``／``保持``）から読み取る。出力は execute へ渡せる
ledger_rows 形式（7要素＋``candidate_id``／``target``／``action``／
``user_decision``、統合・符号化では ``migration_target``）とし、
仕分けが取れなかった候補行は理由コードつきで ``skipped`` に
記録する。

【仕様変更の記録】2026-11-09 の本人の指示で、仕分け方式は
理由列の先頭マーカー（``[削除]`` 等の手書き）から専用タブ
「エントロピー候補」の「仕分け」列（プルダウン選択）へ変更された
（2026-11-08 のマーカー決定時に本人が付けた留保「使いにくければ
あとでまた変えよ」に基づく）。本ファイルの旧テスト群（マーカーの
位置・前置空白・未知マーカー等）は旧仕様を固定していたため、
新仕様への追随として置き換えた。理由列のマーカーはもう読まない
（読まないこと自体を回帰テストで固定する）。
"""

import copy
import json
import os
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, VALID_CONFIG


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


def _row(link, decision_label=None, reason="候補行の理由・前提条件"):
    return {
        "task": f"タスク {link}",
        "status": "未着手",
        "reason": reason,
        "check_method": "証拠を確認する",
        "due_date": "2026-11-15",
        "link": link,
        "updated_at": "2026-11-09",
        "decision_label": decision_label,
    }


def _payload(rows, candidates, judged):
    return {"rows": rows, "candidates": candidates, "judged": judged}


def _single_payload(decision_label, verdict="delete", migration_target=None):
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", verdict, migration_target))
    return _payload([_row("cand-1", decision_label)], candidates, judged)


# --- 4ラベル各々の変換 -------------------------------------------------------


@pytest.mark.parametrize(
    "label,verdict,migration_target",
    [
        ("削除", "delete", None),
        ("統合", "merge", "tools/dest.py"),
        ("符号化", "codify", "tools/code.py"),
        ("保持", "keep", None),
    ],
)
def test_each_label_converts(unit, load_config, label, verdict, migration_target):
    decisions = unit("decisions")
    payload = _single_payload(label, verdict=verdict, migration_target=migration_target)
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


# --- 未仕分け・想定外の値・旧マーカーの扱い -----------------------------------


def test_empty_label_is_recorded_as_no_decision(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload("")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "no_decision"}
    ]


def test_none_label_is_recorded_as_no_decision(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload(None)
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "no_decision"}
    ]


def test_missing_label_key_is_recorded_as_no_decision(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    row = _row("cand-1", "削除")
    del row["decision_label"]
    result = decisions.process(_payload([row], candidates, judged), load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "no_decision"}
    ]


def test_unknown_label_is_recorded_as_unknown_decision(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload("保留")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "unknown_decision"}
    ]


def test_label_with_whitespace_is_not_trimmed(unit, load_config):
    # プルダウンの値は完全一致で判定する。前後の空白が付いた値は
    # 想定外として fail-closed に倒す（表記揺れを実行段へ通さない）。
    decisions = unit("decisions")
    payload = _single_payload(" 削除")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "unknown_decision"}
    ]


def test_non_string_label_is_unknown_decision_without_error(unit, load_config):
    # list のような unhashable な値でも例外にせず unknown_decision にする
    decisions = unit("decisions")
    payload = _single_payload(["削除"])
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "unknown_decision"}
    ]


def test_reason_marker_is_no_longer_read(unit, load_config):
    # 旧方式のマーカーが理由列に残っていても、仕分け列が空なら
    # 仕分けなしとして扱う（マーカー方式の廃止の回帰固定）
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    row = _row("cand-1", None, reason="[削除] 削除する")
    result = decisions.process(_payload([row], candidates, judged), load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "no_decision"}
    ]


def test_label_wins_over_reason_marker(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "keep"))
    row = _row("cand-1", "保持", reason="[削除] 古いメモ")
    result = decisions.process(_payload([row], candidates, judged), load_config())
    assert len(result["rows"]) == 1
    assert result["rows"][0]["user_decision"] == "keep"


# --- 対象行の特定 -----------------------------------------------------------


def test_unrelated_link_row_is_ignored(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    rows = [
        _row("other-task-1", "削除"),
        _row("cand-1", "削除"),
    ]
    result = decisions.process(_payload(rows, candidates, judged), load_config())
    assert len(result["rows"]) == 1
    assert result["rows"][0]["candidate_id"] == "cand-1"
    # 無関係な行は件数（skipped）にも入れない。
    assert result["skipped"] == []


# --- 移行先（migration_target）の解決 ---------------------------------------


def test_merge_with_migration_target_includes_it(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload("統合", verdict="merge", migration_target="tools/dest.py")
    result = decisions.process(payload, load_config())
    assert len(result["rows"]) == 1
    assert result["rows"][0]["migration_target"] == "tools/dest.py"


def test_merge_without_migration_target_is_skipped(unit, load_config):
    decisions = unit("decisions")
    # 判定が delete の候補には移行先が無いため、「統合」でも移行先を
    # 捏造せず missing_migration_target で除外する。
    payload = _single_payload("統合", verdict="delete")
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "missing_migration_target"}
    ]


def test_merge_for_needs_check_candidate_is_skipped(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_needs_check_item("cand-1"))
    payload = _payload([_row("cand-1", "統合")], candidates, judged)
    result = decisions.process(payload, load_config())
    assert result["rows"] == []
    assert result["skipped"] == [
        {"candidate_id": "cand-1", "reason_code": "missing_migration_target"}
    ]


# --- 重複・0件・スキップの記録形式 -------------------------------------------


def test_duplicate_label_rows_both_pass_through(unit, load_config):
    """同一候補に仕分け済み行が複数あれば黙って間引かず両方を通す
    （重複の全体拒否は execute の D2 ガードの役割）。"""
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    rows = [_row("cand-1", "削除"), _row("cand-1", "削除")]
    result = decisions.process(_payload(rows, candidates, judged), load_config())
    assert len(result["rows"]) == 2
    assert [r["candidate_id"] for r in result["rows"]] == ["cand-1", "cand-1"]


def test_zero_decided_rows_is_normal_output(unit, load_config):
    decisions = unit("decisions")
    candidates = _candidates(("cand-1", "docs/target.md"))
    judged = _judged(_judged_item("cand-1", "delete"))
    result = decisions.process(
        _payload([_row("cand-1", None)], candidates, judged), load_config()
    )
    assert result["kind"] == "ledger_rows"
    assert result["rows"] == []
    assert len(result["skipped"]) == 1


def test_skipped_metadata_has_only_candidate_id_and_reason_code(unit, load_config):
    decisions = unit("decisions")
    payload = _single_payload(None)
    result = decisions.process(payload, load_config())
    for entry in result["skipped"]:
        assert set(entry.keys()) == {"candidate_id", "reason_code"}


# --- 設定による対応表の差し替え ---------------------------------------------


def test_config_replaces_label_table(unit, load_config):
    decisions = unit("decisions")
    config = load_config(
        overrides={"decision_labels": {"消す": "delete", "残す": "keep"}}
    )
    candidates = _candidates(("cand-1", "docs/a.md"), ("cand-2", "docs/b.md"))
    judged = _judged(_judged_item("cand-1", "delete"), _judged_item("cand-2", "keep"))
    rows = [_row("cand-1", "消す"), _row("cand-2", "削除")]
    result = decisions.process(_payload(rows, candidates, judged), config)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["candidate_id"] == "cand-1"
    assert result["rows"][0]["user_decision"] == "delete"
    # 差し替え後の対応表に無い「削除」は想定外の値になる。
    assert result["skipped"] == [
        {"candidate_id": "cand-2", "reason_code": "unknown_decision"}
    ]


def test_invalid_decision_labels_config_raises(load_config):
    with pytest.raises(ValueError):
        load_config(overrides={"decision_labels": {}})


@pytest.mark.parametrize(
    "bad_labels",
    [
        {},
        {"": "delete"},
        {"削除": "postpone"},
        {"削除": None},
        ["削除"],
        "delete",
    ],
)
def test_invalid_decision_labels_passed_to_process_raises(
    unit, load_config, bad_labels
):
    decisions = unit("decisions")
    config = dict(load_config())
    config["decision_labels"] = bad_labels
    payload = _single_payload("削除")
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
    rows = [_row("cand-1", "削除"), _row("cand-2", "保持")]
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


# --- decision_labels の unhashable 値（QA 必須-1 回帰） ----------------------
#
# decision_labels の値が list／dict（unhashable）のとき、メンバーシップ
# 判定（``decision in JUDGED_VERDICTS``）が TypeError を送出してはならず、
# 既存の契約どおり ValueError にならなければならない。CLI では設定の
# 誤りとして終了コード 2 で終わること。


@pytest.mark.parametrize(
    "bad_value",
    [
        ["delete"],
        {"verdict": "delete"},
    ],
)
def test_load_config_unhashable_decision_label_value_raises_value_error(
    load_config, bad_value
):
    with pytest.raises(ValueError):
        load_config(overrides={"decision_labels": {"削除": bad_value}})


@pytest.mark.parametrize(
    "bad_value",
    [
        ["delete"],
        {"verdict": "delete"},
    ],
)
def test_process_unhashable_decision_label_value_raises_value_error(
    unit, load_config, bad_value
):
    decisions = unit("decisions")
    config = dict(load_config())
    config["decision_labels"] = {"削除": bad_value}
    payload = _single_payload("削除")
    with pytest.raises(ValueError):
        decisions.process(payload, config)


@pytest.mark.parametrize(
    "bad_value",
    [
        ["delete"],
        {"verdict": "delete"},
    ],
)
def test_validate_decision_labels_unhashable_value_raises_value_error(unit, bad_value):
    decisions = unit("decisions")
    with pytest.raises(ValueError):
        decisions.validate_decision_labels({"削除": bad_value})


@pytest.mark.parametrize(
    "bad_value",
    [
        ["delete"],
        {"verdict": "delete"},
    ],
)
def test_cli_unhashable_decision_label_value_exits_with_code_2(tmp_path, bad_value):
    config_data = copy.deepcopy(VALID_CONFIG)
    config_data["decision_labels"] = {"削除": bad_value}
    config_path = tmp_path / "config.local.json"
    config_path.write_text(
        json.dumps(config_data, ensure_ascii=False), encoding="utf-8"
    )
    input_path = tmp_path / "in.json"
    input_path.write_text(
        json.dumps(_single_payload("削除"), ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = tmp_path / "out.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "src")])
    env["ENTROPY_REDUCTION_CONFIG"] = str(config_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "entropy_reduction.decisions",
            str(input_path),
            str(output_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode == 2, proc.stderr
    assert "TypeError" not in proc.stderr
    assert "unhashable type" not in proc.stderr
    assert "Traceback" not in proc.stderr
