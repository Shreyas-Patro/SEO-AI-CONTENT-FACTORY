"""
Matching: for each source chunk, scan all OTHER articles' canonical phrases.
If a phrase appears verbatim (case-insensitive, word-boundary aware) in the
source chunk, gate by semantic similarity between chunk and target article
representation. If similarity passes threshold, record a match.

Phrases come from extract_phrases.py (LLM-derived, cached by content_hash).
"""
from hashlib import md5
from typing import Dict, List, Optional, Tuple

import numpy as np

from link_engine.config import get_config
from link_engine.db.models import Article, Chunk, Embedding, Match
from link_engine.stages.embed import bytes_to_vector
from link_engine.stages.extract_phrases import get_phrases_for_article


def compute_match_hash(source_chunk_hash: str, target_article_id: str, matched_phrase: str) -> str:
    return md5(f"{source_chunk_hash}:{target_article_id}:{matched_phrase.lower()}".encode()).hexdigest()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _find_phrase_case_insensitive(haystack: str, needle: str) -> Optional[Tuple[int, int, str]]:
    idx = haystack.lower().find(needle.lower())
    if idx == -1:
        return None
    end = idx + len(needle)
    return (idx, end, haystack[idx:end])


def _phrase_is_in_code_block(text: str, pos: int) -> bool:
    return text[:pos].count("```") % 2 == 1


def _phrase_is_inside_existing_link(text: str, pos: int) -> bool:
    import re
    for m in re.finditer(r'\[([^\]]+)\]\([^)]+\)', text):
        if m.start() <= pos <= m.end():
            return True
    return False


def _is_word_boundary_match(text: str, start: int, end: int) -> bool:
    """
    Prevent 'ev' from matching inside 'every' or 'several', etc.
    Short phrases are especially vulnerable — require alphanumeric boundaries on both sides.
    """
    if start > 0:
        prev = text[start - 1]
        if prev.isalnum() or prev == "_":
            return False
    if end < len(text):
        nxt = text[end]
        if nxt.isalnum() or nxt == "_":
            return False
    return True


def compute_matches(session, run_id: str = None) -> int:
    cfg = get_config()
    threshold = cfg.get("similarity_threshold", 0.55)
    max_links_per_article = cfg.get("max_links_per_article", 6)
    top_n = cfg.get("top_n_matches", 8)
    min_w = cfg.get("anchor_min_words", 1)
    max_w = cfg.get("anchor_max_words", 8)

    articles = session.query(Article).all()
    if len(articles) < 2:
        return 0

    article_index: Dict[str, Tuple[Article, List[str], Optional[np.ndarray]]] = {}
    for a in articles:
        phrases = get_phrases_for_article(a)
        if not phrases:
            continue
        rep_vec = bytes_to_vector(a.representation_vector) if a.representation_vector else None
        article_index[a.article_id] = (a, phrases, rep_vec)

    if len(article_index) < 2:
        return 0

    all_chunks = session.query(Chunk).all()
    chunk_embeddings: Dict[str, np.ndarray] = {}
    for emb in session.query(Embedding).all():
        chunk_embeddings[emb.chunk_id] = bytes_to_vector(emb.vector)

    candidates = []

    for source_chunk in all_chunks:
        source_article_id = source_chunk.article_id
        source_text = source_chunk.text
        source_vec = chunk_embeddings.get(source_chunk.chunk_id)

        chunk_candidates = []

        for target_article_id, (target_article, phrases, rep_vec) in article_index.items():
            if target_article_id == source_article_id:
                continue

            best_hit = None
            for phrase in phrases:
                words = phrase.split()
                if not (min_w <= len(words) <= max_w):
                    continue

                found = _find_phrase_case_insensitive(source_text, phrase)
                if not found:
                    continue
                start, end, exact_substring = found

                if not _is_word_boundary_match(source_text, start, end):
                    continue
                if _phrase_is_in_code_block(source_text, start):
                    continue
                if _phrase_is_inside_existing_link(source_text, start):
                    continue

                if best_hit is None or len(phrase.split()) > len(best_hit["phrase"].split()):
                    best_hit = {
                        "phrase": phrase,
                        "start": start,
                        "end": end,
                        "source_phrase_as_written": exact_substring,
                    }

            if best_hit is None:
                continue

            if source_vec is None or rep_vec is None:
                continue
            sim = _cosine(source_vec, rep_vec)
            if sim < threshold:
                continue

            chunk_candidates.append({
                "target_article": target_article,
                "phrase": best_hit["phrase"],
                "start": best_hit["start"],
                "end": best_hit["end"],
                "source_phrase_as_written": best_hit["source_phrase_as_written"],
                "similarity": sim,
            })

        chunk_candidates.sort(
            key=lambda c: (len(c["phrase"].split()), c["similarity"]),
            reverse=True,
        )
        chunk_candidates = chunk_candidates[:top_n]

        for c in chunk_candidates:
            candidates.append({
                "source_chunk": source_chunk,
                "target_article": c["target_article"],
                "phrase": c["phrase"],
                "start": c["start"],
                "end": c["end"],
                "source_phrase_as_written": c["source_phrase_as_written"],
                "similarity": c["similarity"],
            })

    candidates.sort(key=lambda c: c["similarity"], reverse=True)
    seen_pairs = set()
    per_source_article_count: Dict[str, int] = {}
    new_matches = 0

    for c in candidates:
        source_chunk = c["source_chunk"]
        target_article = c["target_article"]
        source_article_id = source_chunk.article_id
        pair_key = (source_article_id, target_article.article_id)

        if pair_key in seen_pairs:
            continue
        if per_source_article_count.get(source_article_id, 0) >= max_links_per_article:
            continue

        target_chunks_sorted = sorted(target_article.chunks, key=lambda ch: ch.position_index or 0)
        if not target_chunks_sorted:
            continue
        target_chunk = target_chunks_sorted[0]

        match_hash = compute_match_hash(source_chunk.chunk_hash, target_article.article_id, c["phrase"])

        existing = session.query(Match).filter_by(match_hash=match_hash).first()
        if existing:
            seen_pairs.add(pair_key)
            per_source_article_count[source_article_id] = per_source_article_count.get(source_article_id, 0) + 1
            continue

        match = Match(
            source_chunk_id=source_chunk.chunk_id,
            target_chunk_id=target_chunk.chunk_id,
            similarity_score=c["similarity"],
            matched_phrase=c["source_phrase_as_written"],
            matched_title_phrase=c["phrase"],
            phrase_char_start=c["start"],
            phrase_char_end=c["end"],
            match_hash=match_hash,
            status="pending_anchor",
        )
        session.add(match)
        seen_pairs.add(pair_key)
        per_source_article_count[source_article_id] = per_source_article_count.get(source_article_id, 0) + 1
        new_matches += 1

    session.flush()
    return new_matches