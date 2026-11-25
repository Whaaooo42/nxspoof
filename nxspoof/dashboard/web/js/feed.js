/*
  js/feed.js - the log pane of the NXSPOOF dashboard: casual or technical view, oldest or newest first.

  Events come from /api/history (at load) and /api/events (live, see app.js); a past run picked in the pane (js/runs.js,
  S.past) is drawn the same way, while the live events keep arriving in the background. A line marks where each run
  begins ("NXSPOOF started · time", before its first event, kind run.log), so one dashboard session's runs stay apart. The technical line types
  come from the server (state.tags), so this file keeps no copy of them.
*/

"use strict";

import { S, el, hms, highlight, store } from "./core.js";

// A leading emoji (with its variation selector / joiners), then any spaces.
const EMOJI_RX = /^((?:\p{Extended_Pictographic}|\p{Emoji_Presentation})(?:️|‍(?:\p{Extended_Pictographic}|\p{Emoji_Presentation}))*️?)\s*/u;
const SHOWN = 800;         // lines kept on the page

// Line types that get their own column in the technical view (from the server).
function tags() { return new Set(S.lastState ? S.lastState.tags : []); }

// Text lines after the first lose their leading indentation (the column already aligns them); marks follow.
function dedent(text, spans) {
  const at = [];
  let out = "", lineStart = false;
  for (let i = 0; i < text.length; i++) {
    at[i] = out.length;
    if (text[i] === "\n") { lineStart = true; out += "\n"; continue; }
    if (lineStart && /\s/.test(text[i])) continue;
    lineStart = false;
    out += text[i];
  }
  at[text.length] = out.length;
  return [out, spans.map(([s, e, who]) => [at[s], at[e], who])];
}

// Marks of a line whose first `cut` characters were taken off (its tag or emoji went to the key column).
function shift(spans, cut) {
  return (spans || []).map(([s, e, who]) => [s - cut, e - cut, who]).filter(([s]) => s >= 0);
}

// Split a line into [key column, text]: the emoji (casual) or the line type (technical).
function splitKey(text, technical) {
  if (technical) {
    const tag = text.split(" ", 1)[0];
    if (tags().has(tag)) {
      // Remove only the padding that lines the tags up (to 6 characters, at least one space);
      // deeper indentation (summary rows, blocked lists) is meaningful and stays.
      const pad = Math.max(6 - tag.length, 1);
      return [tag, text.slice(tag.length).replace(new RegExp("^ {1," + pad + "}"), "")];
    }
    return ["", text.replace(/^\s+/, "")];
  }
  const t = text.replace(/^\s+/, "");
  const m = t.match(EMOJI_RX);
  if (m) return [m[1], t.slice(m[0].length)];
  return ["", t];                                      // continuation lines (summary rows, details)
}

// What one event looks like in the current view (null = hidden in this view).
function renderEvent(ev) {
  let key = "", text = null, extra = null, spans = [];
  if (S.view === "casual") {
    if (ev.casual) { [key, text] = splitKey(ev.casual, false); spans = shift(ev.casual_names, ev.casual.length - text.length); }
    else if (ev.problem && ev.tech) { key = "😕"; text = "Something went wrong:\n"; extra = ev.tech.trim(); }
  } else if (ev.tech) {
    [key, text] = splitKey(ev.tech, true);
    spans = shift(ev.tech_names, ev.tech.length - text.length);
  }
  if (text === null) return null;
  const row = el("div", "ev" + (ev.problem ? " problem" : ""));
  row.appendChild(el("div", "t", ev.t || hms(ev.ts)));
  row.appendChild(el("div", "k" + (S.view === "technical" && key ? " tag tg-" + key.replace(/[^A-Za-z]/g, "") : ""), key));
  const body = el("div", "x");
  body.appendChild(highlight(...dedent(text, spans)));
  if (extra) body.appendChild(el("span", "gray", extra));
  row.appendChild(body);
  return row;
}

// The pane's one scrolling area (a past run's checklist line and the log scroll together).
function scroller() { return document.getElementById("feed-body"); }

