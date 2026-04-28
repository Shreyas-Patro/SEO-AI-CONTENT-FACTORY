"""
Research Ingestion Pipeline.

Takes a research document (md/txt/pdf/docx) and turns it into:
- Facts in SQLite (with citations)
- Embeddings in ChromaDB
- Nodes/edges in the NetworkX graph
- Verification queue items for suspicious facts

USAGE (programmatic):
    from ingestion.pipeline import ingest_document
    result = ingest_document("/path/to/research.md", run_id="prun-...", topic="HSR Layout")

USAGE (dashboard): handled by dashboard_components/ingestion_view.py
"""

import os
import json
import time
import hashlib
from pathlib import Path
from typing import Optional

from ingestion.extract import extract_text
from ingestion.chunk import chunk_text

from db.sqlite_ops import (
    insert_fact, insert_source, add_to_verification_queue, db_conn, _now,
)
from db.chroma_ops import store_fact_embedding
from db.graph_ops import (
    load_graph, save_graph,
    add_location, add_topic, add_fact_node, link_article_cites_fact,
    add_edge, add_node,
)
from db.artifacts import save_artifact
from llm import call_llm_json


FACT_EXTRACTION_SYSTEM = """You are a fact extractor for a Bangalore real estate knowledge base.

Given a chunk of research text, extract individual factual claims. For each fact, output:
- statement: the fact, paraphrased to ~1 sentence
- entities: any locations, prices, dates, organizations mentioned
- category: property | legal | finance | lifestyle | infrastructure | demographic | other
- confidence: 0.0-1.0 based on how specific and verifiable the fact is
- citation_hint: if the chunk mentions a source (URL, report, year), capture it
- numeric_values: any numbers in the fact (with units)

Drop:
- Marketing language, opinions, vague claims
- Facts with no specifics (e.g. "the area is nice")
- Already-obvious or tautological statements

Output ONLY a JSON object with shape:
{
  "facts": [
    {
      "statement": "...",
      "entities": {"locations": [...], "amounts": [...], "orgs": [...]},
      "category": "property",
      "confidence": 0.85,
      "citation_hint": "optional source mention",
      "numeric_values": [{"value": 28000, "unit": "INR/month"}]
    }
  ]
}

If the chunk has no extractable facts, return {"facts": []}.
"""


PLAUSIBILITY_SYSTEM = """You are a fact plausibility checker for Bangalore real estate.

Given a fact, assess whether it's plausible. Flag suspicious facts:
- Numbers way off (e.g. rent of ₹500 in HSR Layout)
- Impossible dates
- Contradictions with general knowledge
- Vague or untestable claims

Output ONLY:
{
  "plausible": true|false,
  "confidence": 0.0-1.0,
  "issue": "description if not plausible, else null",
  "suggested_correction": "if applicable, else null"
}
"""


def _fact_already_exists(statement: str) -> bool:
    """Quick dedup based on hash of normalized statement."""
    norm = statement.lower().strip()
    h = hashlib.md5(norm.encode()).hexdigest()[:12]
    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id LIKE ?", (f"fact-{h}%",)
        ).fetchone()
    return row is not None


