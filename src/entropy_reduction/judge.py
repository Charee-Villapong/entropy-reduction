"""第2層：証拠つき判定（SPEC §2 / §3 judged / D3）.

入力（judge_input）：``candidates`` / ``evidence``（候補 ID をキーとし、
証拠3点 ``last_used`` / ``references`` / ``impact`` と判定案
``proposed_verdict`` / ``migration_target`` / ``reason`` を持つ）。

D3: 証拠3点がすべて揃い、判定案（proposed_verdict と空でない reason）
がある候補だけを ``judged`` とし、それ以外は ``needs_check`` に
分離する（verdict は null、不足点は reason に明記する）。
``references`` の空リストは証拠として有効（参照が無いこと自体が証拠）。
入力 candidates が判定欄（verdict）を持てば contracts が ValueError
とする（層の分離）。
"""

from . import contracts
from .cli import run_cli

_EVIDENCE_KEYS = contracts.EVIDENCE_KEYS


def _is_non_empty_string(value):
    return isinstance(value, str) and value != ""


def process(payload, config):
    """judge_input から judged を組み立てる。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")
    candidates = payload.get("candidates")
    if candidates is None:
        raise ValueError("入力に candidates がありません")
    contracts.validate_candidates(candidates)

    evidence_map = payload.get("evidence")
    if evidence_map is None:
        evidence_map = {}
    if not isinstance(evidence_map, dict):
        raise ValueError("evidence は dict でなければなりません")

    candidate_ids = [item["id"] for item in candidates["items"]]
    unknown = [key for key in evidence_map if key not in candidate_ids]
    if unknown:
        raise ValueError(f"候補に無い ID への証拠があります: {unknown!r}")

    items_out = []
    for candidate in candidates["items"]:
        candidate_id = candidate["id"]
        entry = evidence_map.get(candidate_id)
        if entry is None:
            entry = {}
        if not isinstance(entry, dict):
            raise ValueError(f"evidence[{candidate_id!r}] は dict でなければなりません")

        evidence = {key: entry.get(key) for key in _EVIDENCE_KEYS}
        proposed_verdict = entry.get("proposed_verdict")
        if (
            proposed_verdict is not None
            and proposed_verdict not in contracts.JUDGED_VERDICTS
        ):
            raise ValueError(f"proposed_verdict が不正です: {proposed_verdict!r}")
        reason = entry.get("reason")

        missing = []
        for key in _EVIDENCE_KEYS:
            if evidence[key] is None:
                missing.append(key)
        if proposed_verdict is None:
            missing.append("proposed_verdict")
        if not _is_non_empty_string(reason):
            missing.append("reason")

        if not missing:
            verdict = proposed_verdict
            migration_target = entry.get("migration_target")
            if (
                verdict in contracts.VERDICTS_REQUIRING_MIGRATION_TARGET
                and migration_target is None
            ):
                raise ValueError(
                    f"verdict={verdict} のとき migration_target が必要です"
                )
            if (
                verdict in contracts.VERDICTS_FORBIDDING_MIGRATION_TARGET
                and migration_target is not None
            ):
                raise ValueError(
                    f"verdict={verdict} のとき migration_target は "
                    "null でなければなりません"
                )
            items_out.append(
                {
                    "candidate_id": candidate_id,
                    "status": "judged",
                    "evidence": evidence,
                    "verdict": verdict,
                    "migration_target": migration_target,
                    "reason": reason,
                }
            )
        else:
            items_out.append(
                {
                    "candidate_id": candidate_id,
                    "status": "needs_check",
                    "evidence": evidence,
                    "verdict": None,
                    "migration_target": None,
                    "reason": (f"証拠が不足している（不足: {'・'.join(missing)}）"),
                }
            )

    result = {"kind": "judged", "version": 1, "items": items_out}
    contracts.validate_judged(result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
