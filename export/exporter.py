"""
Export system — generate downloadable files from the database.
"""

import os
import json
import zipfile
from db.sqlite_ops import get_article, get_articles_by_cluster, get_cluster, list_clusters


def export_article_md(article_id, output_dir="outputs"):
    """Export a single article as Markdown file."""
    article = get_article(article_id)
    if not article:
        raise ValueError(f"Article {article_id} not found")

    os.makedirs(output_dir, exist_ok=True)
    filename = f"{article['slug']}.md"
    filepath = os.path.join(output_dir, filename)

    # Build frontmatter
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

    content = f"""---
{json.dumps(meta, indent=2)}
---

{article.get('content_md', '')}
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def export_cluster_zip(cluster_id, output_dir="outputs"):
    """Export an entire cluster as a ZIP with folder structure."""
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
            # Determine subfolder by type
            type_folder = {
                "hub": "01-hub",
                "spoke": "02-spokes",
                "sub_spoke": "03-sub-spokes",
                "faq": "04-faq",
            }.get(article["article_type"], "05-other")

            filename = f"{article['slug']}.md"
            arcpath = f"{cluster_name}/{type_folder}/{filename}"

            meta = {
                "title": article["title"],
                "slug": article["slug"],
                "meta_title": article.get("meta_title", ""),
                "meta_description": article.get("meta_description", ""),
            }

            content = f"---\n{json.dumps(meta, indent=2)}\n---\n\n{article.get('content_md', '')}"
            zf.writestr(arcpath, content)

            # Add schema JSON
            if article.get("schema_json") and article["schema_json"] != "{}":
                schema_path = f"{cluster_name}/meta/{article['slug']}-schema.json"
                zf.writestr(schema_path, article["schema_json"])

        # Add cluster summary
        summary = {
            "cluster_name": cluster["name"],
            "total_articles": len(articles),
            "articles": [{"title": a["title"], "slug": a["slug"], "type": a["article_type"],
                          "word_count": a.get("word_count", 0)} for a in articles]
        }
        zf.writestr(f"{cluster_name}/cluster-summary.json", json.dumps(summary, indent=2))

    return zip_path


def export_bulk_zip(cluster_ids=None, output_dir="outputs"):
    """Export multiple clusters as a single ZIP."""
    if not cluster_ids:
        clusters = list_clusters()
        cluster_ids = [c["id"] for c in clusters]

    zip_path = os.path.join(output_dir, "canvas-homes-content-bulk.zip")
    os.makedirs(output_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for cid in cluster_ids:
            cluster = get_cluster(cid)
            if not cluster:
                continue
            articles = get_articles_by_cluster(cid)
            cluster_name = cluster["name"].lower().replace(" ", "-")

            for article in articles:
                type_folder = {
                    "hub": "01-hub", "spoke": "02-spokes",
                    "sub_spoke": "03-sub-spokes", "faq": "04-faq"
                }.get(article["article_type"], "05-other")

                content = f"---\ntitle: {article['title']}\nslug: {article['slug']}\n---\n\n{article.get('content_md', '')}"
                arcpath = f"{cluster_name}/{type_folder}/{article['slug']}.md"
                zf.writestr(arcpath, content)

    return zip_path

