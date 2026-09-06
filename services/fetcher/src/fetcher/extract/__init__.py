"""LLM extraction over already-fetched content — the `/v1/extract` lane.

Structured extraction used to live in knowledge-pipeline's `workflows` package
and, separately, in newsletter-assistant's voice agent, which drifted apart on
prompt version, model, call shape and failure mode. It lives here instead so
both reach the same implementation over HTTP: one content body in, several
typed payloads out.
"""
