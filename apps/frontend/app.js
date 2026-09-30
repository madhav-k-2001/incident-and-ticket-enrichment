// Alarm Investigation and Ticketing: chat client for the backend's SSE API.
//
//   POST /chat/stream            one turn, streamed (docs/chat-stream-protocol.md)
//   POST /chat/approvals/stream  answers every pending tool approval and
//                                streams the rest of the same turn
//
// The conversation for the current session is kept in localStorage so a
// reload does not lose it; the backend keeps the history the agent sees.

import { renderMarkdown } from "./markdown.js";
import { readEvents } from "./sse.js";

const MAX_CHARS = 4000;
const REASON_MAX_CHARS = 500;
const OUTPUT_MAX_CHARS = 6000;
const STORE_KEY = "alarm-investigation.conversation.v1";
const API_KEY_STORE = "alarm-investigation.api-key";
// The UI is served by the backend at /ui/, so the API is one level up.
const API_ROOT = new URL("../", document.baseURI);
const PLACEHOLDER = "Ask about an alarm, an asset or a ticket";
const ACRONYMS = { id: "ID", ids: "IDs", sop: "SOP", url: "URL", kb: "KB" };

const els = {
  log: document.getElementById("log"),
  inner: document.getElementById("log-inner"),
  empty: document.getElementById("empty"),
  form: document.getElementById("composer"),
  input: document.getElementById("message"),
  send: document.getElementById("send"),
  count: document.getElementById("count"),
  hint: document.getElementById("hint"),
  sessionLabel: document.getElementById("session-label"),
  newSession: document.getElementById("new-session"),
  keyButton: document.getElementById("key-button"),
  keyDialog: document.getElementById("key-dialog"),
  keyForm: document.getElementById("key-form"),
  keyText: document.getElementById("key-text"),
  keyInput: document.getElementById("key-input"),
  keyCancel: document.getElementById("key-cancel"),
  announcer: document.getElementById("announcer"),
};

const state = {
  sessionId: "",
  turns: [],
  busy: false,
  controller: null,
};

const coarsePointer = matchMedia("(pointer: coarse)").matches;
const numberFormat = new Intl.NumberFormat();
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

let seq = 0;
const uid = () => `${Date.now().toString(36)}${(seq++).toString(36)}`;

function newSessionId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  // randomUUID needs a secure context; fall back for plain-http LAN hosts.
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "html") el.innerHTML = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  el.append(...children.flat(Infinity).filter((c) => c != null && c !== false));
  return el;
}

function labelize(key) {
  const words = String(key)
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  if (!words.length) return String(key);
  words[0] = words[0][0].toUpperCase() + words[0].slice(1);
  return words.join(" ");
}

