"""第1層：総量の計測（SPEC §3 snapshot / D5 / D10）.

入力（measure_input）：``taken_at`` / ``targets``（各要素は
``name`` / ``code_lines`` / ``md_lines``）/ ``counts``
（``crons`` / ``hooks`` / ``custom_skills`` / ``ledger_open_rows`` /
``open_issues``。欠けたキーは 0 とする）。

D5: 同じ対象名は重ねて数えない。同名 target は最初の出現だけを数える。
棚卸しランの状態ファイルは、設定 ``harness_state_dir``（``~`` 展開する）
内の ``*.json`` をファイル名順に読み、snapshot の ``sources`` に
``{"type": "harness_run", "run_id": ..., "candidates_by_state": {...}}``
として載せる。状態ファイルに ``candidates_by_state`` が無く
``candidates``（state 付きのリスト）がある場合は state 別に数える
（inventory-harness の集計定義と同じ）。
"""

import json
from pathlib import Path

from .cli import run_cli

COUNT_KEYS = (
    "crons",
    "hooks",
    "custom_skills",
    "ledger_open_rows",
    "open_issues",
)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _config_get(config, key, default=None):
    """config が dict でもオブジェクトでも値を読めるようにする。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _checked_line_count(target, key, name):
    value = target.get(key, 0)
    if not _is_int(value):
        raise ValueError(
            f"target {name!r} の {key} は整数でなければなりません: {value!r}"
        )
    if value < 0:
        raise ValueError(f"target {name!r} の {key} は 0 以上でなければなりません")
    return value


def _unique_targets(targets):
    """D5: 対象名で一意化する（最初の出現を残す。順序は保持する）。"""
    seen = {}
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError(f"target は dict でなければなりません: {target!r}")
        name = target.get("name")
        if not isinstance(name, str) or name == "":
            raise ValueError(f"target に name がありません: {target!r}")
        if name in seen:
            continue
        seen[name] = (
            _checked_line_count(target, "code_lines", name),
            _checked_line_count(target, "md_lines", name),
        )
    return seen


def _harness_sources(config):
    state_dir_value = _config_get(config, "harness_state_dir")
    if not state_dir_value:
        return []
    state_dir = Path(str(state_dir_value)).expanduser()
    if not state_dir.is_dir():
        return []

    sources = []
    for state_path in sorted(state_dir.glob("*.json"), key=lambda p: p.name):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"棚卸し状態ファイルの JSON が壊れています: {state_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"棚卸し状態ファイルは JSON オブジェクトでなければなりません: "
                f"{state_path}"
            )

        if isinstance(data.get("candidates_by_state"), dict):
            candidates_by_state = dict(data["candidates_by_state"])
        elif isinstance(data.get("candidates"), list):
            candidates_by_state = {}
            for candidate in data["candidates"]:
                if isinstance(candidate, dict):
                    state = candidate.get("state")
                    if state is not None:
                        candidates_by_state[state] = (
                            candidates_by_state.get(state, 0) + 1
                        )
        else:
            candidates_by_state = {}

        sources.append(
            {
                "type": "harness_run",
                "run_id": data.get("run_id", state_path.stem),
                "candidates_by_state": candidates_by_state,
            }
        )
    return sources


def process(payload, config):
    """measure_input から snapshot を組み立てる。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")
    if "targets" not in payload:
        raise ValueError("入力に targets がありません")
    targets = payload["targets"]
    if not isinstance(targets, list):
        raise ValueError("targets はリストでなければなりません")

    unique = _unique_targets(targets)

    counts = payload.get("counts") or {}
    if not isinstance(counts, dict):
        raise ValueError("counts は dict でなければなりません")
    totals = {
        "repos": len(unique),
        "code_lines": sum(code for code, _ in unique.values()),
        "md_lines": sum(md for _, md in unique.values()),
    }
    for key in COUNT_KEYS:
        value = counts.get(key, 0)
        if not _is_int(value):
            raise ValueError(f"counts.{key} は整数でなければなりません: {value!r}")
        if value < 0:
            raise ValueError(f"counts.{key} は 0 以上でなければなりません: {value!r}")
        totals[key] = value

    return {
        "kind": "snapshot",
        "version": 1,
        "taken_at": payload.get("taken_at"),
        "totals": totals,
        "sources": _harness_sources(config),
    }


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
