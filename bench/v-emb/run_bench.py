"""V-EMB 実測スクリプト（Issue #19）。

固定ペアセット（pairs.json）で6候補を同一条件で測る。
物差し: 言い換え検出（paraphrase 平均類似度）/ 否定の区別（negation 平均類似度・
言い換えとのマージン・順位精度・最悪の否定ペア）/ 実用面（ロード時間・encode 速度・実サイズ）。

接頭辞ポリシー（各モデルの文書化された対称類似の用法に従う）:
  e5 系は全⽂に "query: " を付ける。その他は接頭辞なし。
"""

import json
import os
import statistics
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = json.load(open(os.path.join(HERE, "pairs.json"), encoding="utf-8"))

MODELS = [
    "intfloat/multilingual-e5-small",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "intfloat/multilingual-e5-base",
    "cl-nagoya/ruri-v3-310m",
    "intfloat/multilingual-e5-large",
    "BAAI/bge-m3",
]
PREFIX = {m: ("query: " if "multilingual-e5" in m else "") for m in MODELS}

_COMMON_TAIL = [
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "1_Pooling/config.json",
    "config.json",
]
FILESETS = {
    "intfloat/multilingual-e5-small": ["model.safetensors", "sentencepiece.bpe.model"]
    + _COMMON_TAIL,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": [
        "model.safetensors",
        "sentencepiece.bpe.model",
        "config_sentence_transformers.json",
        "unigram.json",
    ]
    + _COMMON_TAIL,
    "intfloat/multilingual-e5-base": ["model.safetensors", "sentencepiece.bpe.model"]
    + _COMMON_TAIL,
    "cl-nagoya/ruri-v3-310m": [
        "model.safetensors",
        "tokenizer.model",
        "config_sentence_transformers.json",
    ]
    + _COMMON_TAIL,
    "intfloat/multilingual-e5-large": ["model.safetensors", "sentencepiece.bpe.model"]
    + _COMMON_TAIL,
    "BAAI/bge-m3": [
        "pytorch_model.bin",
        "sentencepiece.bpe.model",
        "config_sentence_transformers.json",
    ]
    + _COMMON_TAIL,
}


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
    results = []
    for repo in MODELS:
        path = snapshot_download(
            repo_id=repo, allow_patterns=FILESETS[repo], local_files_only=True
        )  # resolve cached snapshot
        size = dir_bytes(path)
        t0 = time.time()
        model = SentenceTransformer(path, device="cpu")  # load from local snapshot only
        load_s = time.time() - t0
        texts = [PREFIX[repo] + t for (_c, _i, _s, t) in flat]
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
            "model": repo,
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
        results.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        del model
    out = os.path.join(HERE, "results.json")
    json.dump(
        {"pairs_version": PAIRS["version"], "results": results},
        open(out, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    print("WROTE", out)


if __name__ == "__main__":
    main()
