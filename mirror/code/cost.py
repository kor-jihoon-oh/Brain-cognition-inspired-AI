"""Estimate request costs from reported usage and the bundled pricing snapshot."""

import math

try:
    from .serialization import UNDEFINED, js_truthy
except ImportError:
    from serialization import UNDEFINED, js_truthy


def _get(value, key):
    return value.get(key, UNDEFINED) if isinstance(value, dict) else UNDEFINED


def _nullish(value, fallback):
    return fallback if value is None or value is UNDEFINED else value


def priceUsage(response, pricing):
    u = _get(response, "usage")
    d = _get(u, "input_tokens_details")

    def unknown(why):
        return {"estimated_cost_usd": None, "cost_status": why}

    def n(value):
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0
            and value == math.trunc(value)
        )

    if not js_truthy(u) or not n(_get(u, "input_tokens")) or not n(_get(u, "output_tokens")):
        return unknown("usage_not_reported")
    model = _get(response, "model")
    if js_truthy(model) and model != pricing["model"] and not model.startswith(pricing["model"] + "-"):
        return unknown("unexpected_model")
    if _get(response, "service_tier") not in ("default", "standard", UNDEFINED, None):
        return unknown("unexpected_service_tier")
    if _get(u, "inference_geo") not in (UNDEFINED, None, "global"):
        return unknown("non_global_pricing")
    if u["input_tokens"] > pricing["max_verified_input_tokens"]:
        return unknown("long_context_price_not_configured")
    if not js_truthy(d) or not n(_get(d, "cached_tokens")) or not n(_get(d, "cache_write_tokens")):
        return unknown("cache_breakdown_not_reported")
    read = d["cached_tokens"]
    write = d["cache_write_tokens"]
    ordinary = u["input_tokens"] - read - write
    # Request explicitly selects 30m. Respect per-lifetime usage if returned.
    write_details = _get(d, "cache_write_tokens_details")
    write30 = _nullish(_get(write_details, "ttl_30m_tokens"), write)
    write12 = _nullish(_get(write_details, "ttl_12h_tokens"), 0)
    if not n(ordinary) or not n(write30) or not n(write12) or write30 + write12 != write:
        return unknown("inconsistent_usage")
    p = pricing["standard_global"]
    inputCost = (ordinary * p["input"] + read * p["cached_input"] + write30 * p["cache_write_30m"] + write12 * p["cache_write_12h"]) / 1e6
    outputCost = u["output_tokens"] * p["output"] / 1e6
    return {
        "cost_status": "estimated_from_reported_usage",
        "estimated_cost_usd": inputCost + outputCost,
        "input_cost_usd": inputCost,
        "output_cost_usd": outputCost,
        "input_tokens": u["input_tokens"],
        "ordinary_input_tokens": ordinary,
        "cached_input_tokens": read,
        "cache_write_tokens": write,
        "cache_write_30m_tokens": write30,
        "cache_write_12h_tokens": write12,
        "output_tokens": u["output_tokens"],
        "reasoning_tokens": _nullish(_get(_get(u, "output_tokens_details"), "reasoning_tokens"), None),
        "total_tokens": _nullish(_get(u, "total_tokens"), u["input_tokens"] + u["output_tokens"]),
    }
