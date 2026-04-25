"""
FAQ Architect v2 — wrapped in AgentBase.

Generates AEO-optimized FAQs per article.
"""

import json
from db.sqlite_ops import get_article, update_article
from llm import call_llm_json
from agents.base import AgentBase


class FAQArchitectAgent(AgentBase):
    NAME = "faq_architect"
    INPUT_REQUIRED = ["article_id", "keyword_data"]
    OUTPUT_REQUIRED = ["article_id", "faqs"]
    OUTPUT_NON_EMPTY = ["faqs"]

    def validate_output(self, output):
        is_valid, problems = super().validate_output(output)
        faqs = output.get("faqs", [])
        if not isinstance(faqs, list):
            problems.append("faqs is not a list")
            return False, problems
        for i, f in enumerate(faqs):
            if not isinstance(f, dict):
                problems.append(f"faqs[{i}] is not a dict")
                is_valid = False
                continue
            if not f.get("question"):
                problems.append(f"faqs[{i}] missing question")
                is_valid = False
            if not f.get("answer"):
                problems.append(f"faqs[{i}] missing answer")
                is_valid = False
        return is_valid, problems

    def _execute(self, validated_input):
        article_id = validated_input["article_id"]
        keyword_data = validated_input["keyword_data"]

        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        print(f"\n[{self.NAME}] Generating FAQs for: {article['title']}")

        prompt_template = open("prompts/faq_architect.md").read()

        outline = json.loads(article.get("outline", "[]") or "[]")
        target_keywords = json.loads(article.get("target_keywords", "{}") or "{}")
        kw_groups = keyword_data.get("keyword_map", {}).get("keyword_groups", [])

        prompt = f"""Generate FAQs for this article:

ARTICLE TITLE: {article['title']}
ARTICLE TYPE: {article['article_type']}
ARTICLE OUTLINE:
{json.dumps(outline, indent=2)}

TARGET KEYWORDS:
{json.dumps(target_keywords, indent=2)}

AVAILABLE KEYWORD DATA (top 5 groups):
{json.dumps(kw_groups[:5], indent=2)[:3000]}

Generate 5-10 FAQs optimized for featured snippets and voice search.
"""
        result = call_llm_json(
            prompt,
            system=prompt_template,
            model_role="bulk",
            max_tokens=4096,
            cache_namespace=f"{article_id}:faq_architect",
        )
        self._track_llm(result)

        faq_data = result.get("parsed", {})
        faqs = faq_data.get("faqs", [])

        # Persist on the article
        update_article(article_id, faq_json=json.dumps(faqs))

        return {
            "article_id": article_id,
            "faqs": faqs,
        }

    def _output_summary(self, output):
        return f"{len(output.get('faqs', []))} FAQs for {output.get('article_id','?')}"


# ─── Backwards-compatible wrapper ─────────────────────────────────────────
def run_faq_architect(article_id, keyword_data, cluster_id=None, pipeline_run_id=None):
    from db.artifacts import create_pipeline_run
    if pipeline_run_id is None:
        pipeline_run_id = create_pipeline_run("faq_architect_solo", notes="standalone faq_architect run")

    agent = FAQArchitectAgent(pipeline_run_id, cluster_id=cluster_id, article_id=article_id)
    output = agent.run({"article_id": article_id, "keyword_data": keyword_data})
    output["cost_usd"] = agent.cost_usd
    output["pipeline_run_id"] = pipeline_run_id
    return output