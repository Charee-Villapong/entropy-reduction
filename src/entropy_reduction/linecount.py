"""行数計測の共通部品（SPEC §2）.

入力は ``{"text": ..., "target": 名前}`` または ``{"path": パス}``。
出力は ``{"lines": int, "category": "md" | "code" | "other"}``。
行数は論理行で数える（``str.splitlines`` の意味論：末尾改行の有無や
CRLF に左右されない）。種別は target（または path）の拡張子で判定する。
"""

from pathlib import Path

from .cli import run_cli

# 拡張子と種別の対応表（ケース依存の定義値はここに一箇所だけ集約する）。
MD_EXTENSIONS = frozenset({".md", ".markdown", ".mdx"})
CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cs",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".kts",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".pl",
        ".pm",
        ".r",
        ".lua",
        ".sql",
        ".html",
        ".css",
        ".scss",
    }
)


def categorize(name):
    """ファイル名（またはパス）の拡張子から種別を返す。"""
    if not name:
        return "other"
    extension = Path(str(name)).suffix.lower()
    if extension in MD_EXTENSIONS:
        return "md"
    if extension in CODE_EXTENSIONS:
        return "code"
    return "other"


def count_lines(text):
    """論理行数を返す。"""
    return len(text.splitlines())


def process(payload, config):
    """テキストまたはファイルの行数と種別を返す。

    ``config`` は現状では使わない（共通部品としての署名を揃える）。
    text も path も無ければ ValueError。存在しない path は
    FileNotFoundError（OSError）をそのまま送出する。
    """
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")

    if payload.get("text") is not None:
        text = payload["text"]
        if not isinstance(text, str):
            raise ValueError(f"text は文字列でなければなりません: {text!r}")
        name = payload.get("target") or payload.get("path")
        return {"lines": count_lines(text), "category": categorize(name)}

    if payload.get("path") is not None:
        path = payload["path"]
        text = Path(path).read_text(encoding="utf-8")
        return {"lines": count_lines(text), "category": categorize(path)}

    raise ValueError("入力に text も path もありません")


if __name__ == "__main__":
    # linecount は設定が無ければ空設定で続行する。
    raise SystemExit(run_cli(process, allow_missing_config=True))
