"""P4：定期タスク化の配線層（cycle）.

既存ユニット（measure／detect／present）を交換形式経由で接続し、
実環境からの入力収集（対象列挙・走査・棚卸し候補の変換）と
運用状態（提示済み管理・履歴）を担う。

CLI：``python -m entropy_reduction.cycle <subcommand>``
終了コードは共通規約（0=正常／1=交換形式の違反／2=設定の誤り）。
cycle が追加で必須とする設定キー（``harness_config_path``／
``cycle_state_dir``）の欠落・不正は設定エラー（終了コード 2）とし、
それ以外の ValueError は終了コード 1 とする。
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from . import contracts, detect, linecount, measure, present
from .cli import (
    EXIT_CONFIG_ERROR,
    EXIT_FORMAT_ERROR,
    EXIT_OK,
    ConfigLoadError,
    resolve_config,
)

PRUNED_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        ".pytest_cache",
        ".mypy_cache",
    }
)

TERMINAL_STATES = frozenset({"reported", "rejected_as_spec"})

VIEWPOINT_CATEGORIES = {
    "skill-md-bloat": "bloat",
    "md-redundancy": "duplicate",
}

COUNT_KEYS = measure.COUNT_KEYS

_CYCLE_CONFIG_KEYS = ("harness_config_path", "cycle_state_dir")


def _config_get(config, key, default=None):
    """config が dict でもオブジェクトでも値を読めるようにする。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _expand_path(value):
    return Path(str(value)).expanduser()


def _parse_day(value, context="today"):
    if not isinstance(value, str):
        raise ValueError(f"{context} は YYYY-MM-DD 形式でなければなりません")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"{context} は YYYY-MM-DD 形式でなければなりません: {value!r}"
        ) from None


def _resolve_today(value):
    if value is None:
        return datetime.date.today().isoformat()
    return _parse_day(value).isoformat()


