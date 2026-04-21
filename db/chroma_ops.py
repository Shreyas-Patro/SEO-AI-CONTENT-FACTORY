"""
ChromaDB operations — vector storage for semantic search.
Uses sentence-transformers for local, free embeddings.
"""

import chromadb
from chromadb.config import Settings
import os
from config_loader import get_path

CHROMA_DIR = get_path("chroma_dir")

# Initialize the embedding function (runs locally, free)
from chromadb.utils import embedding_functions
ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

def get_client():
    os.makedirs(CHROMA_DIR, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_DIR)

def init_collections():
    """Create all collections. Safe to run multiple times."""
    client = get_client()

    client.get_or_create_collection(
        name="facts",
        embedding_function=ef,
        metadata={"description": "Individual factual claims with citations"}
    )

    client.get_or_create_collection(
        name="articles",
        embedding_function=ef,
        metadata={"description": "Full article content for similarity and dedup"}
    )

    client.get_or_create_collection(
        name="queries",
        embedding_function=ef,
        metadata={"description": "Target search queries mapped to articles"}
    )

    print(f"ChromaDB collections initialized at {CHROMA_DIR}")
    return client


def store_fact_embedding(fact_id, fact_text, metadata=None):
    """Store a fact in the vector DB for semantic retrieval."""
    client = get_client()
    collection = client.get_collection("facts", embedding_function=ef)
    meta = metadata or {}
    # ChromaDB metadata values must be str, int, float, or bool
    clean_meta = {k: str(v) if not isinstance(v, (int, float, bool)) else v
                  for k, v in meta.items()}
    collection.upsert(
        ids=[fact_id],
        documents=[fact_text],
        metadatas=[clean_meta]
    )


def search_facts(query, top_k=10, where_filter=None):
    """Semantic search for facts relevant to a query."""
    client = get_client()
    collection = client.get_collection("facts", embedding_function=ef)

    kwargs = {
        "query_texts": [query],
        "n_results": min(top_k, collection.count()) if collection.count() > 0 else 1,
    }
    if where_filter:
        kwargs["where"] = where_filter

    if collection.count() == 0:
        return []

    results = collection.query(**kwargs)

    output = []
    for i, doc_id in enumerate(results["ids"][0]):
        output.append({
            "id": doc_id,
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            "distance": results["distances"][0][i] if results["distances"] else 0,
        })
    return output


def store_article_embedding(article_id, article_text, metadata=None):
    """Store article content for dedup detection and cross-referencing."""
    client = get_client()
    collection = client.get_collection("articles", embedding_function=ef)
    meta = metadata or {}
    clean_meta = {k: str(v) if not isinstance(v, (int, float, bool)) else v
                  for k, v in meta.items()}
    # Truncate to ~8000 chars — embedding model has token limits
    truncated = article_text[:8000]
    collection.upsert(
        ids=[article_id],
        documents=[truncated],
        metadatas=[clean_meta]
    )


def search_articles(query, top_k=5):
    """Find articles similar to a query (for interlinking suggestions)."""
    client = get_client()
    collection = client.get_collection("articles", embedding_function=ef)

    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
    )
    output = []
    for i, doc_id in enumerate(results["ids"][0]):
        output.append({
            "id": doc_id,
            "text": results["documents"][0][i][:200] + "...",
            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
            "distance": results["distances"][0][i] if results["distances"] else 0,
        })
    return output


if __name__ == "__main__":
    init_collections()

    # Test: store and retrieve a sample fact
    store_fact_embedding(
        "test-fact-001",
        "The average rent for a 2BHK apartment in HSR Layout, Bangalore is approximately ₹28,000 per month as of 2025.",
        {"category": "property", "location": "HSR Layout"}
    )

    results = search_facts("How much does rent cost in HSR Layout?")
    print(f"\nSearch test — found {len(results)} results:")
    for r in results:
        print(f"  [{r['id']}] {r['text'][:80]}... (distance: {r['distance']:.3f})")