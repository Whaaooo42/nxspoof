/*
  js/core.js - shared state and small helpers of the NXSPOOF dashboard, and talking to NXSPOOF.

  Everything the other modules share lives in S (the last /api/state answer, the view, the events...).
  Text coming from consoles is always inserted as plain text, never as HTML.
*/

"use strict";

// #############################################################################################################################################################
// ##  SHARED STATE                                                                                                                                           ##
// #############################################################################################################################################################

export const TOKEN = document.querySelector('meta[name="nxspoof-token"]').content;   // proves the controls come from this page
export const COLORS = { red: "var(--red)", green: "var(--green)", yellow: "var(--yellow)", blue: "var(--blue)",
                        magenta: "var(--magenta)", cyan: "var(--cyan)", white: "var(--white)" };
export const S = {
  events: [],              // every event received, oldest first
  lastState: null,         // last /api/state answer
  view: null,              // "technical" | "casual" (null until the first state arrives)
  ended: false,            // the run said goodbye
  past: null,              // a past run shown in the log pane instead of the live log: {name, events} (js/runs.js)
  order: "oldest",         // "oldest" (time order, newest at the bottom) | "newest" (newest on top)
  refresh: () => {},       // ask for a new state now (set by app.js)
};

// #############################################################################################################################################################
// ##  SMALL HELPERS                                                                                                                                          ##
// #############################################################################################################################################################

// Read a choice remembered in this browser (storage can be unavailable: then nothing is remembered).
export function load(key) { try { return localStorage.getItem("nxspoof." + key); } catch (e) { return null; } }
// Remember a choice in this browser (silently skipped if storage is unavailable).
export function store(key, v) { try { localStorage.setItem("nxspoof." + key, v); } catch (e) {} }

// Create an element with optional class and text (text is never parsed as HTML).
export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// "3 min 12 s" style durations.
export function dur(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + " s";
  if (sec < 3600) return Math.floor(sec / 60) + " min " + (sec % 60) + " s";
  return Math.floor(sec / 3600) + " h " + Math.floor((sec % 3600) / 60) + " min";
}

// HH:MM:SS from epoch seconds.
export function hms(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour12: false }); }

let toastTimer = null;     // hides the current toast
// Short message at the bottom of the page (red when bad).
export function toast(msg, bad) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ""; }, 3500);
}

// Call a control on NXSPOOF (token-protected); returns {ok, message} or throws with the reason.
export async function action(name, payload) {
  const r = await fetch("/api/action/" + name, {
    method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-NXSPOOF-Token": TOKEN },
    body: JSON.stringify(payload || {}) });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || ("error " + r.status));
  return data;
}

// True if this control is offered by NXSPOOF right now.
export function can(name) { return !!(S.lastState && S.lastState.actions.includes(name)) && !S.ended; }

export const SELFTEST = "self-test";     // who for "Mac (self-test)" (nxspoof/core/text.py)
const OUTCOME_CLASS = { "=blocked": "hl-bad", "=fail": "hl-bad", "=pass": "hl-good", "=up": "hl-good", "=warning": "hl-warn", "=down": "hl-warn" };

// A marked stretch: an outcome word (who "=blocked", "=pass"... from core/text.py outcome) in its color; a console
// reference in the console's CURRENT color (so every line matches its card); gray for the Mac's self-test.
function nameSpan(text, who) {
  if (String(who).startsWith("=")) return el("span", OUTCOME_CLASS[who] || null, text);
  if (who === SELFTEST) return el("span", "gray", text);
  const c = (S.lastState ? S.lastState.consoles : []).find(x => x.mac === who);
  const span = el("span", null, text);
  span.style.color = c ? (COLORS[c.color] || "var(--text)") : "var(--text)";
  span.style.fontWeight = "700";
  return span;
}

// Turn a line into colored pieces: exactly its marked stretches (spans: [start, end, who]: console names and
// outcome words); the rest stays plain. Nothing is ever searched for in the text.
export function highlight(text, spans) {
  const frag = document.createDocumentFragment();
  let pos = 0;
  for (const [s, e, who] of (spans || []).slice().sort((a, b) => a[0] - b[0])) {
    if (s < pos || e > text.length) continue;
    if (s > pos) frag.appendChild(document.createTextNode(text.slice(pos, s)));
    frag.appendChild(nameSpan(text.slice(s, e), who));
    pos = e;
  }
  if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
  return frag;
}