function pretty(value) {
  if (value == null) return "";
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function clip(text, max = OUTPUT_MAX_CHARS) {
  if (text.length <= max) return text;
  return `${text.slice(0, max)}\n… ${numberFormat.format(text.length - max)} more characters`;
}

const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function storage(action, key, value) {
  try {
    if (action === "get") return localStorage.getItem(key);
    if (action === "set") localStorage.setItem(key, value);
    if (action === "remove") localStorage.removeItem(key);
  } catch {
    // Storage can be blocked or full; the app works without it.
  }
  return null;
}

function announce(text) {
  els.announcer.textContent = "";
  setTimeout(() => (els.announcer.textContent = text), 50);
}

// ---------------------------------------------------------------------------
// Conversation model
// ---------------------------------------------------------------------------

function addTurn(turn) {
  Object.assign(turn, { id: uid(), time: Date.now() });
  if (turn.role === "assistant") turn.blocks = [];
  state.turns.push(turn);
  touch(turn);
  return turn;
}

function removeTurns(...turns) {
  for (const turn of turns) {
    turn.orphan = true;
    state.turns = state.turns.filter((t) => t !== turn);
    views.get(turn.id)?.root.remove();
    views.delete(turn.id);
  }
  schedule();
}

function addBlock(turn, block) {
  Object.assign(block, { id: uid(), rev: 0 });
  turn.blocks.push(block);
  touch(turn, block);
  return block;
}

function openText(turn) {
  const last = turn.blocks[turn.blocks.length - 1];
  return last?.type === "text" && last.open ? last : null;
}

function closeText(turn) {
  const block = openText(turn);
  if (block) {
    block.open = false;
    touch(turn, block);
  }
}

function findTool(turn, callId) {
  return turn.blocks.find((b) => b.type === "tool" && b.callId === callId);
}

// Tools still marked as running when a turn ends never reported a result.
function settleTools(turn) {
  for (const block of turn.blocks) {
    if (block.type === "tool" && (block.status === "running" || block.status === "awaiting")) {
      block.status = "incomplete";
      touch(turn, block);
    }
  }
}

function pendingApproval() {
  for (const turn of state.turns) {
    if (turn.role !== "assistant") continue;
    const block = turn.blocks.find(
      (b) => b.type === "approval" && (b.status === "pending" || b.status === "sending")
    );
    if (block) return block;
  }
  return null;
}

function endTurn(turn, status, note) {
  closeText(turn);
  settleTools(turn);
  if (note) addBlock(turn, { type: "note", tone: status === "error" ? "error" : "info", text: note });
  turn.status = status;
  touch(turn);
  if (note) announce(note);
}

// ---------------------------------------------------------------------------
// Stream handling
// ---------------------------------------------------------------------------

function handleEvent(turn, { event, data }) {
  switch (event) {
    case "text_delta": {
      if (!data.delta) return false;
      const block = openText(turn) ?? addBlock(turn, { type: "text", text: "", open: true });
      block.text += data.delta;
      touch(turn, block);
      return false;
    }
    case "message": {
      const block = openText(turn);
      if (block) {
        block.text = data.text ?? block.text;
        block.open = false;
        touch(turn, block);
      } else if (data.text) {
        addBlock(turn, { type: "text", text: data.text, open: false });
      }
      return false;
    }
    case "tool_call": {
      closeText(turn);
      const existing = findTool(turn, data.call_id);
      if (existing) {
        existing.status = "running";
        touch(turn, existing);
      } else {
        addBlock(turn, {
          type: "tool",
          callId: data.call_id,
          name: data.name,
          server: data.server_label ?? null,
          args: clip(pretty(data.arguments)),
          status: "running",
        });
      }
      return false;
    }
    case "tool_output": {
      const block = findTool(turn, data.call_id);
      if (block) {
        block.output = clip(pretty(data.output));
        block.error = data.error ? clip(pretty(data.error)) : null;
        block.status = data.error ? "error" : "done";
        touch(turn, block);
      }
      return false;
    }
    case "approval_required": {
      closeText(turn);
      const approvals = Array.isArray(data.approvals) ? data.approvals : [];
      if (!approvals.length) {
        endTurn(turn, "error", "The assistant asked for approval without saying what for. Start a new session to continue.");
        return true;
      }
      for (const a of approvals) {
        const tool = findTool(turn, a.approval_id);
        if (tool) {
          tool.status = "awaiting";
          touch(turn, tool);
        }
      }
      const block = addBlock(turn, {
        type: "approval",
        status: "pending",
        items: approvals.map((a) => ({
          id: a.approval_id,
          tool: a.tool_name,
          server: a.server_name ?? null,
          args: a.arguments,
          decision: null,
        })),
      });
      block.flash = true;
      turn.status = "awaiting";
      touch(turn);
      afterFlush(() => views.get(turn.id)?.blocks.get(block.id)?.el.focus({ preventScroll: true }));
      const names = block.items.map((i) => labelize(i.tool)).join(", ");
      announce(`Approval needed: ${names}.`);
      return true;
    }
    case "done": {
      closeText(turn);
      const hasText = turn.blocks.some((b) => b.type === "text" && b.text.trim());
      if (!hasText && data.final_output) addBlock(turn, { type: "text", text: data.final_output, open: false });
      endTurn(turn, "done");
      announce("Reply finished.");
      return true;
    }
    case "error": {
      const message =
        data.type === "InternalError" || !data.message
          ? "The agent run failed. Try again, or start a new session if it keeps failing."
          : `The run stopped: ${data.message}`;
      endTurn(turn, "error", message);
      return true;
    }
    default:
      return false; // run_started, agent_updated, reasoning_delta
  }
}

function apiHeaders() {
  const headers = { "Content-Type": "application/json", Accept: "text/event-stream" };
  const key = storage("get", API_KEY_STORE);
  if (key) headers["X-API-Key"] = key;
  return headers;
}

async function readDetail(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).filter(Boolean).join("; ");
  } catch {
    // Not JSON.
  }
  return "";
}

