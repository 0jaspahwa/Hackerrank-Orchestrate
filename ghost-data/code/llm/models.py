"""Model choices and self-imposed rate limits (measurements: docs/llm_limits.md)."""

# (provider, model) in preference order
TEXT = [("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-3.5-flash-lite")]
# pinned: Groq qwen (11/11 linked-resource states, 0 false tokens after corroboration, published limits);
# Gemini is an availability-only fallback (free tier: 20 requests/day/model). See docs/llm_limits.md.
VISION = [("groq", "qwen/qwen3.8-27b"), ("gemini", "gemini-3.5-flash")]

# "provider:model" -> (requests/min, tokens/min) we allow ourselves.
# Groq reports 1000 requests (daily window) and 8000 tokens/min per model; we stay at 75 % of TPM.
# Gemini sends no rate-limit headers; we stay well under the free-tier per-minute quotas and
# honour retryDelay from any 429.
RATE = {
    "groq:openai/gpt-oss-120b": (20, 5600),      # 70 % of the 8000 TPM limit
    "groq:openai/gpt-oss-20b": (20, 6000),
    "groq:qwen/qwen3.8-27b": (10, 5600),         # 70 % of 8000 TPM; ~1 vision call / min
    "gemini:gemini-3.5-flash": (5, 0),
    "gemini:gemini-3.5-flash-lite": (8, 0),
}