def _read_json(path, context="ファイル"):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{context}を読み込めません: {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{context}の JSON が壊れています: {path}: {exc}") from exc


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _require_cycle_config(config):
    """cycle の追加必須キーを検証する（欠落・不正は設定エラー）。"""
    for key in _CYCLE_CONFIG_KEYS:
        value = _config_get(config, key)
        if not isinstance(value, str) or value == "":
            raise ConfigLoadError(
                f"cycle の設定キー {key} がありません、または不正です: {value!r}"
            )
    return config


def stable_key(candidate):
    """候補の安定キー（``source|target|category|title``）を返す。"""
    return "|".join(
        str(candidate.get(key, "")) for key in ("source", "target", "category", "title")
    )


def compute_metrics(items, candidates):
    """走査項目と候補から重複率・死蔵率などの指標を算出する。"""
    scanned_items = len(items)
    dead_items = sum(
        1
        for candidate in candidates
        if candidate.get("category") == "dead" and candidate.get("source") == "weekly"
    )

    signature_groups = {}
    for item in items:
        signature = item.get("signature")
        if isinstance(signature, str) and signature != "":
            signature_groups.setdefault(signature, []).append(item.get("target"))
    duplicate_targets = set()
    for targets in signature_groups.values():
        if len(targets) >= 2:
            duplicate_targets.update(targets)
    for candidate in candidates:
        if (
            candidate.get("category") == "duplicate"
            and candidate.get("source") == "embedding"
        ):
            duplicate_targets.add(candidate.get("target"))
    duplicate_targets.discard(None)
    duplicate_involved = len(duplicate_targets)

    if scanned_items == 0:
        dead_rate = None
        duplicate_rate = None
    else:
        dead_rate = round(dead_items / scanned_items, 4)
        duplicate_rate = round(duplicate_involved / scanned_items, 4)
    return {
        "scanned_items": scanned_items,
        "dead_items": dead_items,
        "dead_rate": dead_rate,
        "duplicate_involved_items": duplicate_involved,
        "duplicate_rate": duplicate_rate,
    }


def _git_last_commit_ts(root, relative_posix):
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "-1",
                "--format=%ct",
                "--",
                relative_posix,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    output = (proc.stdout or "").strip()
    if not output:
        return None
    try:
        return int(output.splitlines()[0].strip())
    except (ValueError, IndexError):
        return None


def _last_used_days_ago(root, file_path, relative_posix, baseline_ts, is_git):
    used_ts = None
    if is_git:
        used_ts = _git_last_commit_ts(root, relative_posix)
    if used_ts is None:
        try:
            used_ts = file_path.stat().st_mtime
        except OSError:
            used_ts = baseline_ts
    diff = baseline_ts - used_ts
    if diff <= 0:
        return 0
    return int(diff // 86400)


def scan_target(target, today):
    """1対象を再帰走査し、行数集計と item／document を返す。"""
    if not isinstance(target, dict):
        raise ValueError(f"target は dict でなければなりません: {target!r}")
    name = target.get("name")
    raw_path = target.get("path")
    if not isinstance(name, str) or name == "":
        raise ValueError(f"target に name がありません: {target!r}")
    if raw_path is None:
        raise ValueError(f"target に path がありません: {target!r}")
    root = _expand_path(raw_path)
    day = _parse_day(today)
    baseline = datetime.datetime.combine(day, datetime.time(23, 59, 59))
    baseline_ts = baseline.timestamp()
    is_git = (root / ".git").exists()

    code_lines = 0
    md_lines = 0
    items = []
    documents = []
    documents_skipped = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNED_DIRS)
        current = Path(dirpath)
        for filename in sorted(filenames):
            file_path = current / filename
            try:
                relative = file_path.relative_to(root)
            except ValueError:
                continue
            relative_posix = relative.as_posix()
            category = linecount.categorize(relative_posix)
            try:
                raw = file_path.read_bytes()
            except OSError:
                if category == "md":
                    documents_skipped += 1
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                if category == "md":
                    documents_skipped += 1
                continue
            lines = linecount.count_lines(text)
            if category == "code":
                code_lines += lines
            elif category == "md":
                md_lines += lines
                item_target = f"{name}/{relative_posix}"
                items.append(
                    {
                        "target": item_target,
                        "md_lines": lines,
                        "last_used_days_ago": _last_used_days_ago(
                            root, file_path, relative_posix, baseline_ts, is_git
                        ),
                        "signature": hashlib.sha256(raw).hexdigest(),
                    }
                )
                documents.append({"target": item_target, "text": text})
    return {
        "name": name,
        "code_lines": code_lines,
        "md_lines": md_lines,
        "items": items,
        "documents": documents,
        "documents_skipped": documents_skipped,
    }


def convert_inventory_candidates(config):
    """棚卸しランの状態ファイルから detect の inventory_candidates を作る。"""
    state_dir_value = _config_get(config, "harness_state_dir")
    if not state_dir_value:
        return []
    state_dir = _expand_path(state_dir_value)
    if not state_dir.is_dir():
        return []

    results = []
    seen = set()
    for state_path in sorted(state_dir.glob("*.json"), key=lambda p: p.name):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"棚卸し状態ファイルの JSON が壊れています: {state_path}: {exc}"
            ) from exc
        except OSError as exc:
            raise ValueError(
                f"棚卸し状態ファイルを読み込めません: {state_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"棚卸し状態ファイルは JSON オブジェクトでなければなりません: "
                f"{state_path}"
            )
        run_id = data.get("run_id") or state_path.stem
        candidates = data.get("candidates") or []
        if not isinstance(candidates, list):
            raise ValueError(
                f"棚卸し状態ファイルの candidates はリストでなければなりません: "
                f"{state_path}"
            )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            state = candidate.get("state")
            if state in TERMINAL_STATES:
                continue
            dedupe_key = (candidate.get("target"), candidate.get("title"))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            viewpoint = candidate.get("viewpoint")
            results.append(
                {
                    "target": candidate.get("target"),
                    "category": VIEWPOINT_CATEGORIES.get(viewpoint, "other"),
                    "title": candidate.get("title"),
                    "detail": (
                        f"棚卸しラン {run_id} の候補"
                        f"（観点：{viewpoint}／状態：{state}）"
                    ),
                }
            )
    return results


