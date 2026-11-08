"""交換形式の検証（SPEC §3 / D4）.

すべての交換形式は ``kind`` と ``version: 1`` を持ち、検証違反は
ValueError とする。正常時は None を返す。

D4: どの形式にも実値（ファイル内容・接続情報・個人情報など）を
含めてはならない。FORBIDDEN_VALUE_FIELDS のキーがペイロードの
どこ（トップレベル・ネスト内部）に現れても違反とする。
"""

import datetime
import re

# D4: 実値を持ちうるキー名（tests/conftest.py の定義と一致させる）。
FORBIDDEN_VALUE_FIELDS = frozenset(
    {
        "content",
        "file_content",
        "raw_text",
        "body",
        "password",
        "token",
        "api_key",
        "secret",
        "private_key",
        "connection_string",
        "connection_info",
        "personal_info",
        "credential",
        "credentials",
    }
)

SNAPSHOT_TOTAL_KEYS = (
    "repos",
    "code_lines",
    "md_lines",
    "crons",
    "hooks",
    "custom_skills",
    "ledger_open_rows",
    "open_issues",
)

CANDIDATE_SOURCES = frozenset({"inventory", "weekly", "embedding"})
CANDIDATE_CATEGORIES = frozenset({"duplicate", "dead", "bloat", "other"})
CANDIDATE_ITEM_KEYS = ("id", "source", "target", "category", "title", "detail")

JUDGED_STATUSES = frozenset({"judged", "needs_check"})
JUDGED_VERDICTS = frozenset({"delete", "merge", "codify", "keep"})
VERDICTS_REQUIRING_MIGRATION_TARGET = frozenset({"merge", "codify"})
VERDICTS_FORBIDDING_MIGRATION_TARGET = frozenset({"delete", "keep"})
JUDGED_ITEM_KEYS = (
    "candidate_id",
    "status",
    "evidence",
    "verdict",
    "migration_target",
    "reason",
)
EVIDENCE_KEYS = ("last_used", "references", "impact")

LEDGER_ROW_KEYS = (
    "task",
    "status",
    "reason",
    "check_method",
    "due_date",
    "link",
    "updated_at",
)

EXECUTION_ITEM_KEYS = ("candidate_id", "action", "result")
EXECUTION_COUNT_KEYS = ("total", "executed")

REMEASURE_REDUCED_KEYS = ("code_lines", "md_lines")
REMEASURE_TREND_KEYS = ("before", "after")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fail(message):
    raise ValueError(message)


def _check_no_forbidden_fields(value, path="$"):
    """D4: dict のキーを再帰的に走査し、禁止キーがあれば ValueError。"""
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in FORBIDDEN_VALUE_FIELDS:
                _fail(f"D4 違反: 実値フィールド {key!r} が {path} に含まれています")
            _check_no_forbidden_fields(sub, f"{path}.{key}")
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            _check_no_forbidden_fields(sub, f"{path}[{index}]")


def _check_common(payload, expected_kind):
    if not isinstance(payload, dict):
        _fail("ペイロードは dict でなければなりません")
    _check_no_forbidden_fields(payload)
    if payload.get("kind") != expected_kind:
        _fail(
            f"kind は {expected_kind!r} でなければなりません: {payload.get('kind')!r}"
        )
    version = payload.get("version")
    if not _is_int(version) or version != 1:
        _fail(f"version は整数の 1 でなければなりません: {version!r}")


def _require_keys(mapping, keys, context):
    if not isinstance(mapping, dict):
        _fail(f"{context} は dict でなければなりません")
    for key in keys:
        if key not in mapping:
            _fail(f"{context} に必須キー {key!r} がありません")


def _check_date_string(value, context):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        _fail(f"{context} は YYYY-MM-DD 形式の文字列でなければなりません: {value!r}")
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        _fail(f"{context} は実在する日付でなければなりません: {value!r}")


def _check_rate(value, context):
    if not _is_number(value):
        _fail(f"{context} は数値でなければなりません: {value!r}")
    if not 0 <= value <= 1:
        _fail(f"{context} は 0〜1 の範囲でなければなりません: {value!r}")


def validate_snapshot(payload):
    """snapshot 形式（SPEC §3）を検証する。"""
    _check_common(payload, "snapshot")
    if "taken_at" not in payload:
        _fail("snapshot に必須キー 'taken_at' がありません")
    _check_date_string(payload["taken_at"], "taken_at")
    _require_keys(payload.get("totals"), SNAPSHOT_TOTAL_KEYS, "totals")
    for key in SNAPSHOT_TOTAL_KEYS:
        value = payload["totals"][key]
        if not _is_int(value):
            _fail(f"totals.{key} は整数でなければなりません: {value!r}")
        if value < 0:
            _fail(f"totals.{key} は 0 以上でなければなりません: {value!r}")
    if "sources" not in payload:
        _fail("snapshot に必須キー 'sources' がありません")
    if not isinstance(payload["sources"], list):
        _fail("sources はリストでなければなりません")
    return None


