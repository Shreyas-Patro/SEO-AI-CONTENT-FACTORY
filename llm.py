"""
LLM utility — wraps Anthropic API with retries, cost tracking, and caching.
All agent LLM calls go through this module.
"""

import anthropic
import time
import hashlib
import json
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
            text = response.content[0].text
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens

            # Calculate cost
            pricing = PRICING.get(model, {"input": 3.0, "output": 15.0})
            cost = (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000

            result = {
                "text": text,
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


def call_llm_json(prompt, system="", model_role="bulk", max_tokens=4096):
    """Call LLM and parse response as JSON. Strips markdown fences if present."""
    result = call_llm(prompt, system, model_role, max_tokens, temperature=0.1, use_cache=True)
    text = result["text"].strip()

    # Strip markdown JSON fences
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        # Try to find JSON in the response
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end+1])
            except:
                raise ValueError(f"Could not parse LLM response as JSON: {e}\nResponse: {text[:500]}")
        else:
            raise ValueError(f"No JSON found in LLM response: {text[:500]}")

    result["parsed"] = parsed
    return result


if __name__ == "__main__":
    # Quick test
    print("Testing LLM connection...")
    result = call_llm("Say 'Canvas Homes AI Engine is online.' and nothing else.", model_role="bulk")
    print(f"Response: {result['text']}")
    print(f"Tokens: {result['tokens_in']} in, {result['tokens_out']} out")
    print(f"Cost: ${result['cost_usd']}")
    print(f"Model: {result['model']}")
    