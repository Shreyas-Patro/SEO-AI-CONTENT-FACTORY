"""
Semantic text chunking.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_text(text, chunk_size=500, chunk_overlap=50):
    """Split text into semantically meaningful chunks."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " "],
        length_function=len,
    )
    chunks = splitter.split_text(text)
    return chunks