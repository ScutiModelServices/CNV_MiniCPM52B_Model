# Purpose

**Kind:** chat messages → text (conversation, structured)

**Group:** Conversation

**Tags:** `cpu`, `streaming`, `tool-calls`, `json-schema`, `thinking`, `project-models`

| | |
|---|---|
| Input | Chat messages. Optional: `tools`, JSON `schema`, `thinking`, temperature, max tokens, project. |
| Output | JSON: content, thinking, `tool_calls`, finish reason. Streamable. |
| Runs on | CPU, through Ollama (MiniCPM5-2B). Heaviest chat model here. |

One call is one model turn. Caller runs the tools and the loop.

**Use for:** agents that need tool calls, schema-constrained JSON, or thinking.
**Not for:** cheap bulk calls.
