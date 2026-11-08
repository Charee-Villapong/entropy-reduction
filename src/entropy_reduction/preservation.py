"""保存率テスト（SPEC §2 / §3 preservation_result / D6 / D7）.

入力（preservation_input）：``questions``（各 ``id`` / ``category`` /
``item_id``）/ ``answers``（各 ``question_id`` / ``correct``）/
``item_locations``（項目 ID → 所在リスト。``md`` / ``code``。
空リスト・欠落は到達不可）。

D7: 保存率は正答数 ÷ 回答総数。到達可能性は、問題の ``item_id`` の
和集合と ``item_locations`` のキーの和集合を母集団とし、所在リストが
空でない項目の割合とする（``code`` のみでも到達可能＝符号化を数える）。
確認問題セットに事故・例外系（``category`` が ``accident`` または
``exception``）の問題が1件も無ければエラーとする。

D6: 合格ラインは設定 ``preservation_pass_line`` からのみ読む。
この合格ラインは、本人が P5 初回一巡後に最終確定する暫定値であり、
コードは設定からのみ読む（コードへの直書きフォールバックは持たない）。
欠落・数値でない・0〜1 の範囲外は ValueError とする。
合格は、保存率が合格ライン以上、かつ到達可能性が 1.0 のときだけとする。
"""

from . import contracts
from .cli import run_cli

_ACCIDENT_EXCEPTION_CATEGORIES = frozenset({"accident", "exception"})


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _config_get(config, key, default=None):
    """config が dict でもオブジェクトでも値を読めるようにする。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _pass_line(config):
    value = _config_get(config, "preservation_pass_line")
    if not _is_number(value):
        raise ValueError(
            f"preservation_pass_line は数値でなければなりません: {value!r}"
        )
    if not 0 <= value <= 1:
        raise ValueError(
            f"preservation_pass_line は 0〜1 の範囲でなければなりません: {value!r}"
        )
    return float(value)


def process(payload, config):
    """preservation_input から preservation_result を組み立てる。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")

    questions = payload.get("questions")
    if questions is None:
        raise ValueError("入力に questions がありません")
    if not isinstance(questions, list):
        raise ValueError("questions はリストでなければなりません")
    if not questions:
        raise ValueError("questions は空であってはなりません")

    answers = payload.get("answers")
    if answers is None:
        raise ValueError("入力に answers がありません")
    if not isinstance(answers, list):
        raise ValueError("answers はリストでなければなりません")
    if not answers:
        raise ValueError("answers は空であってはなりません")

    item_locations = payload.get("item_locations")
    if item_locations is None:
        raise ValueError("入力に item_locations がありません")
    if not isinstance(item_locations, dict):
        raise ValueError("item_locations は dict でなければなりません")
    if not item_locations:
        raise ValueError("item_locations は空であってはなりません")

    categories = set()
    item_ids = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError(f"question は dict でなければなりません: {question!r}")
        categories.add(question.get("category"))
        item_id = question.get("item_id")
        if item_id is not None:
            item_ids.add(item_id)
    if not categories & _ACCIDENT_EXCEPTION_CATEGORIES:
        raise ValueError(
            "確認問題セットに事故・例外系（accident / exception）の問題が"
            "1件も含まれていません"
        )

    correct_count = 0
    for answer in answers:
        if not isinstance(answer, dict):
            raise ValueError(f"answer は dict でなければなりません: {answer!r}")
        if answer.get("correct"):
            correct_count += 1
    preservation_rate = correct_count / len(answers)

    population = item_ids | set(item_locations.keys())
    reachable_count = 0
    for item_id in population:
        locations = item_locations.get(item_id)
        if isinstance(locations, list) and locations:
            reachable_count += 1
    reachability = reachable_count / len(population) if population else 0.0

    pass_line = _pass_line(config)
    passed = preservation_rate >= pass_line and reachability == 1.0

    result = {
        "kind": "preservation_result",
        "version": 1,
        "preservation_rate": preservation_rate,
        "reachability": reachability,
        "pass": passed,
    }
    contracts.validate_preservation_result(result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
