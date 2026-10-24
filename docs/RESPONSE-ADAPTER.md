# Gemini Web response adapter investigation

Scope: improve the existing Gemini Web adapter. No AnythingLLM deployment, extra model, autonomous executor or new agent orchestrator.

## Verified baseline

The upstream v4.23.0 backend emulates tool calling: it embeds tool definitions in the prompt and parses model-produced tool-call blocks. This is not native Google tool calling. Plain-text generation success does not establish tool compatibility.

A read-only live probe requiring `session_status` returned plain text with no tool call. No external command or tool was executed. A separate streaming text probe returned its expected marker, but the router emitted two `[DONE]` events. A provider dashboard probe has a 15-second deadline while previously observed valid Gemini replies exceeded that deadline.

These findings must not be described as complete agent support.

## Repair acceptance criteria

- Exact known whole-response Google service-error text is an upstream failure, not successful assistant output.
- Quoting/discussing the same text in a longer answer must not be rejected by a substring filter.
- An emitted tool call must reference a declared tool and have object-valued, valid JSON arguments.
- Required/forced tool selection with no valid tool call must fail explicitly rather than finish successfully.
- Streaming must retain ordinary incremental output and terminate unsuccessful generations as errors.
- Gemini Web provider tests must allow a model-specific deadline without expanding every provider's timeout.
- Session-invalid, model-error and browser-offline states remain distinct.

## Verification boundary

Model-generated commands must not be executed automatically by this adapter. A client with tools remains responsible for tool execution authorization and returning results. Response normalization must never replace a failed answer with another model's output silently.

`deploy/response-adapter.patch` implements the response validation and output contracts. Go unit tests and `go vet` passed. Live operator verification through 9router passed normal, JSON and code-only marker probes; the dashboard model test returned `ok:true`. A required-tool probe returned one tool call and `finish_reason:tool_calls`; no tool was executed. This is a single successful probe, not a guarantee of model compliance. Default streaming passed its marker probe, but duplicate `[DONE]` events remain a known router issue. The separate `deploy/9router-gemini-ping.patch` raises Gemini Web dashboard-test timeout to 180 seconds and rejects known service-error text.
