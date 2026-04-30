from hashlib import md5
from typing import List

import numpy as np

from link_engine.config import get_config
from link_engine.db.models import Article, Chunk, Embedding, Error

_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        cfg = get_config()
        model_name = cfg.get("embedding_model", "all-mpnet-base-v2")
        _model = SentenceTransformer(model_name)
    return _model


def vector_to_bytes(vector: np.ndarray) -> bytes:
    return vector.astype(np.float32).tobytes()


def bytes_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def embed_chunks(chunks: List[Chunk], session) -> int:
    cfg = get_config()
    model_name = cfg.get("embedding_model", "all-mpnet-base-v2")
    model = get_model()

    to_embed = []
    for chunk in chunks:
        existing = session.get(Embedding, chunk.chunk_id)
        if existing and existing.chunk_hash == chunk.chunk_hash and existing.model == model_name:
            continue
        to_embed.append(chunk)

    if not to_embed:
        return 0

    batch_size = 100
    computed = 0

    for i in range(0, len(to_embed), batch_size):
        batch = to_embed[i:i + batch_size]
        texts = [c.text for c in batch]

        try:
            vectors = model.encode(texts, show_progress_bar=False)
            for chunk, vector in zip(batch, vectors):
                blob = vector_to_bytes(vector)
                existing = session.get(Embedding, chunk.chunk_id)
                if existing:
                    existing.vector = blob
                    existing.chunk_hash = chunk.chunk_hash
                    existing.model = model_name
                    existing.dimensions = len(vector)
                else:
                    session.add(Embedding(
                        chunk_id=chunk.chunk_id,
                        model=model_name,
                        vector=blob,
                        dimensions=len(vector),
                        chunk_hash=chunk.chunk_hash,
                    ))
                computed += 1
        except Exception as e:
            for chunk in batch:
                session.add(Error(
                    stage="embedding",
                    chunk_id=chunk.chunk_id,
                    article_id=chunk.article_id,
                    error_type="embedding_error",
                    message=str(e),
                    rerun_eligible=True,
                ))

    session.flush()
    return computed


def embed_all_pending(session) -> int:
    all_chunks = session.query(Chunk).all()
    return embed_chunks(all_chunks, session)


def _build_representation_text(article: Article) -> str:
    """
    The target-article representation is the text we embed to decide whether
    a source chunk that matches this article's title phrase is genuinely about
    the same topic.

    We use: title + first chunk's text. Title carries the topic signal, first
    chunk typically carries the thesis / intro content.
    """
    first_chunk = (
        sorted(article.chunks, key=lambda c: c.position_index or 0)[0]
        if article.chunks else None
    )
    parts = [article.title or ""]
    if first_chunk:
        parts.append(first_chunk.text)
    return "\n\n".join(p for p in parts if p).strip()


def _compute_rep_hash(text: str, model_name: str) -> str:
    return md5(f"{model_name}::{text}".encode()).hexdigest()


def embed_article_representations(session) -> int:
    """
    For every article, compute (or refresh) a representation vector used as
    the semantic gate in the matching stage.
    """
    cfg = get_config()
    model_name = cfg.get("embedding_model", "all-mpnet-base-v2")
    model = get_model()

    articles = session.query(Article).all()
    to_compute = []

    for article in articles:
        text = _build_representation_text(article)
        if not text:
            continue
        rep_hash = _compute_rep_hash(text, model_name)
        if (
            article.representation_vector is not None
            and article.representation_hash == rep_hash
            and article.representation_model == model_name
        ):
            continue
        to_compute.append((article, text, rep_hash))

    if not to_compute:
        return 0

    batch_size = 50
    computed = 0

    for i in range(0, len(to_compute), batch_size):
        batch = to_compute[i:i + batch_size]
        texts = [t for (_, t, _) in batch]
        try:
            vectors = model.encode(texts, show_progress_bar=False)
            for (article, _text, rep_hash), vector in zip(batch, vectors):
                article.representation_vector = vector_to_bytes(vector)
                article.representation_model = model_name
                article.representation_hash = rep_hash
                computed += 1
        except Exception as e:
            for (article, _t, _h) in batch:
                session.add(Error(
                    stage="embedding",
                    article_id=article.article_id,
                    error_type="representation_embedding_error",
                    message=str(e),
                    rerun_eligible=True,
                ))

    session.flush()
    return computed