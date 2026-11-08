"""第1層：候補の機械抽出（SPEC §2 / §3 candidates / D4 / D6 / D10）.

入力（detect_input）：``snapshot`` / ``inventory_candidates`` /
``items`` / 任意で ``documents``。

- ``inventory_candidates`` は棚卸しランからの引き継ぎで、
  ``source: inventory`` としてそのまま候補に載せる。
- ``items`` からは設定のしきい値（入口の目安）で肥大・死蔵を拾い、
  同一 ``signature`` を共有する群からは重複候補を1件だけ出す
  （いずれも ``source: weekly``）。境界は「以上（>=）」で適用する。
- ``documents`` があれば埋め込み経路で重複候補を出す
  （``source: embedding``）。出力には本文の実値を含めず、
  類似度などのメタ情報だけを載せる（D4）。

第1層は判断を入れない。出力 item は id / source / target /
category / title / detail の6キーちょうどで新規に組み立て、
入力 item の余分なキー（content 等）は絶対に混ぜない（D4）。
候補 ID は発行順に cand-1, cand-2, ... と決定的に振る（D10）。
発行順は inventory（入力順）→ items（入力順。同一 item 内は
bloat → dead）→ signature 重複（初出順）→ embedding。
"""

from . import contracts, embeddings
from .cli import run_cli


