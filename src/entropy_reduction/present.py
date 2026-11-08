"""第3層：仕分け提示（SPEC §2 / §3 ledger_rows / D9）.

入力は judged 形式。確度3段に分けて台帳の行を作る（D9：行を作る
唯一の経路）：

- 「ほぼ確実」：``judged`` で verdict が delete／merge／codify の候補。
  証拠3点の要約を併記し、種類（category）ごとに束ねてグループ識別を
  行に持たせる。
- 「要確認」：``needs_check`` の候補。行に「要確認（証拠不足）」を明記する。
- 「対象外」：``judged`` で verdict が keep の候補。2026-11-08 の本人
  決定により行にはせず、件数だけを ``excluded_count`` に報告する。

任意の補助入力（いずれも非実値のみ）：``candidates``（target／
category／title の解決用）、``presented_candidate_ids``（提示済み
候補。週次提示の増分管理に使い、行にせず件数を報告する）、
``presented_on``（提示日。行の更新日と次回確認日の起点）。

台帳への実書き込みは実行時に sheet-writer 経由で行い、本ユニットは
交換形式の生成までを担う。
"""

import datetime

from . import contracts
from .cli import run_cli

NEEDS_CHECK_MARKER = "要確認（証拠不足）"
_ACTIONABLE_VERDICTS = frozenset({"delete", "merge", "codify"})
_ROW_STATUS = "未着手"
_DUE_DAYS = 7


def _parse_date(value, context):
    if not isinstance(value, str):
        raise ValueError(f"{context} は YYYY-MM-DD 形式でなければなりません")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"{context} は YYYY-MM-DD 形式でなければなりません: {value!r}"
        ) from None


def _evidence_summary(evidence):
    references = evidence.get("references") or []
    refs_text = "、".join(str(ref) for ref in references) if references else "なし"
    return (
        f"最終使用 {evidence.get('last_used')}"
        f"／参照 {refs_text}"
        f"／影響 {evidence.get('impact')}"
    )


def _display_name(candidate, candidate_id):
    title = candidate.get("title") or candidate_id
    target = candidate.get("target")
    if target and target != title:
        return f"{title}（{target}）"
    return title


def process(payload, config):
    """judged から ledger_rows を組み立てる。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")
    contracts.validate_judged(payload)
    items = payload["items"]

    candidates_by_id = {}
    raw_candidates = payload.get("candidates")
    if raw_candidates is not None:
        contracts.validate_candidates(raw_candidates)
        for candidate in raw_candidates["items"]:
            candidates_by_id[candidate["id"]] = candidate

    presented_ids = payload.get("presented_candidate_ids")
    if presented_ids is None:
        presented_ids = []
    if not isinstance(presented_ids, list):
        raise ValueError("presented_candidate_ids はリストでなければなりません")
    already_presented = set(presented_ids)

    if payload.get("presented_on") is not None:
        presented_on = _parse_date(payload["presented_on"], "presented_on")
    else:
        presented_on = datetime.date.today()
    updated_at = presented_on.isoformat()
    due_date = (presented_on + datetime.timedelta(days=_DUE_DAYS)).isoformat()

    almost_certain = []  # (category, 出現順, row) — 種類ごとに束ねて並べる
    needs_check_rows = []
    excluded_count = 0
    already_presented_count = 0

    for item in items:
        candidate_id = item["candidate_id"]
        if candidate_id in already_presented:
            already_presented_count += 1
            continue
        if item["status"] == "judged" and item["verdict"] == "keep":
            excluded_count += 1
            continue

        candidate = candidates_by_id.get(candidate_id, {})
        name = _display_name(candidate, candidate_id)

        if item["status"] == "needs_check":
            reason = NEEDS_CHECK_MARKER
            if item.get("reason"):
                reason = f"{reason}：{item['reason']}"
            needs_check_rows.append(
                {
                    "task": f"【要確認】{name}",
                    "status": _ROW_STATUS,
                    "reason": reason,
                    "check_method": "不足している証拠を補い、再判定してから仕分ける",
                    "due_date": due_date,
                    "link": candidate_id,
                    "updated_at": updated_at,
                }
            )
            continue

        if item["verdict"] not in _ACTIONABLE_VERDICTS:
            raise ValueError(f"verdict が不正です: {item['verdict']!r}")
        category = candidate.get("category")
        if category:
            task = f"【ほぼ確実・束ね：{category}】{name}"
        else:
            task = f"【ほぼ確実】{name}"
        verdict_text = f"判定：{item['verdict']}"
        if item.get("migration_target"):
            verdict_text += f"（移行先：{item['migration_target']}）"
        reason = (
            f"{verdict_text}"
            f"／証拠：{_evidence_summary(item['evidence'])}"
            f"／理由：{item.get('reason')}"
        )
        row = {
            "task": task,
            "status": _ROW_STATUS,
            "reason": reason,
            "check_method": "証拠3点を確認して仕分ける",
            "due_date": due_date,
            "link": candidate_id,
            "updated_at": updated_at,
        }
        almost_certain.append((category or "", len(almost_certain), row))

    almost_certain.sort(key=lambda entry: (entry[0], entry[1]))
    rows = [entry[2] for entry in almost_certain] + needs_check_rows

    result = {
        "kind": "ledger_rows",
        "version": 1,
        "rows": rows,
        "excluded_count": excluded_count,
        "already_presented_count": already_presented_count,
    }
    contracts.validate_ledger_rows(result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