def validate_candidates(payload):
    """candidates 形式（SPEC §3）を検証する。第1層は判定欄を持たない。"""
    _check_common(payload, "candidates")
    items = payload.get("items")
    if not isinstance(items, list):
        _fail("items はリストでなければなりません")
    for index, item in enumerate(items):
        context = f"items[{index}]"
        _require_keys(item, CANDIDATE_ITEM_KEYS, context)
        if "verdict" in item:
            _fail(
                f"{context} に判定欄 verdict を含めてはなりません（第1層は判断しない）"
            )
        if item["source"] not in CANDIDATE_SOURCES:
            _fail(f"{context}.source が不正です: {item['source']!r}")
        if item["category"] not in CANDIDATE_CATEGORIES:
            _fail(f"{context}.category が不正です: {item['category']!r}")
    return None


def validate_judged(payload):
    """judged 形式（SPEC §3 / D3）を検証する。"""
    _check_common(payload, "judged")
    items = payload.get("items")
    if not isinstance(items, list):
        _fail("items はリストでなければなりません")
    for index, item in enumerate(items):
        context = f"items[{index}]"
        _require_keys(item, JUDGED_ITEM_KEYS, context)
        status = item["status"]
        if status not in JUDGED_STATUSES:
            _fail(f"{context}.status が不正です: {status!r}")
        _require_keys(item["evidence"], EVIDENCE_KEYS, f"{context}.evidence")
        if status == "judged":
            evidence = item["evidence"]
            for key in EVIDENCE_KEYS:
                if evidence[key] is None:
                    _fail(
                        f"{context}.evidence.{key} が null の候補は judged になれません"
                    )
            verdict = item["verdict"]
            if verdict not in JUDGED_VERDICTS:
                _fail(f"{context}.verdict が不正です: {verdict!r}")
            migration_target = item["migration_target"]
            if verdict in VERDICTS_FORBIDDING_MIGRATION_TARGET:
                if migration_target is not None:
                    _fail(
                        f"{context}: verdict={verdict} のとき migration_target は "
                        "null でなければなりません"
                    )
            elif verdict in VERDICTS_REQUIRING_MIGRATION_TARGET:
                if migration_target is None:
                    _fail(
                        f"{context}: verdict={verdict} のとき migration_target が "
                        "必要です"
                    )
        else:  # needs_check
            if item["verdict"] is not None:
                _fail(f"{context}: needs_check の verdict は null でなければなりません")
    return None


def validate_ledger_rows(payload):
    """ledger_rows 形式（SPEC §3）を検証する。"""
    _check_common(payload, "ledger_rows")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        _fail("rows はリストでなければなりません")
    for index, row in enumerate(rows):
        _require_keys(row, LEDGER_ROW_KEYS, f"rows[{index}]")
    return None


def validate_execution_record(payload):
    """execution_record 形式（SPEC §3）を検証する。"""
    _check_common(payload, "execution_record")
    if "executed_at" not in payload:
        _fail("execution_record に必須キー 'executed_at' がありません")
    items = payload.get("items")
    if not isinstance(items, list):
        _fail("items はリストでなければなりません")
    for index, item in enumerate(items):
        _require_keys(item, EXECUTION_ITEM_KEYS, f"items[{index}]")
    _require_keys(payload.get("counts"), EXECUTION_COUNT_KEYS, "counts")
    return None


def validate_preservation_result(payload):
    """preservation_result 形式（SPEC §3）を検証する。"""
    _check_common(payload, "preservation_result")
    for key in ("preservation_rate", "reachability", "pass"):
        if key not in payload:
            _fail(f"preservation_result に必須キー {key!r} がありません")
    _check_rate(payload["preservation_rate"], "preservation_rate")
    _check_rate(payload["reachability"], "reachability")
    if payload["pass"] is True and payload["reachability"] != 1.0:
        _fail("pass が true のとき reachability は 1.0 でなければなりません")
    return None


def validate_remeasure_report(payload):
    """remeasure_report 形式（SPEC §3 / D8）を検証する。"""
    _check_common(payload, "remeasure_report")
    for key in ("preservation_pass", "reachability", "reduced", "totals_trend"):
        if key not in payload:
            _fail(f"remeasure_report に必須要素 {key!r} がありません（D8）")
    _require_keys(payload["reduced"], REMEASURE_REDUCED_KEYS, "reduced")
    _require_keys(payload["totals_trend"], REMEASURE_TREND_KEYS, "totals_trend")
    return None


_VALIDATORS = {
    "snapshot": validate_snapshot,
    "candidates": validate_candidates,
    "judged": validate_judged,
    "ledger_rows": validate_ledger_rows,
    "execution_record": validate_execution_record,
    "preservation_result": validate_preservation_result,
    "remeasure_report": validate_remeasure_report,
}


def validate(payload):
    """kind で振り分けて交換形式を検証する。違反は ValueError。"""
    if not isinstance(payload, dict):
        _fail("ペイロードは dict でなければなりません")
    kind = payload.get("kind")
    validator = _VALIDATORS.get(kind)
    if validator is None:
        _fail(f"未知の kind です: {kind!r}")
    return validator(payload)
