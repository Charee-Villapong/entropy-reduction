"""第3層：仕分けの読み取り（仕分け列 → execute の入力）.

本人が専用タブ「エントロピー候補」の「仕分け」列（プルダウン）で
選んだ値を読み、execute が受け付ける形（7要素＋``candidate_id``／
``target``／``action``／``user_decision``、統合・符号化では
``migration_target``）へ変換する読み取りユニット。

入力 payload は次の3要素を持つ：

- ``rows``：台帳から読み戻した行の列（各行は台帳の7要素に加え、
  仕分け列の値 ``decision_label`` を持つ。候補と無関係な一般行が
  混ざっていてよい）
- ``candidates``：candidates 形式（``target`` の解決用）
- ``judged``：judged 形式（``migration_target`` の解決用）

対象行は ``link`` が candidates の ``id`` と一致する行だけとし、
無関係な行は無視する（件数にも入れない）。仕分けは
``decision_label`` の完全一致でのみ判定する（前後の空白も
想定外として扱う）。空・欠落は「仕分けなし」、対応表にない値と
文字列でない値は「想定外」として、いずれも実行段へ進ませない
（fail-closed）。

対応表は設定キー ``decision_labels`` で丸ごと差し替え可能で、
設定に無い場合はモジュール内の既定値を使う。既定値は合格ラインの
ような判定値ではなく形式定義であるため、D6 のフォールバック禁止
（しきい値・合格ラインの直書き禁止）には当たらない（SPEC 付記）。

仕分けが取れなかった候補行は、理由コード（``no_decision``／
``unknown_decision``／``missing_migration_target``）つきで出力の
``skipped`` に記録する。記録には ``candidate_id`` と理由コードのみを
載せ、行の本文など実値は入れない（D4）。同一候補に仕分け済み行が
複数ある場合は黙って間引かず両方を通し、重複の全体拒否は
execute の D2 ガードに委ねる。仕分け済み0件でも正常出力とする
（実行段が空で拒否するのが fail-closed として正しい）。

【仕様変更】2026-11-09 の本人の指示で、仕分け方式は理由列の先頭
マーカー（``[削除]`` 等の手書き）からプルダウン選択へ変更された
（2026-11-08 のマーカー決定時の留保「使いにくければあとでまた
変えよ」に基づく）。理由列のマーカーはもう読まない。
"""

from . import contracts
from .cli import run_cli

DEFAULT_DECISION_LABELS = {
    "削除": "delete",
    "統合": "merge",
    "符号化": "codify",
    "保持": "keep",
}

REASON_NO_DECISION = "no_decision"
REASON_UNKNOWN_DECISION = "unknown_decision"
REASON_MISSING_MIGRATION_TARGET = "missing_migration_target"


def _config_get(config, key, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def validate_decision_labels(labels):
    """``decision_labels`` の値を検証し、コピーを返す。

    存在する場合は空でない dict で、キーは空でない文字列、値は
    ``contracts.JUDGED_VERDICTS`` のいずれかでなければならない。
    違反は ValueError とする。
    """
    if not isinstance(labels, dict) or not labels:
        raise ValueError(
            f"decision_labels は空でない dict でなければなりません: {labels!r}"
        )
    for label, decision in labels.items():
        if not isinstance(label, str) or label == "":
            raise ValueError(
                "decision_labels のキーは空でない文字列でなければなりません: "
                f"{label!r}"
            )
        if not isinstance(decision, str) or decision not in contracts.JUDGED_VERDICTS:
            raise ValueError(
                f"decision_labels の値が不正です: {label!r} -> {decision!r}"
            )
    return dict(labels)


def _resolve_labels(config):
    labels = _config_get(config, "decision_labels")
    if labels is None:
        return dict(DEFAULT_DECISION_LABELS)
    return validate_decision_labels(labels)


def _extract_rows(payload):
    rows_value = payload.get("rows")
    if rows_value is None:
        raise ValueError("入力に rows がありません")
    if isinstance(rows_value, dict):
        contracts.validate_ledger_rows(rows_value)
        return rows_value["rows"]
    if isinstance(rows_value, list):
        contracts.validate_ledger_rows(
            {"kind": "ledger_rows", "version": 1, "rows": rows_value}
        )
        return rows_value
    raise ValueError(
        f"rows はリストまたは ledger_rows 形式でなければなりません: {rows_value!r}"
    )


def process(payload, config):
    """台帳行の仕分け列を読み、execute 用の ledger_rows を返す。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")
    rows = _extract_rows(payload)

    candidates = payload.get("candidates")
    if candidates is None:
        raise ValueError("入力に candidates がありません")
    contracts.validate_candidates(candidates)
    candidates_by_id = {item["id"]: item for item in candidates["items"]}

    judged = payload.get("judged")
    if judged is None:
        raise ValueError("入力に judged がありません")
    contracts.validate_judged(judged)
    judged_by_id = {}
    for item in judged["items"]:
        judged_by_id.setdefault(item["candidate_id"], item)

    labels = _resolve_labels(config)

    output_rows = []
    skipped = []
    for row in rows:
        link = row.get("link")
        if link not in candidates_by_id:
            continue
        candidate_id = link
        candidate = candidates_by_id[link]

        label = row.get("decision_label")
        if label is None or (isinstance(label, str) and label == ""):
            skipped.append(
                {"candidate_id": candidate_id, "reason_code": REASON_NO_DECISION}
            )
            continue
        if not isinstance(label, str) or label not in labels:
            skipped.append(
                {
                    "candidate_id": candidate_id,
                    "reason_code": REASON_UNKNOWN_DECISION,
                }
            )
            continue
        decision = labels[label]

        migration_target = None
        if decision in contracts.VERDICTS_REQUIRING_MIGRATION_TARGET:
            judged_item = judged_by_id.get(candidate_id)
            if judged_item is not None:
                migration_target = judged_item.get("migration_target")
            if not isinstance(migration_target, str) or migration_target == "":
                skipped.append(
                    {
                        "candidate_id": candidate_id,
                        "reason_code": REASON_MISSING_MIGRATION_TARGET,
                    }
                )
                continue

        output_row = {key: row.get(key) for key in contracts.LEDGER_ROW_KEYS}
        output_row["candidate_id"] = candidate_id
        output_row["target"] = candidate.get("target")
        output_row["action"] = decision
        output_row["user_decision"] = decision
        if decision in contracts.VERDICTS_REQUIRING_MIGRATION_TARGET:
            output_row["migration_target"] = migration_target
        output_rows.append(output_row)

    result = {
        "kind": "ledger_rows",
        "version": 1,
        "rows": output_rows,
        "skipped": skipped,
        "skipped_count": len(skipped),
    }
    contracts.validate_ledger_rows(result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
