# Purpose

**Kind:** chat messages → text (conversation, structured)

**Group:** Conversation

**Tags:** `cpu`, `streaming`, `tool-calls`, `json-schema`, `thinking`, `project-models`, `variants`

| | |
|---|---|
| Input | Chat messages. Optional: `tools`, JSON `schema`, `thinking`, temperature, max tokens, project. |
| Output | JSON: content, thinking, `tool_calls`, finish reason. Streamable. |
| Runs on | CPU, through Ollama (MiniCPM5-2B). Heaviest chat model here. The Model variant card lists the quantizations (`Q4_K_M` to `F16`) with an estimate of the memory each takes and whether this machine can run it. |

One call is one model turn. Caller runs the tools and the loop.

**Use for:** agents that need tool calls, schema-constrained JSON, or thinking.
**Not for:** cheap bulk calls.
