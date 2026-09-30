// Reads a Server-Sent Events response body (from fetch) as { event, data }.
//
// The backend's stream endpoints are POST, which EventSource cannot do, so the
// body is parsed here. `data` is the parsed JSON payload. Keep-alive comment
// lines (": keep-alive") are skipped.

export async function* readEvents(body) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let event = "";
  let data = [];
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += value;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const raw of lines) {
        const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
        if (line === "") {
          if (data.length) yield { event: event || "message", data: parse(data.join("\n")) };
          event = "";
          data = [];
        } else if (!line.startsWith(":")) {
          const colon = line.indexOf(":");
          const field = colon === -1 ? line : line.slice(0, colon);
          let value = colon === -1 ? "" : line.slice(colon + 1);
          if (value.startsWith(" ")) value = value.slice(1);
          if (field === "event") event = value;
          else if (field === "data") data.push(value);
        }
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

function parse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}
