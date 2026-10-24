# Response adaptation

Both Chat Completions and Responses reject these exact whole upstream replies after collapsing whitespace (case and punctuation remain significant):

- `I'm having a hard time fulfilling your request. Can I help you with something else instead?`
- `I seem to be encountering an error. Can I try something else for you?`

This is not a refusal filter. Quotes, explanatory prefixes/suffixes and ordinary refusals pass unchanged. Streaming holds only the initial candidate prefix until it differs from either reply; a matching whole reply is never sent as assistant content. Upstream failures use the existing HTTP 502 or streaming failure path, never a successful terminal event. No retry is performed.

Tool adaptation accepts only declared names and valid JSON-object arguments. Invalid, unclosed or disallowed tool calls fail explicitly, without returning executable calls. `required` and forced-name `tool_choice` fail when no valid call is returned. Optional `auto` allows a normal text answer. Argument JSON is preserved rather than decoded through floating point. This validates the envelope, not the tool's parameter schema or execution safety. No tool is executed by the adapter.

## Opt-in Chat Completions contracts

Use the custom per-request key:

```json
{
  "model": "gemini-flash",
  "messages": [{"role": "user", "content": "Return a JSON object with ok=true"}],
  "stream": false,
  "gemini_web_options": {"output_mode": "json"}
}
```

Modes:

- `default` (also when absent): no added instruction or final-output contract; ordinary streaming remains incremental.
- `json`: adds one final-answer instruction without replacing client system messages; the trimmed final text must satisfy Go's `json.Valid`. Markdown fences are rejected, never stripped. JSON scalars and arrays are valid too.
- `code-only`: adds one code-only final-answer instruction without replacing client system messages; final text must be nonempty and contain neither backtick nor tilde Markdown fences. This is a formatting check, **not semantic code validation**; prose or invalid code without fences cannot reliably be detected.

Valid tool calls take precedence over the final-answer contract. Final output violating the contract returns HTTP 502. Unknown/non-string modes or a non-object options value return HTTP 400. Non-default modes currently require `stream=false`; streaming requests are explicitly rejected with HTTP 400 before generation, rather than exposing unvalidated output. These options apply only to Chat Completions, not Responses. No global persona, model change, automatic retry or orchestrator change is introduced.

Offline unit tests validate adaptation and formatting only. They do not establish upstream tool reliability or successful tool execution.
