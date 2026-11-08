"""各ユニット共通の CLI 接続（SPEC §2）.

使い方：``python -m entropy_reduction.<unit> <input.json> <output.json>``
終了コード：0=正常、1=交換形式の違反（入力・検証の ValueError）、
2=設定の誤り。

設定ファイルの探し方：環境変数 ``ENTROPY_REDUCTION_CONFIG`` が指す
パス → ``./config.local.json`` の順。linecount のように設定なしでも
動けるユニットは ``allow_missing_config=True`` で空設定として続行する。
"""

import json
import os
import sys
from pathlib import Path

from .config import load_config

CONFIG_ENV_VAR = "ENTROPY_REDUCTION_CONFIG"
DEFAULT_CONFIG_FILENAME = "config.local.json"

EXIT_OK = 0
EXIT_FORMAT_ERROR = 1
EXIT_CONFIG_ERROR = 2


class ConfigLoadError(Exception):
    """設定の読み込みに失敗した（終了コード 2 に対応）。"""


def resolve_config(allow_missing_config=False):
    """設定ファイルを探して読み、dict を返す。"""
    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_file():
            raise ConfigLoadError(
                f"環境変数 {CONFIG_ENV_VAR} が指す設定ファイルがありません: {path}"
            )
    else:
        path = Path(DEFAULT_CONFIG_FILENAME)
        if not path.is_file():
            if allow_missing_config:
                return {}
            raise ConfigLoadError(
                f"設定ファイルがありません: {path} "
                f"（環境変数 {CONFIG_ENV_VAR} でも指定できます）"
            )
    try:
        return load_config(str(path))
    except (ValueError, OSError) as exc:
        raise ConfigLoadError(f"設定ファイルが不正です: {path}: {exc}") from exc


def run_cli(process, argv=None, allow_missing_config=False):
    """``process(payload, config)`` をファイル入出力に接続し、終了コードを返す。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            "usage: python -m entropy_reduction.<unit> <input.json> <output.json>",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    input_path, output_path = args

    try:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"入力ファイルを読み込めません: {exc}", file=sys.stderr)
        return EXIT_FORMAT_ERROR

    try:
        config = resolve_config(allow_missing_config=allow_missing_config)
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        result = process(payload, config)
    except ValueError as exc:
        print(f"交換形式の違反: {exc}", file=sys.stderr)
        return EXIT_FORMAT_ERROR

    try:
        Path(output_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"出力ファイルを書き込めません: {exc}", file=sys.stderr)
        return EXIT_FORMAT_ERROR
    return EXIT_OK
