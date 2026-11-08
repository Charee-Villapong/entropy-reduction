"""embeddings unit -- P2 で追加した実装回帰テスト（P0 の受け入れテストとは別）.

対象は ``entropy_reduction.embeddings`` の純粋関数群。
合成ベクトルで検証し、numpy は不要とする（pca_reduce のみ
``pytest.importorskip("numpy")`` で形・決定性・符号の安定を見る）。
"""

import math

import pytest

from entropy_reduction import embeddings


def _unit_vector(degrees):
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


# 角度 0° / 10° / 25° の単位ベクトル。類似度は
# sim(0,1)=cos10°≈0.985, sim(0,2)=cos25°≈0.906, sim(1,2)=cos15°≈0.966。
ANGLE_VECTORS = [_unit_vector(0), _unit_vector(10), _unit_vector(25)]


# --- chunk_text -----------------------------------------------------------


def test_chunk_text_empty_input_gives_empty_list():
    assert embeddings.chunk_text("", 800) == []


def test_chunk_text_splits_on_paragraphs():
    assert embeddings.chunk_text("aaa\n\nbbb", 800) == ["aaa", "bbb"]


def test_chunk_text_long_paragraph_is_split_with_overlap():
    text = "abcdefghijklmnopqrstuvwxy"  # 25文字
    chunks = embeddings.chunk_text(text, 10)
    # 重なりは max_chars // 8 = 1文字。開始位置は 0, 9, 18。
    assert chunks == ["abcdefghij", "jklmnopqrs", "stuvwxy"]
    assert all(len(chunk) <= 10 for chunk in chunks)
    assert chunks[1][0] == chunks[0][-1]


def test_chunk_text_short_paragraphs_are_not_merged_or_split():
    text = "ab\n\n" + "c" * 25
    chunks = embeddings.chunk_text(text, 10)
    assert chunks[0] == "ab"
    assert len(chunks) == 4


def test_chunk_text_is_deterministic():
    text = "段落その一。\n\n" + "あ" * 100 + "\n\n段落その三。"
    first = embeddings.chunk_text(text, 30)
    second = embeddings.chunk_text(text, 30)
    assert first == second
    assert all(len(chunk) <= 30 for chunk in first)


# --- model_prefix -----------------------------------------------------------


def test_model_prefix_e5_gets_query_prefix():
    assert embeddings.model_prefix("intfloat/multilingual-e5-small") == "query: "


def test_model_prefix_non_e5_gets_no_prefix():
    assert embeddings.model_prefix("cl-nagoya/ruri-v3-310m") == ""


# --- cosine_matrix -----------------------------------------------------------


def test_cosine_matrix_zero_vectors_are_zero():
    matrix = embeddings.cosine_matrix([[1.0, 0.0], [0.0, 0.0], [0.0, 0.0]])
    assert matrix[0][0] == 1.0
    assert matrix[0][1] == 0.0
    assert matrix[1][2] == 0.0


# --- select_pairs -----------------------------------------------------------


def test_select_pairs_mutual_top_k_pair_is_selected():
    pairs = embeddings.select_pairs(
        ANGLE_VECTORS, top_k=1, relative_margin=0.005, min_similarity=0.5
    )
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]
    assert pairs[0][2] == pytest.approx(math.cos(math.radians(10)))


def test_select_pairs_non_mutual_top_k_pair_is_rejected():
    # 角度 0° / 8° / 20° / 120°。(1, 2) は 2 の最寄りが 1 だが、
    # 1 の最寄りは 0 なので top_k=1 では相互でない。
    vectors = [
        _unit_vector(0),
        _unit_vector(8),
        _unit_vector(20),
        _unit_vector(120),
    ]
    pairs = embeddings.select_pairs(
        vectors, top_k=1, relative_margin=0.03, min_similarity=0.5
    )
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]
    # top_k を広げると相互になり、同じペアが選ばれる。
    wider = embeddings.select_pairs(
        vectors, top_k=2, relative_margin=0.03, min_similarity=0.5
    )
    assert (1, 2) in [(i, j) for i, j, _ in wider]


def test_select_pairs_relative_margin_rejects():
    pairs = embeddings.select_pairs(
        ANGLE_VECTORS, top_k=2, relative_margin=0.1, min_similarity=0.5
    )
    assert pairs == []


def test_select_pairs_min_similarity_cutoff():
    pairs = embeddings.select_pairs(
        ANGLE_VECTORS, top_k=2, relative_margin=0.005, min_similarity=0.99
    )
    assert pairs == []


def test_select_pairs_is_deterministic_and_sorted():
    first = embeddings.select_pairs(
        ANGLE_VECTORS, top_k=2, relative_margin=0.005, min_similarity=0.5
    )
    second = embeddings.select_pairs(
        ANGLE_VECTORS, top_k=2, relative_margin=0.005, min_similarity=0.5
    )
    assert first == second
    assert [(i, j) for i, j, _ in first] == sorted((i, j) for i, j, _ in first)
    assert all(i < j for i, j, _ in first)


def test_select_pairs_single_vector_gives_no_pairs():
    assert embeddings.select_pairs([[1.0, 0.0]], 3, 0.03, 0.5) == []


# --- pca_reduce -----------------------------------------------------------


def test_pca_reduce_single_sample_is_returned_as_is():
    assert embeddings.pca_reduce([[1.0, 2.0, 3.0]], 2) == [[1.0, 2.0, 3.0]]


def test_pca_reduce_shape_and_clamping():
    pytest.importorskip("numpy")
    vectors = [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 1.0, 0.0],
    ]
    reduced = embeddings.pca_reduce(vectors, 2)
    assert len(reduced) == 4
    assert all(len(row) == 2 for row in reduced)
    # 要求次元がサンプル数・次元数を超える場合は小さい方に丸められる。
    clamped = embeddings.pca_reduce(vectors, 64)
    assert all(len(row) == 3 for row in clamped)


def test_pca_reduce_is_deterministic_with_stable_sign():
    pytest.importorskip("numpy")
    vectors = [
        [2.0, 0.0],
        [0.0, 1.0],
        [-2.0, 0.0],
        [0.0, -1.0],
    ]
    first = embeddings.pca_reduce(vectors, 1)
    second = embeddings.pca_reduce(vectors, 1)
    assert first == second
    # 第1成分は x 軸方向で、符号固定により (2, 0) の射影は正になる。
    assert first[0][0] > 0
    assert first[2][0] < 0
