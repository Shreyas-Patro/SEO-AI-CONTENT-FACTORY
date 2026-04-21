"""
Research Ingestion Pipeline
Upload → Extract → Chunk → Extract Facts → Embed → Store → Verify
"""

import json
import os
from ingest.extract import extract_text
from ingest.chunk import chunk_text
from llm import call_llm_json, call_llm
from db.sqlite_ops import insert_fact, insert_source, add_to_verification_queue
from db.chroma_ops import store_fact_embedding
from db.graph_ops import load_graph, add_fact_node, add_location, add_topic, save_graph


def load_prompt(name):
    """Load a prompt template from the prompts directory."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", f"{name}.md")
    with open(path, "r") as f:
        return f.read()


def ingest_research_doc(filepath, source_url="", source_title="", source_author=""):
    """
    Full ingestion pipeline for a research document.
    Returns a summary of what was ingested.
    """
    print(f"\n{'='*60}")
    print(f"INGESTING: {os.path.basename(filepath)}")
    print(f"{'='*60}")

    # Step 1: Extract text
    print("\n[1/5] Extracting text...")
    text = extract_text(filepath)
    print(f"  Extracted {len(text)} characters")

    # Step 2: Chunk
    print("\n[2/5] Chunking text...")
    chunks = chunk_text(text)
    print(f"  Created {len(chunks)} chunks")

    # Step 3: Create source record
    if not source_title:
        source_title = os.path.basename(filepath)
    source_id = insert_source(
        url=source_url, title=source_title, author=source_author
    )

    # Step 4: Extract facts from each chunk
    print("\n[3/5] Extracting facts from chunks...")
    fact_extractor_prompt = load_prompt("fact_extractor")
    all_facts = []
    total_cost = 0

    for i, chunk in enumerate(chunks):
        prompt = f"Extract facts from this text chunk:\n\n---\n{chunk}\n---"
        try:
            result = call_llm_json(prompt, system=fact_extractor_prompt, model_role="bulk")
            facts = result.get("parsed", [])
            if isinstance(facts, list):
                all_facts.extend(facts)
            total_cost += result.get("cost_usd", 0)
            print(f"  Chunk {i+1}/{len(chunks)}: {len(facts) if isinstance(facts, list) else 0} facts extracted")
        except Exception as e:
            print(f"  Chunk {i+1}/{len(chunks)}: ERROR - {e}")

    print(f"  Total facts extracted: {len(all_facts)}")
    print(f"  Extraction cost: ${total_cost:.4f}")

    # Step 5: Store facts
    print("\n[4/5] Storing facts in databases...")
    G = load_graph()
    stored_count = 0

    for fact_data in all_facts:
        if not isinstance(fact_data, dict) or "fact" not in fact_data:
            continue

        fact_id = insert_fact(
            content=fact_data["fact"],
            source_url=source_url or fact_data.get("citation", ""),
            source_title=source_title,
            source_date=fact_data.get("citation", ""),
            category=fact_data.get("category", "general"),
            location=fact_data.get("location", ""),
            confidence=fact_data.get("confidence", 0.8),
            source_id=source_id,
        )

        # Store in ChromaDB
        store_fact_embedding(fact_id, fact_data["fact"], {
            "category": fact_data.get("category", "general"),
            "location": fact_data.get("location", ""),
            "source": source_title,
        })

        # Add to knowledge graph
        add_fact_node(G, fact_id, fact_data["fact"])
        if fact_data.get("location"):
            loc_name = fact_data["location"]
            add_location(G, loc_name)
            from db.graph_ops import add_edge
            add_edge(G, f"fact:{fact_id}", f"loc:{loc_name.lower().replace(' ', '-')}", "ABOUT")
        if fact_data.get("category"):
            add_topic(G, fact_data["category"])
            from db.graph_ops import add_edge
            add_edge(G, f"fact:{fact_id}", f"topic:{fact_data['category']}", "CATEGORIZED_AS")

        stored_count += 1

    save_graph(G)
    print(f"  Stored {stored_count} facts")

    # Step 6: Plausibility check
    print("\n[5/5] Running plausibility checks...")
    plausibility_prompt = load_prompt("fact_plausibility")
    flagged = 0

    for fact_data in all_facts:
        if not isinstance(fact_data, dict) or not fact_data.get("has_number"):
            continue

        prompt = f"Assess this claim:\n\n\"{fact_data['fact']}\""
        try:
            result = call_llm_json(prompt, system=plausibility_prompt, model_role="bulk")
            check = result.get("parsed", {})
            if not check.get("plausible", True):
                add_to_verification_queue(
                    claim_text=fact_data["fact"],
                    issue_type="implausible",
                    suggested_correction=check.get("suggested_correction", "")
                )
                flagged += 1
                print(f"  FLAGGED: {fact_data['fact'][:60]}...")
                print(f"           Reason: {check.get('reason', 'Unknown')}")
        except Exception as e:
            print(f"  Plausibility check error: {e}")

    # Summary
    summary = {
        "file": os.path.basename(filepath),
        "chunks": len(chunks),
        "facts_extracted": len(all_facts),
        "facts_stored": stored_count,
        "facts_flagged": flagged,
        "cost_usd": total_cost,
    }

    print(f"\n{'='*60}")
    print(f"INGESTION COMPLETE")
    print(f"  Facts stored: {stored_count}")
    print(f"  Facts flagged for review: {flagged}")
    print(f"  Total cost: ${total_cost:.4f}")
    print(f"{'='*60}\n")

    return summary


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m ingest.pipeline <filepath> [source_url] [source_title]")
        sys.exit(1)
    filepath = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else ""
    title = sys.argv[3] if len(sys.argv) > 3 else ""
    ingest_research_doc(filepath, source_url=url, source_title=title)