def ingest_document(
    filepath: str,
    run_id: Optional[str] = None,
    topic: str = "",
    source_url: str = "",
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    progress_cb=None,
) -> dict:
    """
    Full ingestion. Returns a summary dict.

    progress_cb: optional callable(stage_name, percent_done) for UI updates.
    """
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(filepath)

    def _progress(stage, pct):
        if progress_cb:
            try:
                progress_cb(stage, pct)
            except Exception:
                pass

    summary = {
        "filepath": str(p),
        "topic": topic,
        "started_at": _now(),
        "stages": {},
    }

    # ─── Stage 1: Extract ─────────────────────────────────────────────────
    _progress("extract", 0)
    text = extract_text(str(p))
    summary["stages"]["extract"] = {
        "char_count": len(text),
        "word_count": len(text.split()),
    }
    _progress("extract", 100)

    # Register the source
    source_id = insert_source(
        url=source_url or f"file://{p.absolute()}",
        title=p.name,
        source_type="research_doc",
    )

    # ─── Stage 2: Chunk ───────────────────────────────────────────────────
    _progress("chunk", 0)
    chunks = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    summary["stages"]["chunk"] = {"chunk_count": len(chunks)}
    _progress("chunk", 100)

    # ─── Stage 3: Fact extraction (one LLM call per chunk) ────────────────
    _progress("fact_extract", 0)
    G = load_graph()

    all_facts = []
    duplicates = 0
    extraction_cost = 0.0

    if topic:
        add_topic(G, topic)

    for i, chunk in enumerate(chunks):
        try:
            result = call_llm_json(
                f"Extract facts from this chunk:\n\n{chunk}",
                system=FACT_EXTRACTION_SYSTEM,
                model_role="bulk",
                max_tokens=2000,
            )
            extraction_cost += result.get("cost_usd", 0)
            parsed = result.get("parsed", {})
            facts = parsed.get("facts", [])

            for fact in facts:
                statement = fact.get("statement", "").strip()
                if not statement or len(statement) < 20:
                    continue
                if _fact_already_exists(statement):
                    duplicates += 1
                    continue

                category = fact.get("category", "general")
                entities = fact.get("entities", {})
                locations = entities.get("locations", [])
                location_str = locations[0] if locations else (topic if topic else "")

                fact_id = insert_fact(
                    content=statement,
                    source_id=source_id,
                    source_url=source_url,
                    source_title=p.name,
                    category=category,
                    location=location_str,
                    confidence=fact.get("confidence", 0.8),
                )

                # Embed in ChromaDB
                store_fact_embedding(
                    fact_id, statement,
                    metadata={
                        "category": category,
                        "location": location_str,
                        "source": p.name,
                    },
                )

                # Add to graph
                add_fact_node(G, fact_id, statement)
                if topic:
                    add_edge(G, f"fact:{fact_id}", f"topic:{topic.lower().replace(' ', '-')}",
                             "RELATES_TO_TOPIC")
                for loc in locations:
                    add_location(G, loc)
                    add_edge(G, f"fact:{fact_id}", f"loc:{loc.lower().replace(' ', '-')}",
                             "MENTIONS_LOCATION")

                all_facts.append({"id": fact_id, **fact})

        except Exception as e:
            print(f"  ⚠️  Chunk {i+1} fact extraction failed: {e}")
            continue

        _progress("fact_extract", int((i + 1) / len(chunks) * 100))

    save_graph(G)

    summary["stages"]["fact_extract"] = {
        "facts_extracted": len(all_facts),
        "duplicates_skipped": duplicates,
        "cost_usd": round(extraction_cost, 4),
    }

    # ─── Stage 4: Plausibility check (sample 20 facts max for cost) ───────
    _progress("plausibility", 0)
    sample = all_facts[:20]
    plausibility_cost = 0.0
    flagged = 0

    for j, fact in enumerate(sample):
        try:
            result = call_llm_json(
                f"Check this fact: {fact['statement']}",
                system=PLAUSIBILITY_SYSTEM,
                model_role="bulk",
                max_tokens=500,
            )
            plausibility_cost += result.get("cost_usd", 0)
            check = result.get("parsed", {})
            if not check.get("plausible", True):
                flagged += 1
                add_to_verification_queue(
                    fact_id=fact["id"],
                    claim_text=fact["statement"],
                    issue_type="implausibility",
                    suggested_correction=check.get("suggested_correction", ""),
                )
        except Exception as e:
            print(f"  ⚠️  Plausibility check failed for fact {j+1}: {e}")

        _progress("plausibility", int((j + 1) / len(sample) * 100) if sample else 100)

    summary["stages"]["plausibility"] = {
        "checked": len(sample),
        "flagged": flagged,
        "cost_usd": round(plausibility_cost, 4),
    }

    # ─── Done ─────────────────────────────────────────────────────────────
    summary["completed_at"] = _now()
    summary["total_facts"] = len(all_facts)
    summary["total_cost_usd"] = round(extraction_cost + plausibility_cost, 4)

    if run_id:
        save_artifact(run_id, "ingestion", "input",
                     {"filepath": str(p), "topic": topic, "source_url": source_url})
        save_artifact(run_id, "ingestion", "output", summary)
        save_artifact(run_id, "ingestion", "metadata", {
            "agent": "ingestion",
            "status": "completed",
            "llm_calls": len(chunks) + len(sample),
            "serp_calls": 0,
            "cost_usd": summary["total_cost_usd"],
        })

    return summary