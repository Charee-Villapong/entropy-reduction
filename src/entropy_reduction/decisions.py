"""第3層：仕分けの読み取り（台帳のマーカー → execute の入力）.

本人が台帳の候補行の「止まってる理由・前提条件」欄（ledger_rows
形式では ``reason`` キー）の**先頭**に記入した仕分けマーカーを読み、
execute が受け付ける形（7要素＋``candidate_id``／``target``／
``action``／``user_decision``、統合・符号化では ``migration_target``）
へ変換する読み取りユニット（2026-11-08 本人決定の記録形式）。

入力 payload は次の3要素を持つ：

- ``rows``：台帳から読み戻した行の列（各行は台帳の7要素を持つ。
  ledger_rows 形式の dict そのものでもよい。候補と無関係な一般行が
  混ざっていてよい）
- ``candidates``：candidates 形式（``target`` の解決用）
- ``judged``：judged 形式（``migration_target`` の解決用）

対象行は ``link`` が candidates の ``id`` と一致する行だけとし、
無関係な行は無視する（件数にも入れない）。マーカー解析では
``reason`` の前置空白（半角・全角を含む。Python の ``lstrip`` が
Unicode 空白を落とす）を許容し、先頭に既知マーカーがある場合だけ
仕分けとして扱う。中ほどにあるマーカーは無効とする。

対応表は設定キー ``decision_markers`` で丸ごと差し替え可能で、
設定に無い場合はモジュール内の既定値を使う。既定値は合格ラインの
ような判定値ではなく形式定義であるため、D6 のフォールバック禁止
（しきい値・合格ラインの直書き禁止）には当たらない（SPEC 付記）。

仕分けが取れなかった候補行は、理由コード（``no_marker``／
``unknown_marker``／``missing_migration_target``）つきで出力の
``skipped`` に記録する。記録には ``candidate_id`` と理由コードのみを
載せ、``reason`` の本文など実値は入れない（D4）。同一候補に
マーカーつき行が複数ある場合は黙って間引かず両方を通し、重複の
全体拒否は execute の D2 ガードに委ねる。仕分け済み0件でも正常
出力とする（実行段が空で拒否するのが fail-closed として正しい）。
"""

from . import contracts
from .cli import run_cli

DEFAULT_DECISION_MARKERS = {
    "[削除]": "delete",
    "[統合]": "merge",
    "[符号化]": "codify",
    "[保持]": "keep",
}

REASON_NO_MARKER = "no_marker"
REASON_UNKNOWN_MARKER = "unknown_marker"
REASON_MISSING_MIGRATION_TARGET = "missing_migration_target"


def _config_get(config, key, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def validate_decision_markers(markers):
    """``decision_markers`` の値を検証し、コピーを返す。

    存在する場合は空でない dict で、キーは空でない文字列、値は
    ``contracts.JUDGED_VERDICTS`` のいずれかでなければならない。
    違反は ValueError とする。
    """
    if not isinstance(markers, dict) or not markers:
        raise ValueError(
            f"decision_markers は空でない dict でなければなりません: {markers!r}"
        )
    for marker, decision in markers.items():
        if not isinstance(marker, str) or marker == "":
            raise ValueError(
                f"decision_markers のキーは空でない文字列でなければなりません: "
                f"{marker!r}"
            )
        if decision not in contracts.JUDGED_VERDICTS:
            raise ValueError(
                f"decision_markers の値が不正です: {marker!r} -> {decision!r}"
            )
    return dict(markers)


def _resolve_markers(config):
    markers = _config_get(config, "decision_markers")
    if markers is None:
        return dict(DEFAULT_DECISION_MARKERS)
    return validate_decision_markers(markers)


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
    """台帳行の仕分けマーカーを読み、execute 用の ledger_rows を返す。"""
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

    markers = _resolve_markers(config)
    # 接頭辞が重なるカスタム対応表でも長いマーカーを優先する。
    markers_by_length = sorted(markers, key=len, reverse=True)

    output_rows = []
    skipped = []
    for row in rows:
        link = row.get("link")
        if link not in candidates_by_id:
            continue
        candidate_id = link
        candidate = candidates_by_id[link]

        reason = row.get("reason")
        if not isinstance(reason, str):
            skipped.append(
                {"candidate_id": candidate_id, "reason_code": REASON_NO_MARKER}
            )
            continue
        stripped = reason.lstrip()

        decision = None
        for marker in markers_by_length:
            if stripped.startswith(marker):
                decision = markers[marker]
                break
        if decision is None:
            if stripped.startswith("["):
                code = REASON_UNKNOWN_MARKER
            else:
                code = REASON_NO_MARKER
            skipped.append({"candidate_id": candidate_id, "reason_code": code})
            continue

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
