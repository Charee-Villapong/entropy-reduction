"""埋め込み経路の共通部品（SPEC §1 / §4、P2）.

チャンク化・モデル接頭辞・ベクトル化・PCA 圧縮・コサイン類似度・
順位ベースのペア選択を、疎結合な純粋関数として提供する。

D1: モジュールトップレベルでは重い import をしない。
``sentence_transformers`` と ``numpy`` は、それらを必要とする関数の
内部で遅延 import する（単体 import は軽いこと）。
"""

import math
import re

_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n")


def chunk_text(text, max_chars):
    """テキストを段落単位でチャンク化する決定的な関数。

    段落（空行区切り）を1チャンクとし、``max_chars`` を超える段落は
    ``max_chars // 8`` の重なりを持たせて分割する。
    空文字（および空白だけの入力）は空リストを返す。
    """
    if not isinstance(text, str):
        raise ValueError(f"text は文字列でなければなりません: {type(text)!r}")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise ValueError(f"max_chars は整数でなければなりません: {max_chars!r}")
    if max_chars < 1:
        raise ValueError(f"max_chars は 1 以上でなければなりません: {max_chars!r}")
    if text == "":
        return []

    overlap = max_chars // 8
    step = max_chars - overlap
    if step < 1:
        step = max_chars

    chunks = []
    for paragraph in _PARAGRAPH_SPLIT_RE.split(text):
        paragraph = paragraph.strip()
        if paragraph == "":
            continue
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue
        start = 0
        while start < len(paragraph):
            chunks.append(paragraph[start : start + max_chars])
            if start + max_chars >= len(paragraph):
                break
            start += step
    return chunks


def model_prefix(model_name):
    """対称類似のための接頭辞ポリシーを返す。

    名前に "multilingual-e5" を含むモデルは "query: "、それ以外は ""。
    V-EMB ベンチ（bench/v-emb/run_bench.py）と同じ方針。
    """
    if isinstance(model_name, str) and "multilingual-e5" in model_name:
        return "query: "
    return ""


def embed_texts(texts, model_name):
    """テキスト列を埋め込みベクトル列にする（キャッシュのみで読む）。

    ``sentence_transformers`` は関数内で遅延 import する。
    モデルは ``local_files_only=True`` でキャッシュからのみ読み込み、
    外部送信はしない（SPEC §1）。
    """
    texts = list(texts)
    if not texts:
        return []
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device="cpu", local_files_only=True)
    prefix = model_prefix(model_name)
    prefixed = [prefix + text for text in texts]
    vectors = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
    return [[float(value) for value in vector] for vector in vectors]


def pca_reduce(vectors, n_components):
    """ベクトル列を PCA で上位 ``n_components`` 成分へ射影する。

    中心化 → SVD → 上位 k 成分への射影。k は
    min(n_components, サンプル数, 次元数) に丸める。
    各成分の符号は「最大絶対値要素が正」になるよう固定し、決定的にする。
    サンプル数が1以下なら入力をそのまま返す。
    ``numpy`` は関数内で遅延 import する。
    """
    if len(vectors) <= 1:
        return vectors
    import numpy as np

    array = np.asarray(vectors, dtype=float)
    if array.ndim != 2:
        raise ValueError("vectors は2次元のベクトル列でなければなりません")
    n_samples, n_dims = array.shape
    k = min(int(n_components), n_samples, n_dims)
    if k < 1:
        raise ValueError(
            f"n_components は 1 以上でなければなりません: {n_components!r}"
        )

    centered = array - array.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    components = np.array(vt[:k], dtype=float)
    for row in range(k):
        pivot = int(np.argmax(np.abs(components[row])))
        if components[row][pivot] < 0:
            components[row] = -components[row]
    projected = centered @ components.T
    return [[float(value) for value in row] for row in projected]


def cosine_matrix(vectors):
    """純 Python のコサイン類似度行列を返す。

    ゼロベクトルと他ベクトル（ゼロベクトル同士を含む）の類似度は 0.0。
    """
    norms = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
    size = len(vectors)
    matrix = []
    for i in range(size):
        row = []
        for j in range(size):
            if norms[i] == 0.0 or norms[j] == 0.0:
                row.append(0.0)
            elif i == j:
                row.append(1.0)
            else:
                dot = sum(a * b for a, b in zip(vectors[i], vectors[j]))
                similarity = dot / (norms[i] * norms[j])
                row.append(max(-1.0, min(1.0, similarity)))
        matrix.append(row)
    return matrix


def _top_k_neighbors(similarities, top_k):
    """各要素について、近傍上位 top_k の index 集合を返す。

    順位は類似度降順、同点は index 昇順で決定的に決める。
    自分自身は近傍に含めない。
    """
    size = len(similarities)
    neighbors = []
    for i in range(size):
        order = sorted(
            (j for j in range(size) if j != i),
            key=lambda j: (-similarities[i][j], j),
        )
        neighbors.append(set(order[:top_k]))
    return neighbors


def select_pairs(vectors, top_k, relative_margin, min_similarity):
    """類似度行列から候補ペア (i, j, similarity) を選ぶ。

    条件は3つすべてを満たすこと:
    ① j が i の近傍上位 top_k 内かつ i が j の近傍上位 top_k 内
    ② similarity が i・j それぞれの「他全要素との平均類似度 +
       relative_margin」を超える
    ③ similarity >= min_similarity

    i < j の各ペアは最大1回、結果は (i, j) 昇順で決定的に返す。
    """
    size = len(vectors)
    if size < 2:
        return []
    if top_k < 1:
        return []

    similarities = cosine_matrix(vectors)
    neighbors = _top_k_neighbors(similarities, top_k)
    means = []
    for i in range(size):
        others = [similarities[i][j] for j in range(size) if j != i]
        means.append(sum(others) / len(others))

    pairs = []
    for i in range(size):
        for j in range(i + 1, size):
            similarity = similarities[i][j]
            if j not in neighbors[i] or i not in neighbors[j]:
                continue
            if not similarity > means[i] + relative_margin:
                continue
            if not similarity > means[j] + relative_margin:
                continue
            if similarity < min_similarity:
                continue
            pairs.append((i, j, similarity))
    return pairs
