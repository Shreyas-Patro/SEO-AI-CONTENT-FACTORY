import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _now():
    return datetime.utcnow()


def _uuid():
    return str(uuid.uuid4())


class Article(Base):
    __tablename__ = "articles"

    article_id = Column(String, primary_key=True)
    slug = Column(String, unique=True, nullable=False)
    title = Column(String)
    url = Column(String)
    file_path = Column(String)
    content_hash = Column(String)
    frontmatter_json = Column(Text)
    status = Column(String, default="new")  # new | unchanged | changed | error

    # NEW: long-tail anchor phrases derived from the title.
    # JSON list of lowercased phrases, e.g.
    # ["how to avoid overfitting in machine learning models",
    #  "avoid overfitting in machine learning models"]
    title_phrases_json = Column(Text)

    # NEW: embedding of title + first chunk used as the "article representation".
    # Used as the semantic gate when a source chunk matches one of this article's
    # title_phrases — we compare source-chunk-embedding to this vector.
    representation_vector = Column(LargeBinary)
    representation_model = Column(String)
    representation_hash = Column(String)  # hash of (title + intro) for cache invalidation

    last_ingested_at = Column(DateTime, default=_now)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    chunks = relationship("Chunk", back_populates="article", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("article_id", "chunk_hash", name="uq_article_chunk_hash"),)

    chunk_id = Column(String, primary_key=True, default=_uuid)
    article_id = Column(String, ForeignKey("articles.article_id"), nullable=False)
    heading = Column(String)
    text = Column(Text, nullable=False)
    char_start = Column(Integer, nullable=False)
    char_end = Column(Integer, nullable=False)
    word_count = Column(Integer)
    chunk_hash = Column(String, nullable=False)
    position_index = Column(Integer)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    article = relationship("Article", back_populates="chunks")
    embedding = relationship("Embedding", back_populates="chunk", uselist=False, cascade="all, delete-orphan")
    source_matches = relationship("Match", foreign_keys="Match.source_chunk_id", back_populates="source_chunk")
    target_matches = relationship("Match", foreign_keys="Match.target_chunk_id", back_populates="target_chunk")


class Embedding(Base):
    __tablename__ = "embeddings"

    chunk_id = Column(String, ForeignKey("chunks.chunk_id"), primary_key=True)
    model = Column(String)
    vector = Column(LargeBinary, nullable=False)
    dimensions = Column(Integer)
    chunk_hash = Column(String)
    computed_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    chunk = relationship("Chunk", back_populates="embedding")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("source_chunk_id", "target_chunk_id", "matched_phrase",
                                       name="uq_match_pair_phrase"),)

    match_id = Column(String, primary_key=True, default=_uuid)
    source_chunk_id = Column(String, ForeignKey("chunks.chunk_id"), nullable=False)
    target_chunk_id = Column(String, ForeignKey("chunks.chunk_id"), nullable=False)
    similarity_score = Column(Float, nullable=False)

    # NEW: the actual long-tail phrase, copied verbatim from source
    # (preserves original capitalisation). This IS the anchor text.
    matched_phrase = Column(String)
    phrase_char_start = Column(Integer)  # offset within source chunk.text
    phrase_char_end = Column(Integer)
    # which title phrase (lowercased) was matched — for debugging / review UI
    matched_title_phrase = Column(String)

    status = Column(String, default="pending_anchor")  # pending_anchor | anchor_ready | anchor_error
    match_hash = Column(String)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    source_chunk = relationship("Chunk", foreign_keys=[source_chunk_id], back_populates="source_matches")
    target_chunk = relationship("Chunk", foreign_keys=[target_chunk_id], back_populates="target_matches")
    anchor = relationship("Anchor", back_populates="match", uselist=False, cascade="all, delete-orphan")


class Anchor(Base):
    __tablename__ = "anchors"
    __table_args__ = (UniqueConstraint("cache_key", name="uq_anchor_cache_key"),)

    anchor_id = Column(String, primary_key=True, default=_uuid)
    match_id = Column(String, ForeignKey("matches.match_id"), nullable=False)
    anchor_text = Column(String)
    reasoning = Column(Text)
    llm_confidence = Column(Integer)
    model = Column(String)
    cache_key = Column(String)
    status = Column(String, default="pending_review")  # pending_review | approved | rejected | edited
    edited_anchor = Column(String)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    match = relationship("Match", back_populates="anchor")
    injection = relationship("Injection", back_populates="anchor", uselist=False, cascade="all, delete-orphan")


class Injection(Base):
    __tablename__ = "injections"

    injection_id = Column(String, primary_key=True, default=_uuid)
    anchor_id = Column(String, ForeignKey("anchors.anchor_id"), nullable=False)
    run_id = Column(String)
    status = Column(String, default="pending")
    error_message = Column(Text)
    injected_at = Column(DateTime)
    backup_path = Column(String)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    anchor = relationship("Anchor", back_populates="injection")


class Error(Base):
    __tablename__ = "errors"

    error_id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String)
    stage = Column(String)
    article_id = Column(String, ForeignKey("articles.article_id"), nullable=True)
    chunk_id = Column(String, nullable=True)
    match_id = Column(String, nullable=True)
    error_type = Column(String)
    message = Column(Text)
    rerun_eligible = Column(Boolean, default=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class Run(Base):
    __tablename__ = "runs"

    run_id = Column(String, primary_key=True, default=_uuid)
    started_at = Column(DateTime, default=_now)
    completed_at = Column(DateTime, nullable=True)
    articles_processed = Column(Integer, default=0)
    chunks_created = Column(Integer, default=0)
    embeddings_computed = Column(Integer, default=0)
    matches_found = Column(Integer, default=0)
    links_approved = Column(Integer, default=0)
    links_injected = Column(Integer, default=0)
    errors_total = Column(Integer, default=0)
    config_json = Column(Text)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)