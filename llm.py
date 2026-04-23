"""
LLM utility — wraps Anthropic API with retries, cost tracking, and caching.
All agent LLM calls go through this module.
"""

import json
import re
import anthropic
import time
import hashlib

from config_loader import get_anthropic_key, get_model
from db.sqlite_ops import cache_get, cache_set

client = anthropic.Anthropic(api_key=get_anthropic_key())

# Pricing per 1M tokens (as of April 2026 — update if prices change)
PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}


def call_llm(prompt, system="", model_role="bulk", max_tokens=4096,
             temperature=0.3, use_cache=True, cache_ttl=7, prefill=None):
    """
    Call Claude with automatic retries and cost tracking.

    Args:
        prompt:      The user message
        system:      System prompt
        model_role:  'writer', 'architect', or 'bulk'
        max_tokens:  Max output tokens
        temperature: 0.0-1.0
        use_cache:   Whether to check/store in cache
        cache_ttl:   Days to keep cached responses
        prefill:     String to force the model to start its reply with.
                     Injected as the first assistant turn — the model MUST
                     continue from it, making ```json fences physically
                     impossible. Pass prefill="{" for all JSON calls.

    Returns:
        dict with keys: text, tokens_in, tokens_out, cost_usd, model, cached
    """
    model = get_model(model_role)

    # Check cache — include prefill in key so cached results don't collide
    if use_cache:
        cache_key = hashlib.md5(f"{model}:{system}:{prompt}:{prefill}".encode()).hexdigest()
        cached = cache_get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    max_retries = 3
    for attempt in range(max_retries):
        try:
            messages = [{"role": "user", "content": prompt}]

            # FIX 1: inject prefill as an assistant turn.
            # Without this, Claude wraps JSON in ```json fences regardless of
            # what the system prompt says, causing every JSON call to fail on
            # attempt 1 and burn a second API call on the repair retry.
            # With prefill="{" the model is forced to open with { and cannot
            # add fences — the ⚠️ JSON parse failed warning disappears entirely.
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

            # When prefill was used the model returns only the CONTINUATION
            # (everything after the prefill string), so prepend it back to
            # reconstruct the full output before parsing or storing.
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
    """
    Strip markdown code fences from LLM output before JSON parsing.
    Safety net for cached responses stored before the prefill fix was applied,
    or any edge case where fences still appear.
    """
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _safe_json_parse(text):
    """
    Robust JSON parser with multi-step repair logic.
    Handles malformed JSON gracefully — never raises, always returns dict or None.
    """
    # Step 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Step 2: strip fences (safety net)
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Step 3: extract embedded JSON block
    match = re.search(r'\{[\s\S]*\}', cleaned)
    if not match:
        return None
    extracted = match.group(0)

    # Step 4: repair common LLM mistakes
    try:
        repaired = extracted.replace('\\n', ' ')
        repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)        # trailing commas
        repaired = re.sub(r"([^\\])'([^'])'([^\\])", r'\1"\2"\3', repaired)  # single quotes
        repaired = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', repaired)  # unquoted keys
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Step 5: brace-matching extraction
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


def call_llm_json(prompt, system="", model_role="bulk", max_tokens=4096, retries=2):
    """
    Call LLM and parse JSON response with automatic retry and fallback.

    Flow:
    1. Call LLM with prefill="{" — model is forced to open with brace, no fences.
    2. Strip any residual fences (safety net for cached responses).
    3. Parse JSON — should succeed on attempt 1 every time now.
    4. If parse still fails (genuine bad JSON), retry at temperature 0.
       FIX 2: repair call now uses max_tokens=4096 (was 2048).
       The old 2048 limit caused the LLM to truncate large JSON responses
       (keyword maps, full competitor analyses) mid-object, producing
       invalid JSON that could never be parsed — leaving AEO targets,
       content gaps, top 5 queries, keyword groups all blank in the output.
    5. If still fails, return safe fallback — never crash an agent.
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
            prefill="{",    # FIX 1: force model to open with { — no fences possible
        )

        last_text = _strip_markdown_fences(result["text"])
        parsed    = _safe_json_parse(last_text)

        if parsed:
            result["parsed"]        = parsed
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
                max_tokens=4096,    # FIX 2: was 2048 — too small for keyword maps and
                                    # full competitor analyses, causing mid-object truncation
                                    # which made AEO targets / content gaps / keyword groups blank
                temperature=0,
                use_cache=False,
                prefill="{",        # prefill on repair call too
            )

            last_text = _strip_markdown_fences(result["text"])
            parsed    = _safe_json_parse(last_text)

            if parsed:
                result["parsed"]        = parsed
                result["parse_success"] = True
                return result

    # Final fallback — never crash the agent
    print("❌ JSON parsing failed after all retries. Returning fallback structure.")
    result["parsed"] = {
        "topic":                  "unknown",
        "trend_direction":        "unknown",
        "trend_summary":          "LLM analysis parsing failed. Manual review required.",
        "intent_clusters":        [],
        "content_gaps":           [],
        "top_5_priority_queries": [],
        "analysis_status":        "FAILED_PARSE",
        "raw_llm_response":       last_text[:500],
    }
    result["parse_success"] = False
    return result


if __name__ == "__main__":
    print("Testing LLM connection...")
    result = call_llm("Say 'Canvas Homes AI Engine is online.' and nothing else.", model_role="bulk")
    print(f"Response: {result['text']}")
    print(f"Tokens: {result['tokens_in']} in, {result['tokens_out']} out")
    print(f"Cost: ${result['cost_usd']}")
    print(f"Model: {result['model']}")
    print(f"LLM output length: {len(result['text'])}")