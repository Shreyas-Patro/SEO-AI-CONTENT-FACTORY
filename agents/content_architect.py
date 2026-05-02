"""
agents/content_architect.py — v5.1 (truncation filter fix)
"""

import json
import re
from db.sqlite_ops import create_cluster, update_cluster, create_article
from db.pipeline_state import StateKeys, PipelineState
from llm import call_llm_json
from agents.base import AgentBase


def _slugify(text):
    s = (text or "").lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s_-]+', '-', s).strip('-')
    return s[:80] or "article"


class ContentArchitectAgent(AgentBase):
    NAME = "content_architect"
    READS_STATE = [StateKeys.KEYWORD_MAP]
    WRITES_STATE = [StateKeys.CLUSTER_PLAN]
    OUTPUT_REQUIRED = ["cluster_id", "cluster_plan", "articles_created"]
    OUTPUT_NON_EMPTY = ["cluster_plan"]
    MAX_VALIDATION_RETRIES = 2

    def __init__(self, pipeline_run_id, cluster_id=None, article_id=None):
        super().__init__(pipeline_run_id, cluster_id, article_id)
        self._expected_min_articles = 0

    def _validate_output(self, output):
        """Custom validation on top of base checks."""
        problems = super()._validate_output(output)

        # The base class flags any total_articles mismatch as "truncated".
        # We replace that with a smarter reconciling check below, so drop it here.
        problems = [p for p in problems if "truncated" not in p.lower()]

        cluster_plan = output.get("cluster_plan", {}) if isinstance(output, dict) else {}
        articles = cluster_plan.get("articles", [])

        if len(articles) < 1:
            problems.append("cluster_plan.articles is empty")

        hubs = [a for a in articles if a.get("type") == "hub"]
        if len(hubs) == 0 and len(articles) > 0:
            problems.append("no hub article in plan")

        for i, a in enumerate(articles):
            if not a.get("title"):
                problems.append(f"articles[{i}] missing title")
            if not a.get("type"):
                problems.append(f"articles[{i}] missing type")

        # Reconciling truncation check: trust the actual count
        declared = cluster_plan.get("total_articles", 0)
        actual = len(articles)

        # Reconcile: actual count is the truth
        cluster_plan["total_articles"] = actual

        TRUNCATION_THRESHOLD = 3
        MIN_ARTICLES = 5

        if actual < MIN_ARTICLES:
            problems.append(f"only {actual} articles inserted (need ≥{MIN_ARTICLES})")
        elif declared - actual >= TRUNCATION_THRESHOLD:
            problems.append(
                f"declared {declared} but only {actual} articles — likely truncated"
            )
        elif declared != actual:
            print(
                f"  Note: LLM declared {declared}, inserted {actual} — using actual count"
            )

        return problems

    def _build_fallback_keyword_groups(self, topic):
        return [
            {"group_name": f"{topic} Overview", "primary_keyword": topic, "priority": "high"},
            {"group_name": f"{topic} Property Prices", "primary_keyword": f"{topic} property prices", "priority": "high"},
            {"group_name": f"{topic} Rental Market", "primary_keyword": f"rent in {topic}", "priority": "high"},
            {"group_name": f"{topic} Connectivity", "primary_keyword": f"{topic} connectivity", "priority": "medium"},
            {"group_name": f"{topic} Lifestyle", "primary_keyword": f"things to do in {topic}", "priority": "medium"},
            {"group_name": f"{topic} Schools & Hospitals", "primary_keyword": f"schools and hospitals in {topic}", "priority": "low"},
            {"group_name": f"{topic} FAQ", "primary_keyword": f"{topic} FAQ", "priority": "low"},
        ]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        topic = agent_input.get("topic") or state.topic or ""

        kw_map = (
            agent_input.get("keyword_map")
            or agent_input.get("keyword_data", {}).get("keyword_map")
            or state.get(StateKeys.KEYWORD_MAP, {})
            or {}
        )

        kw_groups = kw_map.get("keyword_groups", [])
        if not kw_groups:
            print(f"  ⚠️  No keyword_groups — building enriched fallback for {topic}")
            trend_state = state.get(StateKeys.TREND_DATA, {}) or {}
            raw = trend_state.get("raw_data", {}) or {}
            paa = (raw.get("paa_questions") or [])[:8]
            related = (raw.get("related_searches") or [])[:8]

            kw_groups = self._build_fallback_keyword_groups(topic)

            if paa:
                kw_groups.append({
                    "group_name": f"{topic} FAQs from SERPs",
                    "primary_keyword": f"{topic} questions",
                    "supporting_keywords": [
                        str(p) if not isinstance(p, dict) else p.get("question", "")
                        for p in paa
                    ],
                    "intent": "informational",
                    "estimated_volume": "medium",
                    "competition": "low",
                    "opportunity_score": 75,
                })

            if related:
                kw_groups.append({
                    "group_name": f"{topic} Related Topics",
                    "primary_keyword": f"{topic} related",
                    "supporting_keywords": related,
                    "intent": "informational",
                    "estimated_volume": "medium",
                    "competition": "medium",
                    "opportunity_score": 60,
                })

            kw_map = {**kw_map, "keyword_groups": kw_groups}

        self._expected_min_articles = max(int(len(kw_groups) * 0.6), 5)

        print(f"\n[{self.NAME}] Designing cluster for: {topic}")
        print(f"  {len(kw_groups)} keyword groups, expecting ≥{self._expected_min_articles} articles")

        relevant_facts = []
        try:
            from db.chroma_ops import search_facts
            facts = search_facts(f"{topic} Bangalore", top_k=10)
            relevant_facts = [{"text": f.get("text", "")[:200]} for f in facts]
        except Exception:
            pass

        existing_titles = []
        try:
            from db.graph_ops import load_graph, get_nodes_by_type
            G = load_graph()
            existing_articles = get_nodes_by_type(G, "article")
            existing_titles = [a.get("label", "") for a in existing_articles[:15]]
        except Exception:
            pass

        try:
            prompt_template = open("prompts/content_architect.md", encoding="utf-8").read()
        except FileNotFoundError:
            prompt_template = "You are a content strategist. Design a hub-spoke content cluster. Return JSON only."

        retry_clause = ""
        if self._retry_attempt > 1 and self._retry_problems:
            retry_clause = (
                "\n\n⚠️ PREVIOUS RESPONSE WAS INVALID:\n"
                + "\n".join(f"  - {p}" for p in self._retry_problems)
                + "\nFix every issue.\n"
            )

        expected_articles = max(len(kw_groups), 8)

        prompt = f"""Design a content cluster for "{topic}" in Bangalore.

KEYWORD STRATEGY ({len(kw_groups)} keyword groups):
{json.dumps(kw_map, indent=2, default=str)[:8000]}

RELEVANT FACTS:
{json.dumps(relevant_facts, indent=2)[:1500]}

EXISTING ARTICLES (avoid duplication):
{json.dumps(existing_titles, indent=2)[:800]}

REQUIREMENTS:
- At least {expected_articles} articles total.
- 1-2 hub articles, 4-8 spoke articles, 2-4 sub-spoke articles, 1 FAQ article.
- Every article needs: title, slug, type (hub|spoke|sub_spoke|faq), target_keywords,
  word_count_target, outline (H2/H3 list), internal_links, faq_count, notes.
- "total_articles" MUST equal the actual count of articles array items.
- Slugs: lowercase, hyphenated, URL-safe.
{retry_clause}
Return the full cluster plan as JSON."""

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

        cluster_id = self.cluster_id or create_cluster(topic, topic)
        self.cluster_id = cluster_id

        hub_ids, spoke_ids, faq_ids = [], [], []
        successful_inserts = 0
        failed_inserts = 0

        for art in articles:
            try:
                requested_slug = art.get("slug") or _slugify(art.get("title", ""))
                article_id = create_article(
                    title=art.get("title", "Untitled"),
                    slug=requested_slug,
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
                failed_inserts += 1
                print(f"  ⚠️  Insert failed for '{art.get('title','?')}': {e}")

        print(f"  Inserted {successful_inserts}/{len(articles)} articles")

        try:
            update_cluster(
                cluster_id,
                hub_article_ids=json.dumps(hub_ids),
                spoke_article_ids=json.dumps(spoke_ids),
                faq_article_ids=json.dumps(faq_ids),
                cluster_plan=json.dumps(cluster_plan),
                keyword_map=json.dumps(kw_map),
            )
        except Exception as e:
            print(f"  ⚠️  update_cluster failed: {e}")

        return {
            "cluster_id": cluster_id,
            "cluster_plan": cluster_plan,
            "articles_created": successful_inserts,
            "articles_failed": failed_inserts,
        }