// What one event adds to the pane, in time order: a divider first when a run begins (its first line, kind "run.log",
// drawn in both styles even when that line itself isn't shown), then its row (if it shows in this view).
function partsFor(ev) {
  const out = [];
  if (ev.kind === "run.log") {
    const d = el("div", "ev-divider");
    d.appendChild(el("span", null, "NXSPOOF started · " + (ev.t || hms(ev.ts))));
    out.push(d);
  }
  const row = renderEvent(ev);
  if (row) out.push(row);
  return out;
}

// True when the log is scrolled to its newest end (so new lines should keep it there).
function atNewestEnd(box) {
  return S.order === "oldest" ? box.scrollHeight - box.scrollTop - box.clientHeight < 40 : box.scrollTop < 40;
}

// Rebuild the whole pane (after a view/order switch, the history load, or picking a run); the last SHOWN lines.
export function renderFeed() {
  const box = document.getElementById("events");
  const events = S.past ? S.past.events : S.events;
  box.className = S.view;
  box.textContent = "";
  const rows = [];
  for (let i = events.length - 1; i >= 0 && rows.length < SHOWN; i--) {
    rows.push(...partsFor(events[i]).reverse());     // newest first
  }
  if (S.order === "oldest") rows.reverse();
  for (const r of rows) box.appendChild(r);
  if (!rows.length) box.appendChild(el("div", "empty", S.past ? "Nothing to show in this view for that run." : "Waiting for events…"));
  const area = scroller();
  area.scrollTop = S.order === "oldest" && !S.past ? area.scrollHeight : 0;     // a past run opens at its top (its checklist line)
}

let redraw = null;          // a whole-pane redraw waiting to happen (late events come in batches: one redraw for all)

// Put events in time order (stable: an event's detail lines keep their place right after it).
export function timeOrder(events) {
  return events.map((ev, i) => [ev, i]).sort((a, b) => (a[0].ts - b[0].ts) || (a[1] - b[1])).map(x => x[0]);
}

// Add one live event; follow it only if you were already at the newest end. An event older than the newest one (a run
// that reconnects sends its history after the new dashboard's first lines) slots in by its time, then the pane redraws once.
export function addEvent(ev) {
  const last = S.events[S.events.length - 1];
  if (last && ev.ts < last.ts) {
    let i = S.events.length;
    while (i > 0 && S.events[i - 1].ts > ev.ts) i--;
    S.events.splice(i, 0, ev);
    if (!redraw && S.view && !S.past) redraw = setTimeout(() => { redraw = null; renderFeed(); }, 100);
    return;
  }
  S.events.push(ev);
  if (S.events.length > 3000) S.events = S.events.slice(-2000);
  if (!S.view || S.past) return;
  const parts = partsFor(ev);
  if (!parts.length) return;
  const box = document.getElementById("events");
  const empty = box.querySelector(".empty");
  if (empty) empty.remove();
  const area = scroller();
  const follow = atNewestEnd(area);
  if (S.order === "oldest") {
    for (const p of parts) box.appendChild(p);
    while (box.childNodes.length > SHOWN) box.removeChild(box.firstChild);
    if (follow) area.scrollTop = area.scrollHeight;
  } else {
    for (const p of parts) box.insertBefore(p, box.firstChild);      // the row ends on top, its divider just under it
    while (box.childNodes.length > SHOWN) box.removeChild(box.lastChild);
    if (follow) area.scrollTop = 0;
  }
}

// Switch between the casual and technical views.
export function setView(v) {
  S.view = v;
  store("view", v);
  document.getElementById("btn-technical").classList.toggle("active", v === "technical");
  document.getElementById("btn-casual").classList.toggle("active", v === "casual");
  renderFeed();
}

// Switch between time order (oldest first) and newest first.
export function setOrder(o) {
  S.order = o === "newest" ? "newest" : "oldest";
  store("order", S.order);
  document.getElementById("btn-oldest").classList.toggle("active", S.order === "oldest");
  document.getElementById("btn-newest").classList.toggle("active", S.order === "newest");
  if (S.view) renderFeed();
}