def _enumerate_targets(config):
    harness_config_path = _expand_path(_config_get(config, "harness_config_path"))
    if not harness_config_path.is_file():
        raise ValueError(f"harness 設定ファイルがありません: {harness_config_path}")
    data = _read_json(harness_config_path, context="harness 設定ファイル")
    if not isinstance(data, dict):
        raise ValueError("harness 設定ファイルは JSON オブジェクトでなければなりません")
    targets_config = data.get("targets") or {}
    if not isinstance(targets_config, dict):
        raise ValueError("harness 設定の targets は dict でなければなりません")
    exclude = set(targets_config.get("exclude") or [])

    results = []
    mirrors_value = targets_config.get("mirrors_dir")
    if mirrors_value:
        mirrors_dir = _expand_path(mirrors_value)
        if mirrors_dir.is_dir():
            for child in sorted(mirrors_dir.iterdir(), key=lambda p: p.name):
                if not child.is_dir():
                    continue
                if child.name in exclude:
                    continue
                if not (child / ".git").exists():
                    continue
                results.append({"name": child.name, "path": str(child)})
    extra_targets = targets_config.get("extra_targets") or []
    if not isinstance(extra_targets, list):
        raise ValueError("harness 設定の extra_targets はリストでなければなりません")
    for entry in extra_targets:
        if not isinstance(entry, dict):
            raise ValueError(
                f"extra_targets の要素は dict でなければなりません: {entry!r}"
            )
        entry_name = entry.get("name")
        entry_path = entry.get("path")
        if entry_name in exclude:
            continue
        results.append({"name": entry_name, "path": str(entry_path)})
    results.sort(key=lambda entry: str(entry.get("name", "")))
    return results


def _validate_counts(data):
    if not isinstance(data, dict):
        raise ValueError("counts は JSON オブジェクトでなければなりません")
    if set(data.keys()) != set(COUNT_KEYS):
        raise ValueError(
            f"counts のキーは {sorted(COUNT_KEYS)} ちょうどでなければなりません: "
            f"{sorted(data.keys())}"
        )
    validated = {}
    for key in COUNT_KEYS:
        value = data[key]
        if not _is_int(value):
            raise ValueError(f"counts.{key} は整数でなければなりません: {value!r}")
        if value < 0:
            raise ValueError(f"counts.{key} は 0 以上でなければなりません: {value!r}")
        validated[key] = value
    return validated


def _read_presented(cycle_state_dir):
    presented_path = Path(cycle_state_dir) / "presented.json"
    if not presented_path.is_file():
        return {"version": 1, "presented": []}
    data = _read_json(presented_path, context="提示済み状態ファイル")
    if not isinstance(data, dict):
        raise ValueError("提示済み状態ファイルは JSON オブジェクトでなければなりません")
    presented = data.get("presented") or []
    if not isinstance(presented, list):
        raise ValueError("提示済み状態の presented はリストでなければなりません")
    return {"version": 1, "presented": list(presented)}


def _presented_keys(presented_data):
    keys = set()
    for entry in presented_data.get("presented", []):
        if isinstance(entry, dict) and entry.get("key") is not None:
            keys.add(entry["key"])
    return keys


def _count_stages(rows):
    almost_certain = 0
    needs_check = 0
    for row in rows:
        task = row.get("task", "") if isinstance(row, dict) else ""
        if isinstance(task, str) and task.startswith("【ほぼ確実"):
            almost_certain += 1
        elif isinstance(task, str) and task.startswith("【要確認"):
            needs_check += 1
    return almost_certain, needs_check


def _do_prepare(args, config):
    today = _resolve_today(args.today)
    counts_data = _read_json(_expand_path(args.counts), context="counts ファイル")
    counts = _validate_counts(counts_data)

    targets = _enumerate_targets(config)
    scanned = [scan_target(target, today) for target in targets]

    measure_targets = [
        {
            "name": result["name"],
            "code_lines": result["code_lines"],
            "md_lines": result["md_lines"],
        }
        for result in scanned
    ]
    all_items = []
    all_documents = []
    documents_skipped = 0
    for result in scanned:
        all_items.extend(result["items"])
        all_documents.extend(result["documents"])
        documents_skipped += result["documents_skipped"]

    inventory_candidates = convert_inventory_candidates(config)

    measure_input = {
        "taken_at": today,
        "targets": measure_targets,
        "counts": counts,
    }
    snapshot = measure.process(measure_input, config)

    detect_input = {
        "snapshot": snapshot,
        "inventory_candidates": inventory_candidates,
        "items": all_items,
        "documents": all_documents,
    }
    candidates = detect.process(detect_input, config)
    metrics = compute_metrics(all_items, candidates["items"])

    if args.workdir:
        workdir = _expand_path(args.workdir)
    else:
        cycle_state_dir = _expand_path(_config_get(config, "cycle_state_dir"))
        workdir = cycle_state_dir / "runs" / today
    workdir.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_date": today,
        "totals": snapshot["totals"],
        "sources": snapshot["sources"],
        "candidates_total": len(candidates["items"]),
        "metrics": metrics,
        "documents_skipped": documents_skipped,
    }
    _write_json(workdir / "measure_input.json", measure_input)
    _write_json(workdir / "snapshot.json", snapshot)
    _write_json(workdir / "candidates.json", candidates)
    _write_json(workdir / "prepare_summary.json", summary)
    return summary


