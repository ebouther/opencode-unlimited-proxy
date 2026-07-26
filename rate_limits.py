def classify_upstream_429(response) -> tuple[str, int | None, object]:
    """Preserve upstream limit semantics; a 429 is not evidence of an IP block."""
    retry_after = response.headers.get("retry-after")
    try:
        retry_seconds = max(0, int(float(retry_after))) if retry_after else None
    except (TypeError, ValueError):
        retry_seconds = None

    try:
        payload = response.json()
    except Exception:
        payload = {"error": {"type": "upstream_rate_limit", "message": response.text}}

    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    error_type = str(error.get("type", "upstream_rate_limit"))
    if error_type in {"FreeUsageLimitError", "GoUsageLimitError", "BlackUsageLimitError"}:
        category = "quota"
    elif error_type == "RateLimitError":
        category = "rate_limit"
    else:
        category = "upstream_rate_limit"
    return category, retry_seconds, payload
