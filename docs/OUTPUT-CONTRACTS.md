# Optional Gemini Web output contracts

Implemented by `deploy/response-adapter.patch` on top of upstream v4.23.0. Apply `deploy/browser-source.patch` first. Existing unpatched backends do not gain these modes just by updating the extension.

## Default behavior

Without explicit options, preserve normal conversations, existing client system messages, incremental streaming and declared tools. Never silently change every account to a programming-only persona.

## Chat request extension

```json
{
  "model": "gweb/YOUR_MODEL",
  "stream": false,
  "gemini_web_options": { "output_mode": "json" },
  "messages": [
    { "role": "system", "content": "Use only facts from supplied input." },
    { "role": "user", "content": "Return a JSON object with ready set to true." }
  ]
}
```

Modes: `default`, `code-only`, `json`. The adapter appends a single instruction without replacing user-provided system messages. This prompt is guidance, not a native Google enforcement boundary.

- **JSON:** validate the entire trimmed final text with a JSON parser. No silent stripping of Markdown fences, no extraction from surrounding prose.
- **Code-only:** reject empty text, Markdown fences and known whole-response service errors. This is structural validation only; arbitrary code correctness cannot be established without a language-specific parser/test environment.
- **Tools:** valid requested tool calls take precedence; the final-output contract applies after a tool result returns and the model emits a final answer. No automatic command execution belongs in this adapter.
- **Streaming:** nondefault contracts currently reject `stream: true` with HTTP 400 before generation. Use `stream: false` for JSON/code-only. Default mode retains incremental streaming.

Unknown options/modes should be rejected as bad requests. Format violations should be explicit errors, without unlimited retries or silent calls to another model. Never claim code was run or tested without execution evidence.