def _order_present_rows(rows, candidates):
    """「ほぼ確実」の束ねグループ順を候補の初出順に整える。

    present はカテゴリ名の辞書順に束ねるが、SPEC が固定しているのは
    「同種の行が隣り合う」ことだけでグループ間の順序は未固定。
    cycle の受け入れテストは候補順（初出順）を期待するため、
    グループ内部の順序と隣接は present の結果のまま保ち、
    グループ間の並びだけを候補の初出順に合わせる。
    「要確認」の行は present と同様に末尾に置く。
    """
    category_by_id = {}
    category_first_index = {}
    for index, candidate in enumerate(candidates.get("items", [])):
        if not isinstance(candidate, dict):
            continue
        category_by_id[candidate.get("id")] = candidate.get("category") or ""
        category = candidate.get("category") or ""
        if category not in category_first_index:
            category_first_index[category] = index

    almost_rows = []
    needs_rows = []
    for row in rows:
        task = row.get("task", "") if isinstance(row, dict) else ""
        if isinstance(task, str) and task.startswith("【要確認"):
            needs_rows.append(row)
        else:
            almost_rows.append(row)

    groups = {}
    group_order = []
    for row in almost_rows:
        category = category_by_id.get(row.get("link"), "")
        if category not in groups:
            groups[category] = []
            group_order.append(category)
        groups[category].append(row)
    group_order.sort(
        key=lambda category: category_first_index.get(
            category, len(category_first_index)
        )
    )
    ordered = []
    for category in group_order:
        ordered.extend(groups[category])
    return ordered + needs_rows


def _do_assemble_present(args, config):
    today = _resolve_today(args.today)
    workdir = _expand_path(args.workdir)
    candidates = _read_json(workdir / "candidates.json", context="candidates ファイル")
    contracts.validate_candidates(candidates)
    judged = _read_json(_expand_path(args.judged), context="judged ファイル")
    contracts.validate_judged(judged)

    cycle_state_dir = _expand_path(_config_get(config, "cycle_state_dir"))
    presented_data = _read_presented(cycle_state_dir)
    presented_keys = _presented_keys(presented_data)
    presented_candidate_ids = [
        candidate["id"]
        for candidate in candidates["items"]
        if stable_key(candidate) in presented_keys
    ]

    payload = dict(judged)
    payload["candidates"] = candidates
    payload["presented_candidate_ids"] = presented_candidate_ids
    payload["presented_on"] = today
    result = present.process(payload, config)
    result = dict(result)
    result["rows"] = _order_present_rows(result["rows"], candidates)

    workdir.mkdir(parents=True, exist_ok=True)
    _write_json(workdir / "ledger_rows.json", result)
    _write_json(workdir / "judged.json", judged)

    rows = result["rows"]
    almost_certain, needs_check = _count_stages(rows)
    return {
        "rows": len(rows),
        "almost_certain": almost_certain,
        "needs_check": needs_check,
        "excluded_count": result.get("excluded_count", 0),
        "already_presented_count": result.get("already_presented_count", 0),
    }


