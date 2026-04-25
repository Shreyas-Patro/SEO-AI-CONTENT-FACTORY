"""
LLM utility — wraps Anthropic API with retries, cost tracking, and caching.
All agent LLM calls go through this module.

KEY FIX in this version:
- Q8 in your brief: "Keyword Mapper not giving good outputs consistently"
- Root cause: cache key didn't include the topic, so HSR Layout cached results
  were being returned for Hosa Road queries.
- Fix: every call_llm_json now accepts cache_namespace which is hashed into
  the cache key. Agents pass topic + agent_name as namespace.
"""

import json
import re
import anthropic
import time
import hashlib

from config_loader import get_anthropic_key, get_model
from db.sqlite_ops import cache_get, cache_set

client = anthropic.Anthropic(api_key=get_anthropic_key())

PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}


def call_llm(prompt, system="", model_role="bulk", max_tokens=4096,
             temperature=0.3, use_cache=True, cache_ttl=7, prefill=None,
             cache_namespace=""):
    """
    Call Claude with automatic retries and cost tracking.

    Args:
        cache_namespace: Extra string mixed into the cache key. Pass topic name
                         + agent_name to prevent cross-topic cache poisoning.
    """
    model = get_model(model_role)

    if use_cache:
        cache_key = hashlib.md5(
            f"{model}:{system}:{prompt}:{prefill}:{cache_namespace}".encode()
        ).hexdigest()
        cached = cache_get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    max_retries = 3
    for attempt in range(max_retries):
        try:
            messages = [{"role": "user", "content": prompt}]

            if prefill:
                messages.append({"role": "assistant", "content": prefill})

            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "temperature": temperature,
            }
            if system:
                kwargs["system"] = system

            response = client.messages.create(**kwargs)

            response_text = response.content[0].text
            if prefill:
                response_text = prefill + response_text

            tokens_in  = response.usage.input_tokens
            tokens_out = response.usage.output_tokens

            pricing = PRICING.get(model, {"input": 3.0, "output": 15.0})
            cost = (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000

            result = {
                "text":       response_text,
                "tokens_in":  tokens_in,
                "tokens_out": tokens_out,
                "cost_usd":   round(cost, 6),
                "model":      model,
                "cached":     False,
            }

            if use_cache:
                cache_set(cache_key, result, ttl_days=cache_ttl)

            return result

        except anthropic.RateLimitError:
            wait = (2 ** attempt) * 5
            print(f"  Rate limited. Waiting {wait}s... (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)
        except anthropic.APIError as e:
            if attempt < max_retries - 1:
                wait = (2 ** attempt) * 2
                print(f"  API error: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

    raise Exception(f"Failed after {max_retries} retries")


def _strip_markdown_fences(text):
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _safe_json_parse(text):
    """Robust JSON parser. Never raises, returns dict or None."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = _strip_markdown_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[\s\S]*\}', cleaned)
    if not match:
        return None
    extracted = match.group(0)

    try:
        repaired = extracted.replace('\\n', ' ')
        repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)
        repaired = re.sub(r"([^\\])'([^'])'([^\\])", r'\1"\2"\3', repaired)
        repaired = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', repaired)
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    try:
        depth = 0
        start_idx = None
        for i, char in enumerate(extracted):
            if char == '{':
                if depth == 0:
                    start_idx = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start_idx is not None:
                    return json.loads(extracted[start_idx:i+1])
    except json.JSONDecodeError:
        pass

    return None


def call_llm_json(prompt, system="", model_role="bulk", max_tokens=4096, retries=2,
                  cache_namespace=""):
    """
    Call LLM and parse JSON response with automatic retry and fallback.

    Args:
        cache_namespace: Pass topic + agent_name to prevent cross-topic cache hits.
    """
    last_text = ""

    for attempt in range(retries):
        result = call_llm(
            prompt,
            system,
            model_role,
            max_tokens,
            temperature=0.1 if attempt == 0 else 0,
            use_cache=(attempt == 0),
            prefill="{",
            cache_namespace=cache_namespace,
        )

        last_text = _strip_markdown_fences(result["text"])
        parsed = _safe_json_parse(last_text)

        if parsed:
            result["parsed"] = parsed
            result["parse_success"] = True
            return result

        if attempt < retries - 1:
            print(f"⚠️  JSON parse failed on attempt {attempt + 1}/{retries}. Retrying at temperature 0...")

            repair_prompt = f"""Your previous response could not be parsed as JSON.

Return ONLY a valid JSON object.
Start your response with {{ and end with }}.
No markdown fences, no explanation, no preamble.

Original attempt:
{last_text[:500]}

Now return valid JSON ONLY:"""

            result = call_llm(
                repair_prompt,
                system="Return ONLY valid JSON. Start with { and end with }. Nothing else.",
                model_role="bulk",
                max_tokens=8000,    # bumped to 8000 for big keyword maps / competitor matrices
                temperature=0,
                use_cache=False,
                prefill="{",
                cache_namespace=cache_namespace,
            )

            last_text = _strip_markdown_fences(result["text"])
            parsed = _safe_json_parse(last_text)

            if parsed:
                result["parsed"] = parsed
                result["parse_success"] = True
                return result

    print("❌ JSON parsing failed after all retries. Returning fallback structure.")
    result["parsed"] = {
        "analysis_status": "FAILED_PARSE",
        "raw_llm_response": last_text[:500],
    }
    result["parse_success"] = False
    return result
