# Optional backend chat cleanup

This control applies only to conversations created by the paired Gemini Web backend. It is not a browser-history cleaner, bulk Gemini deletion, logout, or cookie reset.

## Behavior

- Default: off. Saving the explicit setting changes `auto_delete_conversation` only on that profile's dedicated backend.
- Enabling is refused when `multi_turn` or `temporary_chat` is enabled. The receiver never switches those modes behind the user's back.
- The existing backend waits for the upstream response/artifact read to complete, extracts that response's conversation ID, and schedules its deletion asynchronously. It does not wait for a client receipt acknowledgement and cannot prove the downstream application received the answer.
- Deletion is best effort. A configured checkbox does not mean a particular conversation was successfully removed. Authentication problems, upstream changes or network errors may prevent removal.
- Existing manual Gemini conversations are not enumerated or bulk-deleted.
- Removing an item from Gemini history does not establish immediate erasure from every Google storage/retention system.

## API

`GET /v1/cleanup`, authenticated by the installation's scoped device token:

```json
{"enabled":false,"multiTurn":false,"temporaryChat":false,"bestEffort":true}
```

`POST /v1/cleanup` accepts exactly `{"enabled":true}` or `{"enabled":false}`. Invalid booleans or extra keys are rejected. An incompatible backend mode returns 409 when enabling; disabling remains allowed. Backend settings are reread to verify the change. No cookies or admin credentials are returned.

## Use

In extension settings, connect the profile, find **Backend chat cleanup**, read the warning, select the desired setting and save. Confirm the server-reported setting. Do not infer deletion success from the profile's `good` login status.

For verification, use a disposable synthetic backend/config integration test first. A live deletion test must use an explicitly disposable new conversation, never a user's existing chat. Manual Google UI history inspection is required before claiming a live conversation disappeared.
