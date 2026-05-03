"""
Export system — generate downloadable files from the database.
"""

import os
import json
import zipfile
from db.sqlite_ops import get_article, get_articles_by_cluster, get_cluster, list_clusters
import re

FAQ_HEADING_RE = re.compile(r"^#{2,3}\s+(?:Frequently Asked Questions|FAQs?)\s*$",
                            re.IGNORECASE | re.MULTILINE)

def _split_body_and_faqs(content_md: str):
    """
    Returns (body_without_faq, faq_section_md, faq_section_md_or_empty).
    Looks for a '## Frequently Asked Questions' (or similar) heading
    and chops everything from that heading onward.
    """
    if not content_md:
        return "", ""
    m = FAQ_HEADING_RE.search(content_md)
    if not m:
        return content_md, ""
    body = content_md[: m.start()].rstrip()
    faq  = content_md[m.start():].strip()
    return body, faq


def export_article_md(article_id, output_dir="outputs"):
    """Export article as TWO files: <slug>.md (no FAQs) and <slug>.faqs.md."""
    article = get_article(article_id)
    if not article:
        raise ValueError(f"Article {article_id} not found")
    os.makedirs(output_dir, exist_ok=True)

    body, faq_block = _split_body_and_faqs(article.get("content_md", ""))
    faqs_struct = json.loads(article.get("faq_json", "[]") or "[]")

    meta = {
        "title": article["title"],
        "slug": article["slug"],
        "type": article["article_type"],
        "meta_title": article.get("meta_title", ""),
        "meta_description": article.get("meta_description", ""),
        "keywords": json.loads(article.get("target_keywords", "[]")),
        "word_count": article.get("word_count", 0),
        "readability_score": article.get("readability_score"),
        "brand_tone_score": article.get("brand_tone_score"),
    }

    body_path = os.path.join(output_dir, f"{article['slug']}.md")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(f"---\n{json.dumps(meta, indent=2)}\n---\n\n{body}\n")

    faq_path = None
    if faq_block or faqs_struct:
        faq_meta = {
            "parent_slug": article["slug"],
            "parent_title": article["title"],
            "faq_count": len(faqs_struct),
        }
        faq_md_lines = [f"---\n{json.dumps(faq_meta, indent=2)}\n---\n"]
        if faq_block:
            faq_md_lines.append(faq_block)
        elif faqs_struct:
            faq_md_lines.append("# Frequently Asked Questions\n")
            for q in faqs_struct:
                faq_md_lines.append(f"\n## {q.get('question','?')}\n\n{q.get('answer','')}\n")
        faq_path = os.path.join(output_dir, f"{article['slug']}.faqs.md")
        with open(faq_path, "w", encoding="utf-8") as f:
            f.write("\n".join(faq_md_lines))

    return {"body": body_path, "faqs": faq_path}

def export_cluster_zip(cluster_id, output_dir="outputs"):
    cluster = get_cluster(cluster_id)
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    articles = get_articles_by_cluster(cluster_id)
    cluster_name = cluster["name"].lower().replace(" ", "-")
    zip_filename = f"{cluster_name}-cluster.zip"
    zip_path = os.path.join(output_dir, zip_filename)
    os.makedirs(output_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:

        for article in articles:
            type_folder = {
                "hub": "01-hub",
                "spoke": "02-spokes",
                "sub_spoke": "03-sub-spokes",
                "faq": "04-faq",
            }.get(article["article_type"], "05-other")

            body, faq_block = _split_body_and_faqs(article.get("content_md", ""))

            meta = {
                "title": article["title"],
                "slug": article["slug"],
                "meta_title": article.get("meta_title", ""),
                "meta_description": article.get("meta_description", ""),
            }

            body_doc = f"---\n{json.dumps(meta, indent=2)}\n---\n\n{body}\n"

            # Main article
            zf.writestr(
                f"{cluster_name}/{type_folder}/{article['slug']}.md",
                body_doc,
            )

            # FAQs
            if faq_block:
                zf.writestr(
                    f"{cluster_name}/faqs/{article['slug']}.faqs.md",
                    faq_block,
                )

            # Schema
            if article.get("schema_json") and article["schema_json"] != "{}":
                zf.writestr(
                    f"{cluster_name}/meta/{article['slug']}-schema.json",
                    article["schema_json"],
                )

        # ✅ OUTSIDE loop (correct place)
        summary = {
            "cluster_name": cluster["name"],
            "total_articles": len(articles),
            "articles": [
                {
                    "title": a["title"],
                    "slug": a["slug"],
                    "type": a["article_type"],
                    "word_count": a.get("word_count", 0),
                }
                for a in articles
            ],
        }

        zf.writestr(
            f"{cluster_name}/cluster-summary.json",
            json.dumps(summary, indent=2),
        )

    return zip_path
