"""第2層：証拠つき判定（SPEC §2 / §3 judged、P2 で実装予定）.

本フェーズ（P1 計測まわり）では未実装のスタブ。
"""

from .cli import run_cli


def process(payload, config):
    raise NotImplementedError("judge はフェーズ未実装です（P2 で実装予定）")


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
