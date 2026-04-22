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
             temperature=0.3, use_cache=True, cache_ttl=7):
    """
    Call Claude with automatic retries and cost tracking.

    Args:
        prompt: The user message
        system: System prompt
        model_role: 'writer', 'architect', or 'bulk'
        max_tokens: Max output tokens
        temperature: 0.0-1.0
        use_cache: Whether to check/store in cache
        cache_ttl: Days to keep cached responses

    Returns:
        dict with keys: text, tokens_in, tokens_out, cost_usd, model, cached
    """
    model = get_model(model_role)

    # Check cache
    if use_cache:
        cache_key = hashlib.md5(f"{model}:{system}:{prompt}".encode()).hexdigest()
        cached = cache_get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    # Call API with retries
    max_retries = 3
    for attempt in range(max_retries):
        try:
            messages = [{"role": "user", "content": prompt}]
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "temperature": temperature,
            }
            if system:
                kwargs["system"] = system

            response = client.messages.create(**kwargs)

            # Extract response
            response_text = response.content[0].text
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens

            # Calculate cost
            pricing = PRICING.get(model, {"input": 3.0, "output": 15.0})
            cost = (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000

            result = {
                "text": response_text,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": round(cost, 6),
                "model": model,
                "cached": False,
            }

            # Store in cache
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


def _safe_json_parse(text):
    """
    Robust JSON parser with multi-step repair logic.
    Handles malformed JSON gracefully.
    """
    # Step 1: Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Step 2: Strip markdown code fences
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

    # Step 3: Extract JSON block if embedded in text
    match = re.search(r'\{[\s\S]*\}', cleaned)
    if not match:
        return None

    extracted = match.group(0)

    # Step 4: Apply repair fixes
    try:
        # Remove literal \n (common LLM error)
        repaired = extracted.replace('\\n', ' ')

        # Fix trailing commas (very common)
        repaired = re.sub(r',(\s*[}\]])', r'\1', repaired)

        # Fix single quotes to double quotes
        repaired = re.sub(r"([^\\])'([^'])'([^\\])", r'\1"\2"\3', repaired)

        # Fix missing quotes around keys
        repaired = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', repaired)

        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Step 5: Final attempt — try to extract just the first valid JSON object
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
                    potential_json = extracted[start_idx:i+1]
                    return json.loads(potential_json)
    except json.JSONDecodeError:
        pass

    # Parsing failed completely
    return None


def call_llm_json(prompt, system="", model_role="bulk", max_tokens=4096, retries=2):
    """
    Call LLM and parse JSON with automatic retry and fallback.

    Flow:
    1. Call LLM at temperature 0.1
    2. Try to parse JSON
    3. If fails, retry at temperature 0 with stricter repair prompt
    4. If still fails, return fallback structure
    """
    last_text = ""

    for attempt in range(retries):
        result = call_llm(
            prompt,
            system,
            model_role,
            max_tokens,
            temperature=0.1 if attempt == 0 else 0,  # temp=0 on retry = much cleaner JSON
            use_cache=(attempt == 0)                   # never cache repair attempts
        )

        last_text = result["text"].strip()

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
                max_tokens=2048,
                temperature=0,
                use_cache=False
            )

            last_text = result["text"].strip()
            parsed = _safe_json_parse(last_text)

            if parsed:
                result["parsed"] = parsed
                result["parse_success"] = True
                return result

    # Final fallback — never crash the agent
    print("❌ JSON parsing failed after all retries. Returning fallback structure.")

    result["parsed"] = {
        "topic": "unknown",
        "trend_direction": "unknown",
        "trend_summary": "LLM analysis parsing failed. Manual review required.",
        "intent_clusters": [],
        "content_gaps": [],
        "top_5_priority_queries": [],
        "analysis_status": "FAILED_PARSE",
        "raw_llm_response": last_text[:500]
    }
    result["parse_success"] = False

    return result


if __name__ == "__main__":
    # Quick test
    print("Testing LLM connection...")
    result = call_llm("Say 'Canvas Homes AI Engine is online.' and nothing else.", model_role="bulk")
    print(f"Response: {result['text']}")
    print(f"Tokens: {result['tokens_in']} in, {result['tokens_out']} out")
    print(f"Cost: ${result['cost_usd']}")
    print(f"Model: {result['model']}")
    print(f"LLM output length: {len(result['text'])}")  # Fixed: use result['text'] not bare `text`