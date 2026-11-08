"""再計測と Before/After 報告（SPEC §2 / §3 remeasure_report / D8）.

入力（remeasure_input）：``before`` / ``after``（各 snapshot）＋
``preservation_result``。いずれか欠ければ ValueError。

before / after は contracts の snapshot 検証、preservation_result は
contracts の preservation_result 検証を通し、違反はそのまま
ValueError とする。削減量は数え直さず、検証済み snapshot の totals を
そのまま使う（P1 の measure と同じ物差しの再利用）。

D8: 報告は「保存率（合否）／到達可能性／削減量（記録のみ）／
総量の推移」の4点を含む。削減量は before − after とし、
``totals_trend`` には before / after の totals 全体のコピーを載せる。
"""

import copy

from . import contracts
from .cli import run_cli


def process(payload, config):
    """remeasure_input から remeasure_report を組み立てる。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")

    before = payload.get("before")
    if before is None:
        raise ValueError("入力に before がありません")
    after = payload.get("after")
    if after is None:
        raise ValueError("入力に after がありません")
    preservation_result = payload.get("preservation_result")
    if preservation_result is None:
        raise ValueError("入力に preservation_result がありません")

    contracts.validate_snapshot(before)
    contracts.validate_snapshot(after)
    contracts.validate_preservation_result(preservation_result)

    before_totals = before["totals"]
    after_totals = after["totals"]

    result = {
        "kind": "remeasure_report",
        "version": 1,
        "preservation_pass": preservation_result["pass"],
        "reachability": preservation_result["reachability"],
        "reduced": {
            "code_lines": before_totals["code_lines"] - after_totals["code_lines"],
            "md_lines": before_totals["md_lines"] - after_totals["md_lines"],
        },
        "totals_trend": {
            "before": copy.deepcopy(before_totals),
            "after": copy.deepcopy(after_totals),
        },
    }
    contracts.validate_remeasure_report(result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
