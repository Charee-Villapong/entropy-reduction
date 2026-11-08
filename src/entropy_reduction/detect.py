"""第1層：候補の機械抽出（SPEC §2 / §3 candidates）.

本フェーズ（P1 計測まわり）では未実装のスタブ。
"""

from .cli import run_cli


def process(payload, config):
    raise NotImplementedError("detect はフェーズ未実装です（本フェーズの実装範囲外）")


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
