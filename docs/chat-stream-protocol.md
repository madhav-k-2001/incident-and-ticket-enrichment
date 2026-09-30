# Chat streaming protocol (`POST /chat/stream`)

Streams one chat turn as [Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html).
The request body is the same as `POST /chat`; history is loaded from and saved to
PostgreSQL by `session_id`, so the client only sends the new message.

```http
POST /chat/stream
Content-Type: application/json

{"session_id": "abc", "message": "Prepare an incident for the top alarm in EastRefinery"}
```

Responses: `200 text/event-stream`, or `422` (JSON) if the request is invalid
(e.g. blank message) — validation happens before the stream starts.

> The browser `EventSource` API only supports GET, so consume this with
> `fetch()` + a `ReadableStream` reader, or a library such as
> `@microsoft/fetch-event-source`. Browsers on another origin also need CORS
> configured on the backend (not set up yet).

## Events

Each frame is `event: <name>` and a single-line JSON `data:`. Frames are
separated by a blank line. Lines starting with `:` are keep-alive comments
(sent after `SSE_HEARTBEAT_SECONDS` of silence) and should be ignored.

| event             | data                                                    | notes |
|-------------------|---------------------------------------------------------|-------|
| `run_started`     | `{session_id}`                                          | first event |
| `agent_updated`   | `{agent}`                                               | active agent name |
| `text_delta`      | `{delta}`                                               | append to the answer being typed |
| `reasoning_delta` | `{delta}`                                               | reasoning summary, models that emit it |
| `message`         | `{text}`                                                | a complete assistant message (the agent may emit one before a tool call and one after) |
| `tool_call`       | `{call_id, name, arguments, server_label?}`             | `arguments` is the raw JSON string; `server_label` is set for hosted MCP tools |
| `tool_output`     | `{call_id, output, error?}`                             | match to `tool_call` by `call_id`; `output` is a string |
| `done`            | `{session_id, final_output}`                            | last event on success |
| `error`           | `{type, message}`                                       | last event on failure; HTTP status is already 200 by then |

Typical order for a turn with one tool call:

```
run_started → agent_updated → [text_delta*] → tool_call → tool_output → text_delta* → message → done
```

## Behaviour

- Closing the connection cancels the run; the turn is then **not** saved to history.
- Stream errors are reported as an `error` event. Only SDK errors (e.g. max turns
  exceeded) include their message; anything else is `InternalError` and logged server-side.
- There is no resume: `id:` / `Last-Event-ID` are not used.
