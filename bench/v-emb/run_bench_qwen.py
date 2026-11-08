"""V-EMB 追加実測スクリプト（Issue #19）: Qwen/Qwen3-Embedding-0.6B。

run_bench.py と同一条件・同一方式で1モデルだけを測る。
- 固定ペアセット v1（pairs.json）、全60文を1回 encode（warm-up 1回 + 計時5回の中央値）
- コサイン類似度（normalize_embeddings=True の内積）
- 接頭辞なし。Qwen3-Embedding は指示文（instruction）で挙動が変わるモデルだが、
  本測定は他6モデルと同じ「素の条件」= 指示文なし・プロンプトなしで測る。
  ロード後に default_prompt_name / prompts を確認し、既定プロンプトが
  有効なら無効化してから測る（その場合は実行ログに明示される）。
- 結果は results.json を上書きせず results_qwen.json に別途書く。
"""

import json
import os
import statistics
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = json.load(open(os.path.join(HERE, "pairs.json"), encoding="utf-8"))

REPO = "Qwen/Qwen3-Embedding-0.6B"
FILESET = [
    "model.safetensors",
    "vocab.json",
    "merges.txt",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "1_Pooling/config.json",
    "config.json",
]


def dir_bytes(path):
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            fp = os.path.realpath(os.path.join(root, f))
            total += os.path.getsize(fp)
    return total


def main():
    from huggingface_hub import snapshot_download
    from sentence_transformers import SentenceTransformer

    cats = ["paraphrase", "negation", "unrelated"]
    flat = []  # (category, idx, side, text)
    for c in cats:
        for i, (a, b) in enumerate(PAIRS[c]):
            flat.append((c, i, 0, a))
            flat.append((c, i, 1, b))

    path = snapshot_download(
        repo_id=REPO, allow_patterns=FILESET, local_files_only=True
    )  # resolve cached snapshot
    size = dir_bytes(path)
    t0 = time.time()
    model = SentenceTransformer(path, device="cpu")  # load from local snapshot only
    load_s = time.time() - t0

    # 指示文なし条件の担保: 既定プロンプトが設定されていたら外して明示する
    dpn = getattr(model, "default_prompt_name", None)
    print("default_prompt_name:", dpn, flush=True)
    print("prompts:", getattr(model, "prompts", None), flush=True)
    if dpn is not None:
        model.default_prompt_name = None
        print("ADAPTATION: default_prompt_name を None に無効化（指示文なし条件）", flush=True)

    texts = [t for (_c, _i, _s, t) in flat]  # 接頭辞なし
    # warm-up + timed repeats (encode the full 60-sentence set)
    model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    reps = []
    for _ in range(5):
        t0 = time.time()
        emb = model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        reps.append(time.time() - t0)
    enc_s = statistics.median(reps)
    sims = {}
    for c in cats:
        vals = []
        for i in range(len(PAIRS[c])):
            a = emb[
                [
                    j
                    for j, x in enumerate(flat)
                    if x[0] == c and x[1] == i and x[2] == 0
                ][0]
            ]
            b = emb[
                [
                    j
                    for j, x in enumerate(flat)
                    if x[0] == c and x[1] == i and x[2] == 1
                ][0]
            ]
            vals.append(float(a @ b))
        sims[c] = vals
    pm, nm = statistics.mean(sims["paraphrase"]), statistics.mean(sims["negation"])
    wins = sum(1 for p in sims["paraphrase"] for n in sims["negation"] if p > n)
    rank_acc = wins / (len(sims["paraphrase"]) * len(sims["negation"]))
    worst_i = int(np.argmax(sims["negation"]))
    r = {
        "model": REPO,
        "dim": int(emb.shape[1]),
        "size_mb": round(size / 1e6, 1),
        "load_s": round(load_s, 2),
        "encode_60_median_s": round(enc_s, 3),
        "sentences_per_s": round(len(texts) / enc_s, 1),
        "paraphrase_mean": round(pm, 4),
        "paraphrase_std": round(statistics.pstdev(sims["paraphrase"]), 4),
        "negation_mean": round(nm, 4),
        "negation_std": round(statistics.pstdev(sims["negation"]), 4),
        "unrelated_mean": round(statistics.mean(sims["unrelated"]), 4),
        "margin": round(pm - nm, 4),
        "rank_accuracy": round(rank_acc, 4),
        "worst_negation": {
            "pair": PAIRS["negation"][worst_i],
            "sim": round(sims["negation"][worst_i], 4),
        },
        "paraphrase_min": round(min(sims["paraphrase"]), 4),
    }
    print(json.dumps(r, ensure_ascii=False), flush=True)
    out = os.path.join(HERE, "results_qwen.json")
    json.dump(
        {
            "pairs_version": PAIRS["version"],
            "note": (
                "V-EMB 追加実測（Issue #19）。run_bench.py と同一条件。"
                "Qwen3-Embedding は指示文なし（他6モデルと同じ素の条件）で測定。"
            ),
            "results": [r],
        },
        open(out, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    print("WROTE", out)


if __name__ == "__main__":
    main()