def _config_get(config, key, default=None):
    """config が dict でもオブジェクトでも値を読めるようにする。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _required_threshold(config, key):
    value = _config_get(config, key)
    if value is None:
        raise ValueError(f"設定 {key} が読めません")
    return value


def _is_md_item(item, target):
    file_type = item.get("file_type")
    if file_type is None:
        return target.lower().endswith(".md")
    return file_type == "md"


def _embedding_candidates(payload, config, append_candidate):
    """埋め込み経路：documents から重複候補を出す。"""
    documents = payload.get("documents")
    if documents is None:
        return
    if not isinstance(documents, list):
        raise ValueError("documents はリストでなければなりません")
    if not documents:
        return

    model = _config_get(config, "embedding_model")
    if not model:
        raise ValueError("埋め込み経路には設定 embedding_model が必要です")
    chunk_chars = _config_get(config, "embedding_chunk_chars", 800)
    if chunk_chars is None:
        chunk_chars = 800
    pca_components = _config_get(config, "embedding_pca_components", 64)
    if pca_components is None:
        pca_components = 64
    top_k = _config_get(config, "embedding_top_k", 3)
    if top_k is None:
        top_k = 3
    relative_margin = _config_get(config, "embedding_relative_margin", 0.03)
    if relative_margin is None:
        relative_margin = 0.03
    min_similarity = _config_get(config, "embedding_min_similarity", 0.5)
    if min_similarity is None:
        min_similarity = 0.5

    target_first_index = {}
    chunk_texts = []
    chunk_doc_indexes = []
    for doc_index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise ValueError(f"document は dict でなければなりません: {document!r}")
        target = document.get("target")
        if not isinstance(target, str) or target == "":
            raise ValueError(f"document に target がありません: {document!r}")
        text = document.get("text")
        if not isinstance(text, str):
            raise ValueError(f"document に text がありません: {document!r}")
        if target not in target_first_index:
            target_first_index[target] = doc_index
        for chunk in embeddings.chunk_text(text, chunk_chars):
            chunk_texts.append(chunk)
            chunk_doc_indexes.append(doc_index)

    if len(chunk_texts) < 2:
        return

    vectors = embeddings.embed_texts(chunk_texts, model)
    reduced = embeddings.pca_reduce(vectors, pca_components)
    pairs = embeddings.select_pairs(reduced, top_k, relative_margin, min_similarity)

    doc_targets = [document["target"] for document in documents]
    best_by_target_pair = {}
    for i, j, similarity in pairs:
        target_a = doc_targets[chunk_doc_indexes[i]]
        target_b = doc_targets[chunk_doc_indexes[j]]
        if target_first_index[target_a] <= target_first_index[target_b]:
            key = (target_a, target_b)
        else:
            key = (target_b, target_a)
        if key not in best_by_target_pair or similarity > best_by_target_pair[key]:
            best_by_target_pair[key] = similarity

    def _pair_order(key):
        return (target_first_index[key[0]], target_first_index[key[1]])

    for target_a, target_b in sorted(best_by_target_pair, key=_pair_order):
        best = best_by_target_pair[(target_a, target_b)]
        if target_a == target_b:
            detail = (
                f"同一文書内でチャンクレベルの類似ペアを検出"
                f"（最良類似度 {best:.3f}）"
            )
        else:
            detail = (
                f"{target_b} との間でチャンクレベルの類似ペアを検出"
                f"（最良類似度 {best:.3f}）"
            )
        append_candidate(
            source="embedding",
            target=target_a,
            category="duplicate",
            title="重複の可能性がある文書",
            detail=detail,
        )


def process(payload, config):
    """detect_input から candidates を組み立てる。"""
    if not isinstance(payload, dict):
        raise ValueError("入力は dict でなければなりません")
    if "snapshot" not in payload:
        raise ValueError("入力に snapshot がありません")
    contracts.validate_snapshot(payload["snapshot"])

    bloat_md_lines = _required_threshold(config, "bloat_md_lines")
    unused_days = _required_threshold(config, "unused_days")

    items_out = []

    def append_candidate(source, target, category, title, detail):
        items_out.append(
            {
                "id": f"cand-{len(items_out) + 1}",
                "source": source,
                "target": target,
                "category": category,
                "title": title,
                "detail": detail,
            }
        )

    # 1. inventory_candidates（入力順、そのまま候補化）
    inventory_candidates = payload.get("inventory_candidates") or []
    if not isinstance(inventory_candidates, list):
        raise ValueError("inventory_candidates はリストでなければなりません")
    for candidate in inventory_candidates:
        if not isinstance(candidate, dict):
            raise ValueError(
                f"inventory candidate は dict でなければなりません: {candidate!r}"
            )
        for key in ("target", "category", "title", "detail"):
            if key not in candidate:
                raise ValueError(f"inventory candidate に {key} がありません")
        if candidate["category"] not in contracts.CANDIDATE_CATEGORIES:
            raise ValueError(
                f"inventory candidate の category が不正です: "
                f"{candidate['category']!r}"
            )
        append_candidate(
            source="inventory",
            target=candidate["target"],
            category=candidate["category"],
            title=candidate["title"],
            detail=candidate["detail"],
        )

    # 2. items のしきい値検出（入力順、同一 item 内は bloat → dead）
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise ValueError("items はリストでなければなりません")
    signature_groups = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"item は dict でなければなりません: {item!r}")
        target = item.get("target")
        if not isinstance(target, str) or target == "":
            raise ValueError(f"item に target がありません: {item!r}")

        md_lines = item.get("md_lines")
        if _is_md_item(item, target) and md_lines is not None:
            if md_lines >= bloat_md_lines:
                append_candidate(
                    source="weekly",
                    target=target,
                    category="bloat",
                    title="肥大した md ファイル",
                    detail=(f"md {md_lines}行（入口の目安 {bloat_md_lines}行以上）"),
                )
        last_used_days_ago = item.get("last_used_days_ago")
        if last_used_days_ago is not None and last_used_days_ago >= unused_days:
            append_candidate(
                source="weekly",
                target=target,
                category="dead",
                title="長期間使われていない項目",
                detail=(
                    f"最終利用から {last_used_days_ago}日"
                    f"（入口の目安 {unused_days}日以上）"
                ),
            )

        signature = item.get("signature")
        if isinstance(signature, str) and signature != "":
            signature_groups.setdefault(signature, []).append(target)

    # 3. signature 重複（初出順、群ごとにちょうど1件）
    for signature, targets in signature_groups.items():
        if len(targets) < 2:
            continue
        others = "、".join(targets[1:])
        append_candidate(
            source="weekly",
            target=targets[0],
            category="duplicate",
            title="重複した項目",
            detail=f"{others} と同一シグネチャ {signature} を共有しています",
        )

    # 4. 埋め込み経路（documents がある場合のみ）
    _embedding_candidates(payload, config, append_candidate)

    result = {"kind": "candidates", "version": 1, "items": items_out}
    contracts.validate_candidates(result)
    return result


if __name__ == "__main__":
    raise SystemExit(run_cli(process))
