"""設定の読み込みと検証（SPEC §4 / D6）.

設定値はコードに直書きせず、設定ファイル（JSON）から読む。
必須キーと値の型・範囲を検証し、違反は ValueError とする
（CLI では終了コード 2 = 設定の誤りに対応する）。
ファイルが存在しない場合は OSError（FileNotFoundError）をそのまま送出する。
"""

import json
from pathlib import Path

from . import contracts

REQUIRED_KEYS = (
    "bloat_md_lines",
    "unused_days",
    "preservation_pass_line",
    "harness_state_dir",
    "ledger_target",
)

_NON_NEGATIVE_INT_KEYS = ("bloat_md_lines", "unused_days")
_NON_EMPTY_STR_KEYS = ("harness_state_dir", "ledger_target")

# 任意キー（埋め込み経路。存在する場合のみ検証し、欠けていてもよい）。
_POSITIVE_INT_KEYS = (
    "embedding_top_k",
    "embedding_pca_components",
    "embedding_chunk_chars",
)
_RATE_KEYS = ("embedding_relative_margin", "embedding_min_similarity")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_config(path):
    """設定ファイル *path* を読み、検証済みの dict を返す。"""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # JSONDecodeError は ValueError の継承だが、文脈を付けて明示する。
        raise ValueError(f"設定ファイルの JSON が壊れています: {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("設定ファイルは JSON オブジェクトでなければなりません")

    for key in REQUIRED_KEYS:
        if key not in data:
            raise ValueError(f"必須の設定キーがありません: {key}")

    for key in _NON_NEGATIVE_INT_KEYS:
        value = data[key]
        if not _is_int(value):
            raise ValueError(f"{key} は整数でなければなりません: {value!r}")
        if value < 0:
            raise ValueError(f"{key} は 0 以上でなければなりません: {value!r}")

    pass_line = data["preservation_pass_line"]
    if not _is_number(pass_line):
        raise ValueError(
            f"preservation_pass_line は数値でなければなりません: {pass_line!r}"
        )
    if not 0 <= pass_line <= 1:
        raise ValueError(
            f"preservation_pass_line は 0〜1 の範囲でなければなりません: {pass_line!r}"
        )

    for key in _NON_EMPTY_STR_KEYS:
        value = data[key]
        if not isinstance(value, str) or value == "":
            raise ValueError(f"{key} は空でない文字列でなければなりません: {value!r}")

    if "embedding_model" in data:
        value = data["embedding_model"]
        if not isinstance(value, str) or value == "":
            raise ValueError(
                f"embedding_model は空でない文字列でなければなりません: {value!r}"
            )

    for key in _POSITIVE_INT_KEYS:
        if key not in data:
            continue
        value = data[key]
        if not _is_int(value):
            raise ValueError(f"{key} は整数でなければなりません: {value!r}")
        if value < 1:
            raise ValueError(f"{key} は 1 以上でなければなりません: {value!r}")

    for key in _RATE_KEYS:
        if key not in data:
            continue
        value = data[key]
        if not _is_number(value):
            raise ValueError(f"{key} は数値でなければなりません: {value!r}")
        if not 0 <= value <= 1:
            raise ValueError(f"{key} は 0〜1 の範囲でなければなりません: {value!r}")

    if "decision_markers" in data:
        markers = data["decision_markers"]
        if not isinstance(markers, dict) or not markers:
            raise ValueError(
                f"decision_markers は空でない dict でなければなりません: {markers!r}"
            )
        for marker, decision in markers.items():
            if not isinstance(marker, str) or marker == "":
                raise ValueError(
                    "decision_markers のキーは空でない文字列でなければなりません: "
                    f"{marker!r}"
                )
            if decision not in contracts.JUDGED_VERDICTS:
                raise ValueError(
                    f"decision_markers の値が不正です: {marker!r} -> {decision!r}"
                )

    return data
