"""
Content Architect v3 — uses base class's _retry_attempt + _retry_problems.

Fixes from round 2:
- Retry now uses _retry_suffix() in cache_namespace so each retry calls
  the LLM fresh instead of returning cached broken output.
- Defensive against keyword_mapper failures: if keyword_groups is missing
  or empty, the agent generates a fallback set from the topic name rather
  than crashing.
- DB inserts now use the new auto-suffix slug logic in sqlite_ops.create_article,
  so duplicate slugs are renamed (mg-road-guide → mg-road-guide-2) instead of
  raising and cascading the 'database is locked' error.
"""

import json
from db.sqlite_ops import (
    create_cluster, update_cluster, create_article
)
from db.chroma_ops import search_facts
from db.graph_ops import load_graph, get_nodes_by_type
from llm import call_llm_json
from agents.base import AgentBase


def _slugify(text):
    """Make a URL-friendly slug from any string."""
    import re
    s = (text or "").lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s_-]+', '-', s).strip('-')
    return s[:80] or "article"


class ContentArchitectAgent(AgentBase):
    NAME = "content_architect"
    INPUT_REQUIRED = ["topic", "keyword_data"]
    OUTPUT_REQUIRED = ["cluster_id", "cluster_plan", "articles_created"]
    OUTPUT_NON_EMPTY = ["cluster_plan"]
    MAX_VALIDATION_RETRIES = 2

    def __init__(self, pipeline_run_id, cluster_id=None, article_id=None):
        super().__init__(pipeline_run_id, cluster_id, article_id)
        self._expected_min_articles = 0

    def validate_output(self, output):
        is_valid, problems = super().validate_output(output)

        cluster_plan = output.get("cluster_plan", {})
        articles = cluster_plan.get("articles", [])

        if len(articles) < 1:
            problems.append("cluster_plan.articles is empty")
            is_valid = False

        hubs = [a for a in articles if a.get("type") == "hub"]
        if len(hubs) == 0:
            problems.append("no hub article in plan")
            is_valid = False

        for i, a in enumerate(articles):
            if not a.get("title"):
                problems.append(f"articles[{i}] missing title")
                is_valid = False
            if not a.get("type"):
                problems.append(f"articles[{i}] missing type")
                is_valid = False

        # Coverage check
        if self._expected_min_articles and len(articles) < self._expected_min_articles:
            problems.append(
                f"only {len(articles)} articles but expected ≥{self._expected_min_articles} "
                f"based on keyword_groups count — likely truncated by token limit"
            )
            is_valid = False

        declared = cluster_plan.get("total_articles")
        if isinstance(declared, int) and declared != len(articles):
            problems.append(
                f"cluster_plan.total_articles={declared} but only {len(articles)} articles present "
                f"— output was truncated"
            )
            is_valid = False

        return is_valid, problems

    def _build_fallback_keyword_groups(self, topic):
        """If keyword_mapper failed and we have nothing, generate sensible defaults."""
        return [
            {"group_name": f"{topic} Overview", "suggested_article_type": "hub",
             "primary_keyword": topic, "secondary_keywords": [], "priority": "high"},
            {"group_name": f"{topic} Property Prices", "suggested_article_type": "spoke",
             "primary_keyword": f"{topic} property prices", "priority": "high"},
            {"group_name": f"{topic} Rental Market", "suggested_article_type": "spoke",
             "primary_keyword": f"rent in {topic}", "priority": "high"},
            {"group_name": f"{topic} Connectivity", "suggested_article_type": "spoke",
             "primary_keyword": f"{topic} connectivity", "priority": "medium"},
            {"group_name": f"{topic} Lifestyle", "suggested_article_type": "spoke",
             "primary_keyword": f"things to do in {topic}", "priority": "medium"},
            {"group_name": f"{topic} Schools & Hospitals", "suggested_article_type": "sub_spoke",
             "primary_keyword": f"schools and hospitals in {topic}", "priority": "low"},
            {"group_name": f"{topic} FAQ", "suggested_article_type": "faq",
             "primary_keyword": f"{topic} FAQ", "priority": "low"},
        ]

    def _build_prompt(self, topic, kw_map, retry_attempt, retry_problems, existing_titles, relevant_facts):
        prompt_template = open("prompts/content_architect.md").read()

        kw_groups = kw_map.get("keyword_groups", [])
        if not kw_groups:
            kw_groups = self._build_fallback_keyword_groups(topic)
            kw_map = dict(kw_map)
            kw_map["keyword_groups"] = kw_groups

        n_groups = len(kw_groups)
        expected_articles = max(n_groups, 8)

        retry_clause = ""
        if retry_attempt > 0 and retry_problems:
            retry_clause = (
                "\n\n⚠️ YOUR PREVIOUS RESPONSE WAS INCOMPLETE/INVALID. Problems:\n"
                + "\n".join(f"  - {p}" for p in retry_problems)
                + "\n\nFix every issue. Produce a COMPLETE plan. If output threatens to be too long, "
                "use shorter outlines per article rather than fewer articles.\n"
            )

        prompt = f"""Design a content cluster for "{topic}" in Bangalore.

KEYWORD STRATEGY ({n_groups} keyword groups):
{json.dumps(kw_map, indent=2)[:8000]}

RELEVANT FACTS:
{json.dumps(relevant_facts or [], indent=2)[:1500]}

EXISTING ARTICLES (avoid duplication):
{json.dumps(existing_titles or [], indent=2)[:800]}

REQUIREMENTS:
- The cluster MUST have at least {expected_articles} articles.
- 1-2 hub articles, 4-8 spoke articles, 2-4 sub-spoke articles, 1 FAQ article.
- For phase-specific topics, generate one spoke per phase.
- Every article needs: title, slug, type (hub|spoke|sub_spoke|faq), target_keywords (primary + secondary),
  word_count_target, outline (H2/H3 list), internal_links, faq_count, notes.
- "total_articles" MUST equal the actual count of items in the articles array.
- Slugs must be lowercase, hyphenated, URL-safe.
{retry_clause}

Return the full hub-spoke-sub_spoke-FAQ cluster plan as JSON.
"""
        return prompt, prompt_template

    def _execute(self, validated_input):
        topic = validated_input["topic"]
        keyword_data = validated_input["keyword_data"]
        kw_map = keyword_data.get("keyword_map", {}) or {}

        # Use fallback if keyword_mapper failed
        kw_groups = kw_map.get("keyword_groups", [])
        if not kw_groups:
            print(f"  ⚠️  No keyword_groups from keyword_mapper — using fallback for {topic}")
            kw_groups = self._build_fallback_keyword_groups(topic)
            kw_map = {**kw_map, "keyword_groups": kw_groups}

        self._expected_min_articles = max(int(len(kw_groups) * 0.6), 5)

        retry = self._retry_attempt
        print(f"\n[{self.NAME}] Designing cluster for: {topic}{' (retry ' + str(retry) + ')' if retry else ''}")
        print(f"  Expected min articles: {self._expected_min_articles} (from {len(kw_groups)} keyword groups)")

        # Get relevant facts and existing articles
        try:
            facts = search_facts(f"{topic} Bangalore", top_k=10)
            relevant_facts = [{"text": f.get("text", "")[:200]} for f in facts]
        except Exception:
            relevant_facts = []

        try:
            G = load_graph()
            existing_articles = get_nodes_by_type(G, "article")
            existing_titles = [a.get("label", "") for a in existing_articles[:15]]
        except Exception:
            existing_titles = []

        prompt, prompt_template = self._build_prompt(
            topic, kw_map, retry, self._retry_problems, existing_titles, relevant_facts
        )

        result = call_llm_json(
            prompt,
            system=prompt_template,
            model_role="architect",
            max_tokens=16000,
            cache_namespace=f"{topic}:content_architect{self._retry_suffix()}",
        )
        self._track_llm(result)

        cluster_plan = result.get("parsed", {})
        articles = cluster_plan.get("articles", [])

        # Create cluster + articles in DB (using auto-slug-suffix from sqlite_ops)
        cluster_id = self.cluster_id or create_cluster(topic, topic)
        self.cluster_id = cluster_id

        hub_ids, spoke_ids, faq_ids = [], [], []
        successful_inserts = 0
        failed_inserts = 0

        for art in articles:
            try:
                # Ensure slug exists and is sane
                requested_slug = art.get("slug") or _slugify(art.get("title", ""))

                article_id = create_article(
                    title=art.get("title", "Untitled"),
                    slug=requested_slug,    # auto-suffixed if exists
                    cluster_id=cluster_id,
                    article_type=art.get("type", "spoke"),
                    target_keywords=art.get("target_keywords", {}),
                    outline=art.get("outline", []),
                )
                art["db_id"] = article_id
                successful_inserts += 1
                if art.get("type") == "hub":
                    hub_ids.append(article_id)
                elif art.get("type") == "faq":
                    faq_ids.append(article_id)
                else:
                    spoke_ids.append(article_id)
            except Exception as e:
                # Should be rare now that slug is auto-suffixed and conn is closed properly
                failed_inserts += 1
                print(f"  ⚠️  Failed to insert article '{art.get('title','?')}': {type(e).__name__}: {e}")

        print(f"  Inserted {successful_inserts}/{len(articles)} articles ({failed_inserts} failed)")

        try:
            update_cluster(
                cluster_id,
                hub_article_ids=json.dumps(hub_ids),
                spoke_article_ids=json.dumps(spoke_ids),
                faq_article_ids=json.dumps(faq_ids),
                cluster_plan=json.dumps(cluster_plan),
                keyword_map=json.dumps(keyword_data),
            )
        except Exception as e:
            print(f"  ⚠️  update_cluster failed: {e}")

        return {
            "cluster_id": cluster_id,
            "cluster_plan": cluster_plan,
            "articles_created": successful_inserts,
            "articles_failed": failed_inserts,
        }

    def _output_summary(self, output):
        plan = output.get("cluster_plan", {})
        articles = plan.get("articles", [])
        return f"cluster_id={output.get('cluster_id','?')}, {len(articles)} articles, {output.get('articles_created',0)} inserted"


def run_content_architect(seed_topic, keyword_data, cluster_id=None, pipeline_run_id=None):
    from db.artifacts import create_pipeline_run
    if pipeline_run_id is None:
        pipeline_run_id = create_pipeline_run(seed_topic, notes="standalone content_architect run")

    agent = ContentArchitectAgent(pipeline_run_id, cluster_id=cluster_id)
    output = agent.run({"topic": seed_topic, "keyword_data": keyword_data})
    output["cost_usd"] = agent.cost_usd
    output["pipeline_run_id"] = pipeline_run_id
    return output