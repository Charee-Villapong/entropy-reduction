"""第3層：削減の実行（SPEC §2 / §3 execution_record、P3 で実装予定）.

本フェーズ（P1 計測まわり）では未実装のスタブ。
"""

from .cli import run_cli


def process(payload, config):
    raise NotImplementedError("execute はフェーズ未実装です（P3 で実装予定）")


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