def _do_complete(args, config):
    # ``--today`` は受け取るが、run_date／metrics は prepare_summary が正。
    workdir = _expand_path(args.workdir)
    ledger_rows = _read_json(
        workdir / "ledger_rows.json", context="ledger_rows ファイル"
    )
    candidates = _read_json(workdir / "candidates.json", context="candidates ファイル")
    judged = _read_json(workdir / "judged.json", context="judged ファイル")
    snapshot = _read_json(workdir / "snapshot.json", context="snapshot ファイル")
    prepare_summary_path = workdir / "prepare_summary.json"
    if not prepare_summary_path.is_file():
        raise ValueError("complete には prepare_summary.json が必要です")
    prepare_summary = _read_json(
        prepare_summary_path, context="prepare_summary ファイル"
    )
    if not isinstance(prepare_summary, dict):
        raise ValueError("prepare_summary は JSON オブジェクトでなければなりません")
    if "run_date" not in prepare_summary:
        raise ValueError("prepare_summary に run_date がありません")
    if "metrics" not in prepare_summary:
        raise ValueError("prepare_summary に metrics がありません")
    run_date = prepare_summary["run_date"]
    _parse_day(run_date, context="run_date")
    prepare_metrics = prepare_summary["metrics"]
    if not isinstance(prepare_metrics, dict):
        raise ValueError("prepare_summary の metrics は dict でなければなりません")

    if not isinstance(ledger_rows, dict) or not isinstance(
        ledger_rows.get("rows"), list
    ):
        raise ValueError("ledger_rows に rows がありません")
    rows = ledger_rows["rows"]
    if not isinstance(candidates, dict) or not isinstance(
        candidates.get("items"), list
    ):
        raise ValueError("candidates に items がありません")
    if not isinstance(judged, dict) or not isinstance(judged.get("items"), list):
        raise ValueError("judged に items がありません")
    if not isinstance(snapshot, dict) or "totals" not in snapshot:
        raise ValueError("snapshot に totals がありません")

    # append 件数の検証（部分追加のまま完了にしない）。
    if len(rows) >= 1:
        append_path = workdir / "append_result.json"
        if not append_path.is_file():
            raise ValueError("行がある場合は append_result.json が必要です")
        append_data = _read_json(append_path, context="append_result ファイル")
        if not isinstance(append_data, dict) or not _is_int(
            append_data.get("appended_rows")
        ):
            raise ValueError("append_result の appended_rows が不正です")
        appended_rows = append_data["appended_rows"]
        if appended_rows != len(rows):
            raise ValueError(
                f"appended_rows ({appended_rows}) が行数 ({len(rows)}) と一致しません"
            )
    else:
        appended_rows = 0

    candidates_by_id = {}
    for candidate in candidates["items"]:
        if isinstance(candidate, dict) and candidate.get("id") is not None:
            candidates_by_id[candidate["id"]] = candidate

    # judged の candidate_id が candidates に無い場合は fail-closed。
    for item in judged["items"]:
        if not isinstance(item, dict):
            raise ValueError(f"judged の要素は dict でなければなりません: {item!r}")
        if item.get("candidate_id") not in candidates_by_id:
            raise ValueError(
                f"judged の candidate_id が candidates にありません: "
                f"{item.get('candidate_id')!r}"
            )

    cycle_state_dir = _expand_path(_config_get(config, "cycle_state_dir"))
    presented_data = _read_presented(cycle_state_dir)
    presented_keys = _presented_keys(presented_data)

    row_candidate_ids = set()
    for row in rows:
        if isinstance(row, dict) and row.get("link") is not None:
            row_candidate_ids.add(row["link"])

    new_presented = []
    for item in judged["items"]:
        candidate_id = item["candidate_id"]
        if candidate_id not in row_candidate_ids:
            continue
        if item.get("verdict") == "keep":
            continue
        candidate = candidates_by_id[candidate_id]
        key = stable_key(candidate)
        if key in presented_keys:
            continue
        new_presented.append(
            {"key": key, "candidate_id": candidate_id, "run_date": run_date}
        )
        presented_keys.add(key)

    # trend の before：run_date より前の snapshot のうち最新の totals。
    snapshots_dir = cycle_state_dir / "snapshots"
    before_totals = None
    before_day = None
    if snapshots_dir.is_dir():
        for snapshot_path in snapshots_dir.glob("*.json"):
            try:
                file_day = datetime.date.fromisoformat(snapshot_path.stem)
            except ValueError:
                continue
            if file_day >= _parse_day(run_date, context="run_date"):
                continue
            if before_day is not None and file_day <= before_day:
                continue
            data = _read_json(snapshot_path, context="過去 snapshot ファイル")
            if isinstance(data, dict) and "totals" in data:
                before_day = file_day
                before_totals = data["totals"]

    reachability = None
    preservation_path = cycle_state_dir / "last_preservation.json"
    if preservation_path.is_file():
        preservation_data = _read_json(
            preservation_path, context="last_preservation ファイル"
        )
        if isinstance(preservation_data, dict):
            reachability = preservation_data.get("reachability")

    execute_path = workdir / "execute_result.json"
    if execute_path.is_file():
        execute_data = _read_json(execute_path, context="execute_result ファイル")
        if not isinstance(execute_data, dict):
            raise ValueError("execute_result は JSON オブジェクトでなければなりません")
        execution = dict(execute_data)
        # execute_result の有無が performed の根拠であり、中身の同名キーで
        # 上書きされないよう、取り込んだ後に固定する（QA 推奨指摘への対応）。
        execution["performed"] = True
    else:
        execution = {
            "performed": False,
            "note": "削減の実行なし（本人の仕分け済み行なし）",
        }

    almost_certain, needs_check = _count_stages(rows)
    candidates_total = len(candidates["items"])
    metrics = {
        "reachability": reachability,
        "duplicate_rate": prepare_metrics.get("duplicate_rate"),
        "duplicate_involved_items": prepare_metrics.get("duplicate_involved_items"),
        "dead_rate": prepare_metrics.get("dead_rate"),
        "dead_items": prepare_metrics.get("dead_items"),
        "scanned_items": prepare_metrics.get("scanned_items"),
    }
    report = {
        "kind": "cycle_report",
        "version": 1,
        "run_date": run_date,
        "candidates": {
            "total": candidates_total,
            "almost_certain": almost_certain,
            "needs_check": needs_check,
            "excluded": ledger_rows.get("excluded_count", 0),
            "already_presented": ledger_rows.get("already_presented_count", 0),
            "appended_rows": appended_rows,
        },
        "metrics": metrics,
        "totals_trend": {"before": before_totals, "after": snapshot["totals"]},
        "execution": execution,
        "sources": snapshot.get("sources", []),
    }

    # ここから先が状態の更新（検証はすべて済んでいる）。
    cycle_state_dir.mkdir(parents=True, exist_ok=True)
    if new_presented:
        updated = {
            "version": 1,
            "presented": list(presented_data.get("presented", [])) + new_presented,
        }
        _write_json(cycle_state_dir / "presented.json", updated)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    _write_json(snapshots_dir / f"{run_date}.json", snapshot)
    _write_json(workdir / "cycle_report.json", report)

    history_entry = {
        "run_date": run_date,
        "candidates_total": candidates_total,
        "appended_rows": appended_rows,
        "totals": snapshot["totals"],
    }
    history_path = cycle_state_dir / "history.jsonl"
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(history_entry, ensure_ascii=False) + "\n")
    return report


def _build_parser():
    parser = argparse.ArgumentParser(prog="entropy_reduction.cycle")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--counts", required=True)
    prepare_parser.add_argument("--workdir", default=None)
    prepare_parser.add_argument("--today", default=None)

    assemble_parser = subparsers.add_parser("assemble-present")
    assemble_parser.add_argument("--workdir", required=True)
    assemble_parser.add_argument("--judged", required=True)
    assemble_parser.add_argument("--today", default=None)

    complete_parser = subparsers.add_parser("complete")
    complete_parser.add_argument("--workdir", required=True)
    complete_parser.add_argument("--today", default=None)
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        config = resolve_config()
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        _require_cycle_config(config)
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    handlers = {
        "prepare": _do_prepare,
        "assemble-present": _do_assemble_present,
        "complete": _do_complete,
    }
    handler = handlers[args.subcommand]
    try:
        result = handler(args, config)
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except ValueError as exc:
        print(f"交換形式の違反: {exc}", file=sys.stderr)
        return EXIT_FORMAT_ERROR
    except OSError as exc:
        print(f"入出力エラー: {exc}", file=sys.stderr)
        return EXIT_FORMAT_ERROR
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
