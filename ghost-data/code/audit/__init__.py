"""Ghost Data Still Exists - deletion-audit pipeline.

Architecture: the model describes, code decides. Everything in this package is
deterministic; the optional LLM layer (llm.py) only classifies request intent and
extracts facts from screenshots, and its output is always corroborated here.
"""