function httpMessage(status, detail) {
  if (status === 422) return `The request was rejected${detail ? `: ${detail}` : "."}`;
  if (status >= 500) return `The backend failed to handle the request (${status}). Try again.`;
  return `The backend returned ${status}${detail ? `: ${detail}` : "."}`;
}

// Runs one streaming request into `turn`. `hooks.onHttpError(status, detail)`
// returns true when it handled a non-2xx response itself.
async function runStream(path, body, turn, hooks = {}) {
  const controller = new AbortController();
  state.controller = controller;
  setBusy(true);
  turn.status = "streaming";
  touch(turn);

  try {
    let response;
    try {
      response = await fetch(new URL(path, API_ROOT), {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch {
      if (turn.orphan) return;
      if (controller.signal.aborted) return endTurn(turn, "stopped", hooks.stoppedNote);
      if (hooks.onNetworkError?.()) return;
      return endTurn(turn, "error", "Couldn't reach the backend. Check that it's running, then try again.");
    }

    if (!response.ok) {
      const detail = await readDetail(response);
      if (turn.orphan) return;
      if (!hooks.onHttpError?.(response.status, detail)) {
        endTurn(turn, "error", httpMessage(response.status, detail));
      }
      return;
    }

    hooks.onOpen?.();
    let ended = false;
    try {
      for await (const event of readEvents(response.body)) {
        if (turn.orphan) return;
        if (handleEvent(turn, event)) {
          ended = true;
          break;
        }
      }
    } catch {
      // Aborted or the connection dropped; reported below.
    }
    if (!ended && !turn.orphan) {
      if (controller.signal.aborted) endTurn(turn, "stopped", hooks.stoppedNote);
      else endTurn(turn, "error", "The connection closed before the reply finished. This turn wasn't saved.");
    }
  } finally {
    if (state.controller === controller) {
      state.controller = null;
      setBusy(false);
    }
  }
}

async function send(text) {
  const message = text.trim();
  if (!message || state.busy || pendingApproval()) return;

  const userTurn = addTurn({ role: "user", text: message });
  const turn = addTurn({ role: "assistant", status: "streaming" });
  setInput("");
  forceScroll = true;

  await runStream("chat/stream", { session_id: state.sessionId, message }, turn, {
    stoppedNote: "Stopped. This turn wasn't saved to the session.",
    onHttpError(status) {
      if (status === 401) {
        removeTurns(userTurn, turn);
        if (!els.input.value) setInput(message);
        askForKey(() => els.form.requestSubmit());
        return true;
      }
      if (status === 409) {
        endTurn(
          turn,
          "error",
          "This session is waiting for an approval that isn't shown here. Start a new session to continue."
        );
        return true;
      }
      return false;
    },
  });
}

function decide(turn, block, item, approved, reason) {
  item.decision = { approved, reason: approved ? null : (reason ?? "").trim() || null };
  item.mode = null;
  block.error = null;
  touch(turn, block);
  const next = block.items.find((i) => !i.decision);
  if (next) {
    afterFlush(() => document.getElementById(`approve-${domId(next.id)}`)?.focus());
    return;
  }
  submitApprovals(turn, block);
}

async function submitApprovals(turn, block) {
  const reopen = (error) => {
    block.status = "pending";
    block.error = error;
    for (const item of block.items) item.decision = null;
    turn.status = "awaiting";
    touch(turn, block);
  };

  block.status = "sending";
  touch(turn, block);

  const decisions = block.items.map((i) => ({
    approval_id: i.id,
    approved: i.decision.approved,
    ...(i.decision.reason ? { reason: i.decision.reason } : {}),
  }));
  const anyApproved = decisions.some((d) => d.approved);

  await runStream("chat/approvals/stream", { session_id: state.sessionId, decisions }, turn, {
    stoppedNote: anyApproved ? "Stopped. The approved action may already have run." : "Stopped.",
    onOpen() {
      block.status = "sent";
      touch(turn, block);
    },
    onNetworkError() {
      reopen("Couldn't reach the backend. Check that it's running, then decide again.");
      return true;
    },
    onHttpError(status, detail) {
      if (status === 401) {
        // Nothing ran: show the request again, and resend these decisions once a key is saved.
        const decided = block.items.map((i) => i.decision);
        reopen(null);
        askForKey(() => {
          block.items.forEach((item, n) => (item.decision = decided[n]));
          submitApprovals(turn, block);
        });
        return true;
      }
      if (status === 404) {
        block.status = "expired";
        touch(turn, block);
        endTurn(turn, "done", "This request expired or was already answered. Send a new message to continue.");
        return true;
      }
      reopen(httpMessage(status, detail));
      return true;
    },
  });
}

function stopRun() {
  state.controller?.abort();
}

function newSession() {
  stopRun();
  for (const turn of state.turns) turn.orphan = true;
  state.turns = [];
  state.sessionId = newSessionId();
  for (const view of views.values()) view.root.remove();
  views.clear();
  renderSessionLabel();
  persist();
  schedule();
  els.input.focus();
  announce("New session started.");
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

const views = new Map(); // turn.id -> { root, body, working, blocks: Map(block.id -> { el, rev }) }
const dirty = new Set();
const focusQueue = [];
let frame = 0;
let forceScroll = false;

function touch(turn, block) {
  if (block) block.rev += 1;
  dirty.add(turn);
  schedule();
}

function schedule() {
  if (!frame) frame = requestAnimationFrame(flush);
}

function afterFlush(fn) {
  focusQueue.push(fn);
  schedule();
}

function flush() {
  frame = 0;
  const stick = forceScroll || els.log.scrollHeight - els.log.scrollTop - els.log.clientHeight < 96;
  forceScroll = false;

  for (const turn of dirty) if (!turn.orphan) syncTurn(turn);
  dirty.clear();

  els.empty.hidden = state.turns.length > 0;
  els.log.setAttribute("aria-busy", String(state.busy));
  updateComposer();
  if (stick) els.log.scrollTop = els.log.scrollHeight;
  while (focusQueue.length) focusQueue.shift()();
  persistSoon();
}

function syncTurn(turn) {
  let view = views.get(turn.id);
  if (!view) {
    view = createTurnView(turn);
    views.set(turn.id, view);
    els.inner.append(view.root);
  }
  if (turn.role !== "assistant") return;

  for (const block of turn.blocks) {
    const known = view.blocks.get(block.id);
    if (known && known.rev === block.rev) continue;
    const el = renderBlock(turn, block);
    if (known) known.el.replaceWith(el);
    else view.body.insertBefore(el, view.working);
    view.blocks.set(block.id, { el, rev: block.rev });
  }

  const last = turn.blocks[turn.blocks.length - 1];
  const showingProgress =
    (last?.type === "text" && last.open) || (last?.type === "tool" && last.status === "running");
  view.working.hidden = turn.status !== "streaming" || showingProgress;
}

function createTurnView(turn) {
  const isUser = turn.role === "user";
  const body = h("div", { class: "turn-body" });
  const root = h(
    "article",
    { class: `turn turn-${turn.role}`, "aria-label": isUser ? "You" : "Assistant" },
    h(
      "div",
      { class: "turn-gutter" },
      h("span", { class: "turn-role" }, isUser ? "You" : "Assistant"),
      h("time", { class: "turn-time", datetime: new Date(turn.time).toISOString() }, timeFormat.format(turn.time))
    ),
    body
  );
  const view = { root, body, working: null, blocks: new Map() };
  if (isUser) {
    body.append(h("p", { class: "user-text" }, turn.text));
  } else {
    view.working = h("p", { class: "working", hidden: true }, "Working");
    body.append(view.working);
  }
  return view;
}

function renderBlock(turn, block) {
  switch (block.type) {
    case "text":
      return h("div", { class: block.open ? "md streaming" : "md", html: renderMarkdown(block.text) });
    case "tool":
      return renderTool(turn, block);
    case "approval":
      return renderApproval(turn, block);
    default:
      return h("p", { class: "note", "data-tone": block.tone }, block.text);
  }
}

const TOOL_STATUS = {
  running: "running",
  done: "finished",
  error: "failed",
  awaiting: "waiting for approval",
  incomplete: "no result",
};

function renderTool(turn, block) {
  const visibleFlag = { error: "Failed", awaiting: "Waiting for approval", incomplete: "No result" }[block.status];
  const result =
    block.status === "done" || block.status === "error"
      ? [
          h("p", { class: "tool-label" }, block.error ? "Error" : "Result"),
          h("pre", {}, block.error || block.output || "(empty)"),
        ]
      : null;

  return h(
    "details",
    {
      class: "tool",
      "data-status": block.status,
      open: block.expanded,
      ontoggle: (e) => {
        block.expanded = e.currentTarget.open;
        persistSoon();
      },
    },
    h(
      "summary",
      {},
      h("span", { class: "tool-mark", "aria-hidden": "true" }),
      h("span", { class: "tool-name" }, labelize(block.name)),
      block.server ? h("span", { class: "tool-flag" }, block.server) : null,
      visibleFlag ? h("span", { class: "tool-flag" }, visibleFlag) : h("span", { class: "sr-only" }, TOOL_STATUS[block.status])
    ),
    h(
      "div",
      { class: "tool-body" },
      h("p", { class: "tool-label" }, "Input"),
      h("pre", {}, block.args || "{}"),
      result
    )
  );
}

const domId = (id) => String(id).replace(/[^\w-]/g, "");

function approvalLegend(block) {
  if (block.status === "pending") return "Approval needed";
  if (block.status === "sending") return "Sending your decision";
  if (block.status === "expired") return "Approval expired";
  const approved = block.items.filter((i) => i.decision?.approved).length;
  if (approved === block.items.length) return "Approved";
  if (approved === 0) return "Rejected";
  return "Decisions sent";
}

function renderApproval(turn, block) {
  const flash = block.flash;
  block.flash = false;
  const count = block.items.length;
  return h(
    "section",
    {
      class: flash ? "approval flash" : "approval",
      "data-status": block.status,
      tabindex: "-1",
      "aria-label": approvalLegend(block),
    },
    h("p", { class: "approval-legend" }, approvalLegend(block)),
    block.status === "pending"
      ? h(
          "p",
          { class: "approval-note" },
          count > 1
            ? `The assistant wants to run ${count} actions. Nothing runs until you decide on each one.`
            : "The assistant wants to run this action. Nothing runs until you decide."
        )
      : null,
    block.items.map((item) => renderApprovalItem(turn, block, item)),
    block.error ? h("p", { class: "approval-error", role: "alert" }, block.error) : null
  );
}

function renderApprovalItem(turn, block, item) {
  const headingId = `approval-${domId(item.id)}`;
  return h(
    "div",
    { class: "approval-item", role: "group", "aria-labelledby": headingId },
    h(
      "div",
      { class: "approval-head" },
      h("h3", { class: "approval-tool", id: headingId }, labelize(item.tool)),
      item.server ? h("span", { class: "approval-server" }, item.server) : null
    ),
    renderFields(item.args),
    renderDecision(turn, block, item)
  );
}

function renderFields(args) {
  if (args == null || args === "") return h("p", { class: "muted" }, "No details were sent with this action.");
  if (!isPlainObject(args)) return h("pre", { class: "code" }, pretty(args));
  // Unwrap a single wrapper object such as { draft: {...} }.
  const keys = Object.keys(args);
  const fields = keys.length === 1 && isPlainObject(args[keys[0]]) ? args[keys[0]] : args;
  return fieldList(fields);
}

function fieldList(obj) {
  return h(
    "dl",
    { class: "fields" },
    Object.entries(obj).map(([key, value]) => [h("dt", {}, labelize(key)), h("dd", {}, fieldValue(value))])
  );
}

function fieldValue(value) {
  if (value == null || value === "") return h("span", { class: "muted" }, "Not set");
  if (isPlainObject(value)) return fieldList(value);
  if (Array.isArray(value)) {
    if (!value.length) return h("span", { class: "muted" }, "None");
    if (value.every((v) => v === null || typeof v !== "object")) return value.join(", ");
    return h("pre", { class: "code" }, pretty(value));
  }
  return String(value);
}

function renderDecision(turn, block, item) {
  if (block.status !== "pending") {
    if (!item.decision) return null;
    const { approved, reason } = item.decision;
    return h("p", { class: "decision-result" }, approved ? "Approved" : reason ? `Rejected: ${reason}` : "Rejected");
  }

  const id = domId(item.id);
  if (item.mode === "rejecting") {
    return h(
      "div",
      { class: "reject-form" },
      h("label", { for: `reason-${id}` }, "Tell the assistant why (optional)"),
      h(
        "textarea",
        {
          id: `reason-${id}`,
          class: "reason",
          rows: "2",
          maxlength: String(REASON_MAX_CHARS),
          oninput: (e) => (item.reasonDraft = e.target.value),
        },
        item.reasonDraft ?? ""
      ),
      h(
        "div",
        { class: "decision" },
        h(
          "button",
          {
            type: "button",
            class: "btn btn-quiet",
            onclick: () => {
              item.mode = null;
              touch(turn, block);
              afterFlush(() => document.getElementById(`approve-${id}`)?.focus());
            },
          },
          "Cancel"
        ),
        h(
          "button",
          { type: "button", class: "btn btn-primary", onclick: () => decide(turn, block, item, false, item.reasonDraft) },
          "Reject"
        )
      )
    );
  }

  if (item.decision) {
    return h(
      "div",
      { class: "decision" },
      h("p", { class: "decision-state" }, item.decision.approved ? "Approved" : "Rejected"),
      h(
        "button",
        {
          type: "button",
          class: "btn btn-quiet",
          onclick: () => {
            item.decision = null;
            touch(turn, block);
            afterFlush(() => document.getElementById(`approve-${id}`)?.focus());
          },
        },
        "Change"
      )
    );
  }

  return h(
    "div",
    { class: "decision" },
    h(
      "button",
      {
        type: "button",
        class: "btn btn-secondary",
        onclick: () => {
          item.mode = "rejecting";
          touch(turn, block);
          afterFlush(() => document.getElementById(`reason-${id}`)?.focus());
        },
      },
      "Reject"
    ),
    h(
      "button",
      { type: "button", id: `approve-${id}`, class: "btn btn-primary", onclick: () => decide(turn, block, item, true) },
      "Approve"
    )
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

let trimmedNotice = 0;

function setBusy(busy) {
  state.busy = busy;
  schedule();
}

function updateComposer() {
  const waiting = Boolean(pendingApproval()) && !state.busy;
  els.input.disabled = waiting;
  els.input.placeholder = waiting ? "Answer the approval request first" : PLACEHOLDER;

  if (state.busy) {
    els.send.textContent = "Stop";
    els.send.className = "btn btn-secondary send";
    els.send.disabled = false;
  } else {
    els.send.textContent = "Send";
    els.send.className = "btn btn-primary send";
    els.send.disabled = waiting || !els.input.value.trim();
  }

  const length = els.input.value.length;
  const level = length >= MAX_CHARS ? "full" : length >= MAX_CHARS * 0.9 ? "near" : "";
  els.count.dataset.level = level;
  els.count.textContent = trimmedNotice
    ? `Pasted text was cut to ${numberFormat.format(MAX_CHARS)} characters`
    : `${numberFormat.format(length)} / ${numberFormat.format(MAX_CHARS)}`;
}

function setInput(value) {
  els.input.value = value;
  fitInput();
  updateComposer();
}

function fitInput() {
  els.input.style.height = "auto";
  els.input.style.height = `${els.input.scrollHeight}px`;
}

function renderSessionLabel() {
  els.sessionLabel.textContent = `Session ${state.sessionId.slice(0, 8)}`;
  els.sessionLabel.title = `Session ID: ${state.sessionId}`;
}

// ---------------------------------------------------------------------------
// API key
// ---------------------------------------------------------------------------

let keyRetry = null;

function askForKey(retry) {
  const saved = storage("get", API_KEY_STORE);
  els.keyText.textContent = retry
    ? saved
      ? "The saved API key was rejected. Enter a valid key for this backend."
      : "This backend needs an API key. It is stored in this browser only."
    : "Change the API key for this backend, or clear the field to remove it.";
  els.keyInput.value = retry ? "" : saved ?? "";
  keyRetry = retry;
  els.keyDialog.showModal();
}

function updateKeyButton() {
  els.keyButton.hidden = !storage("get", API_KEY_STORE);
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

const TRANSIENT = new Set(["rev", "flash", "mode", "reasonDraft", "orphan"]);
let persistTimer = 0;

function persist() {
  clearTimeout(persistTimer);
  const data = JSON.stringify({ sessionId: state.sessionId, turns: state.turns }, (key, value) =>
    TRANSIENT.has(key) ? undefined : value
  );
  storage("set", STORE_KEY, data);
}

function persistSoon() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(persist, 400);
}

function restore() {
  let saved = null;
  try {
    saved = JSON.parse(storage("get", STORE_KEY));
  } catch {
    saved = null;
  }
  if (!saved?.sessionId || !Array.isArray(saved.turns)) {
    state.sessionId = newSessionId();
    return;
  }

  state.sessionId = saved.sessionId;
  state.turns = saved.turns.filter((t) => t && (t.role === "user" || t.role === "assistant"));
  for (const turn of state.turns) {
    dirty.add(turn);
    if (turn.role !== "assistant") continue;
    turn.blocks = Array.isArray(turn.blocks) ? turn.blocks : [];
    for (const block of turn.blocks) {
      block.rev = 0;
      if (block.type === "text") block.open = false;
      if (block.type === "approval" && block.status === "sending") block.status = "sent";
    }
    if (turn.status === "streaming") {
      endTurn(turn, "stopped", "The page was closed before this reply finished.");
    }
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.busy) stopRun();
  else send(els.input.value);
});

els.input.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey || e.isComposing || coarsePointer) return;
  e.preventDefault();
  if (!state.busy) els.form.requestSubmit();
});

els.input.addEventListener("paste", (e) => {
  const pasted = e.clipboardData?.getData("text") ?? "";
  const { value, selectionStart, selectionEnd } = els.input;
  if (value.length - (selectionEnd - selectionStart) + pasted.length > MAX_CHARS) {
    clearTimeout(trimmedNotice);
    trimmedNotice = setTimeout(() => {
      trimmedNotice = 0;
      updateComposer();
    }, 4000);
  }
});

els.input.addEventListener("input", () => {
  fitInput();
  updateComposer();
});

els.inner.addEventListener("click", (e) => {
  const example = e.target.closest(".example");
  if (!example || els.input.disabled) return;
  setInput(example.textContent.trim());
  els.input.focus();
});

els.newSession.addEventListener("click", newSession);
els.keyButton.addEventListener("click", () => askForKey(null));
els.keyCancel.addEventListener("click", () => {
  keyRetry = null;
  els.keyDialog.close();
});
els.keyDialog.addEventListener("close", () => (keyRetry = null));
els.keyForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const key = els.keyInput.value.trim();
  if (key) storage("set", API_KEY_STORE, key);
  else storage("remove", API_KEY_STORE);
  const retry = keyRetry;
  els.keyDialog.close();
  updateKeyButton();
  if (key && retry) retry();
});

addEventListener("pagehide", persist);

if (coarsePointer) els.hint.hidden = true;
restore();
renderSessionLabel();
updateKeyButton();
forceScroll = true;
schedule();
