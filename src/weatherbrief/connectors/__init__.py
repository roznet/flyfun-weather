"""Agent-facing connector layer shared by the MCP server and the GPT/OpenAPI router.

The heavy meteorological logic lives upstream (analysis pipeline, served as raw
JSON by the REST API). This package holds only the thin agent-facade layer:
the response *shaping* that compresses raw payloads into LLM-sized structures
and carries the meteorological guardrails. Both connector front-doors —
``weatherbrief.mcp.server`` (Claude) and ``weatherbrief.api.agent`` (ChatGPT
Custom GPT) — share these views so the two integrations cannot drift.
"""
