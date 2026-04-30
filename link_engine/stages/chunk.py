import re
import uuid
from hashlib import md5
from typing import List, Tuple, Optional

from link_engine.db.models import Article, Chunk, Error
from link_engine.db.session import get_session


def compute_chunk_hash(text: str) -> str:
    return md5(text.encode()).hexdigest()


def _split_into_sections(body: str) -> List[Tuple[Optional[str], str, int]]:
    """
    Split markdown body by ## and ### headings.
    Returns list of (heading, text, char_start_of_text).
    """
    # Regex finds headings at start of line
    pattern = re.compile(r'^(#{2,3} .+)$', re.MULTILINE)
    matches = list(pattern.finditer(body))

    sections = []

    if not matches:
        # No headings — treat whole body as one section
        sections.append((None, body.strip(), 0))
        return sections

    # Text before first heading (intro)
    if matches[0].start() > 0:
        intro = body[:matches[0].start()].strip()
        if intro:
            sections.append((None, intro, 0))

    for i, match in enumerate(matches):
        heading = match.group(1).lstrip('#').strip()
        text_start = match.end() + 1  # +1 for newline
        text_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[text_start:text_end].strip()

        # char_start is where the heading line starts
        section_char_start = match.start()
        sections.append((heading, text, section_char_start))

    return sections


def _split_on_paragraphs(text: str, char_offset: int, max_words: int = 300) -> List[Tuple[str, int, int]]:
    """
    Further split a large text block on paragraph boundaries.
    Returns list of (chunk_text, char_start, char_end).
    """
    paragraphs = re.split(r'\n\n+', text)
    chunks = []
    current_text = ""
    current_start = char_offset
    cursor = char_offset

    for para in paragraphs:
        para_word_count = len(para.split())
        combined_words = len((current_text + " " + para).split()) if current_text else para_word_count

        if current_text and combined_words > max_words:
            # Flush current
            chunks.append((current_text.strip(), current_start, cursor))
            current_start = cursor
            current_text = para
        else:
            current_text = (current_text + "\n\n" + para).strip() if current_text else para

        cursor += len(para) + 2  # +2 for \n\n separator

    if current_text.strip():
        chunks.append((current_text.strip(), current_start, cursor))

    return chunks


def chunk_article(article: Article, session) -> List[Chunk]:
    """
    Chunk a single article's body text.
    Deletes old chunks and creates fresh ones.
    """
    # Delete stale chunks for this article
    session.query(Chunk).filter(Chunk.article_id == article.article_id).delete()
    session.flush()

    try:
        # Read the body text (strip frontmatter)
        import frontmatter as fm
        raw = open(article.file_path, encoding="utf-8").read()
        post = fm.loads(raw)
        body = post.content

        # Compute frontmatter offset (chars before body in raw file)
        # We need char offsets relative to the BODY for injection
        # Store them relative to body; inject.py adds frontmatter length back
        sections = _split_into_sections(body)
        chunks = []
        position_index = 0

        for heading, section_text, section_start in sections:
            word_count = len(section_text.split())

            if word_count <= 300:
                # Single chunk
                chunk_text = section_text
                char_start = section_start
                char_end = section_start + len(section_text)
                chunk = Chunk(
                    chunk_id=str(uuid.uuid4()),
                    article_id=article.article_id,
                    heading=heading,
                    text=chunk_text,
                    char_start=char_start,
                    char_end=char_end,
                    word_count=len(chunk_text.split()),
                    chunk_hash=compute_chunk_hash(chunk_text),
                    position_index=position_index,
                )
                session.add(chunk)
                chunks.append(chunk)
                position_index += 1
            else:
                # Split large section into paragraph-based sub-chunks
                sub_chunks = _split_on_paragraphs(section_text, section_start)
                for i, (sub_text, sub_start, sub_end) in enumerate(sub_chunks):
                    chunk = Chunk(
                        chunk_id=str(uuid.uuid4()),
                        article_id=article.article_id,
                        heading=heading if i == 0 else f"{heading} (cont.)",
                        text=sub_text,
                        char_start=sub_start,
                        char_end=sub_end,
                        word_count=len(sub_text.split()),
                        chunk_hash=compute_chunk_hash(sub_text),
                        position_index=position_index,
                    )
                    session.add(chunk)
                    chunks.append(chunk)
                    position_index += 1

        session.flush()
        return chunks

    except Exception as e:
        session.add(Error(
            stage="chunking",
            article_id=article.article_id,
            error_type="chunk_error",
            message=str(e),
            rerun_eligible=True,
        ))
        return []


def chunk_all_articles(articles: List[Article], session) -> int:
    total = 0
    for article in articles:
        chunks = chunk_article(article, session)
        total += len(chunks)
    return total