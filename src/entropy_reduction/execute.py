"""第3層：削減の実行（SPEC §2 / §3 execution_record / D2 / D4）.

入力は ledger_rows 形式で、各行は台帳の7要素に加えて ``candidate_id``
／``target``／``action``／``user_decision`` を持つ（仕分け結果を台帳
から読み戻した形）。

D2 の実行ガードは fail-closed：``user_decision``（delete／merge／
codify／keep）が記録されていない行が1件でも混ざれば、全体を拒否して
何も実行しない。行0件・candidate_id の重複・``action`` と
``user_decision`` の不一致も全体を拒否する。present が出した直後の
行（user_decision を持たない）はそのままでは実行できない。

削減操作の実体は executor へ委譲する（``process(payload, config,
executor=None)`` で差し替え可能）。executor が無い場合、設定に
``execute_root`` があればその配下のファイル実体を操作する executor
を、無ければ実データに触らない記録のみの executor を使う。ファイル
実体の executor は、対象パスが ``execute_root`` 配下に解決しない場合
（絶対パス・``..`` による脱出）は実行前に全体を拒否する。個別の操作
失敗はその行の result を ``failed`` として記録する。keep の行は操作
を行わず result を ``kept`` とする。
"""

import datetime
import shutil
from pathlib import Path

from . import contracts
from .cli import run_cli

_VALID_DECISIONS = contracts.JUDGED_VERDICTS
_ACTIONABLE_DECISIONS = frozenset({"delete", "merge", "codify"})


def _config_get(config, key, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _validate_rows(payload):
    """入力を検証し、実行してよい行の列を返す（D2 ガード）。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")
    contracts.validate_ledger_rows(payload)
    rows = payload["rows"]
    if not rows:
        raise ValueError("実行対象の行がありません")
    seen = set()
    for index, row in enumerate(rows):
        context = f"rows[{index}]"
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError(f"{context} に candidate_id がありません")
        if candidate_id in seen:
            raise ValueError(f"candidate_id が重複しています: {candidate_id!r}")
        seen.add(candidate_id)
        decision = row.get("user_decision")
        if not isinstance(decision, str) or not decision.strip():
            raise ValueError(
                f"{context}（{candidate_id}）に本人の仕分け"
                "（user_decision）がありません（D2）"
            )
        if decision not in _VALID_DECISIONS:
            raise ValueError(
                f"{context}（{candidate_id}）の user_decision が不正です: "
                f"{decision!r}"
            )
        action = row.get("action")
        if action is not None and action != decision:
            raise ValueError(
                f"{context}（{candidate_id}）の action と user_decision が"
                f"一致しません: {action!r} != {decision!r}"
            )
    return rows


def _record_executor(row):
    """実データに触らず、実行したことだけを記録する executor。"""
    return "done"


def _resolve_under_root(root, target, context):
    """target を root 配下のパスに解決する。配下から外れれば ValueError。"""
    if not isinstance(target, str) or not target:
        raise ValueError(f"{context} に対象パスがありません")
    base = Path(root).expanduser().resolve()
    resolved = (base / target).resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(
            f"{context} が execute_root の配下から外れています: {target!r}"
        )
    return resolved


def _make_filesystem_executor(root, rows):
    """ファイル実体を操作する executor を作る（実行前に全行を事前検証）。"""
    for row in rows:
        if row["user_decision"] not in _ACTIONABLE_DECISIONS:
            continue
        candidate_id = row["candidate_id"]
        _resolve_under_root(root, row.get("target"), candidate_id)
        if row["user_decision"] == "merge":
            _resolve_under_root(
                root, row.get("migration_target"), f"{candidate_id} の移行先"
            )

    def _execute(row):
        decision = row["user_decision"]
        target = _resolve_under_root(root, row.get("target"), row["candidate_id"])
        if decision == "merge":
            destination = _resolve_under_root(
                root, row.get("migration_target"), "移行先"
            )
            data = target.read_bytes()
            with destination.open("ab") as handle:
                handle.write(data)
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return "done"

    return _execute


def _default_executor(config, rows):
    root = _config_get(config, "execute_root")
    if root:
        return _make_filesystem_executor(root, rows)
    return _record_executor


def process(payload, config, executor=None):
    """本人が仕分けた台帳行を実行し、execution_record を返す。"""
    rows = _validate_rows(payload)
    if executor is None:
        executor = _default_executor(config, rows)

    items = []
    executed = 0
    for row in rows:
        decision = row["user_decision"]
        candidate_id = row["candidate_id"]
        if decision == "keep":
            items.append(
                {"candidate_id": candidate_id, "action": "keep", "result": "kept"}
            )
            continue
        try:
            result = executor(row)
        except Exception:
            result = "failed"
        if not isinstance(result, str) or not result:
            result = "failed"
        items.append(
            {"candidate_id": candidate_id, "action": decision, "result": result}
        )
        if result == "done":
            executed += 1

    record = {
        "kind": "execution_record",
        "version": 1,
        "executed_at": datetime.date.today().isoformat(),
        "items": items,
        "counts": {"total": len(rows), "executed": executed},
    }
    contracts.validate_execution_record(record)
    return record


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
