/*
  js/start.js - the header's one main button (D27): ▶ Start NXSPOOF, or ■ Stop while it runs, in the same place and at the
  same width; and the panel under the header when this Mac isn't ready.

  ▶ Start: the quick check, then macOS's own password window (no Terminal window); the run starts in the background
  from NXSPOOF's data folder (output/datafolder.py), and this page shows it. If this Mac isn't ready, the panel says why,
  with "Fix and start" (when setup can fix all of it: one password for both, D30), "Open Configure" and "Start anyway". ■ Stop asks first, then stops the run cleanly (firewall off, summary, sharing
  off). While it starts or stops, the button says so and waits; what it's waiting for is written next to it.
  There's no Close button: closing the page closes the dashboard (app/pagewatch.py), and a run keeps going.
*/

"use strict";

import { S, el, toast, action, can } from "./core.js";
import { showView } from "./views.js";
import { taskNote } from "./tasks.js";

const STARTING = { terminal: "type your password in the Terminal window", password: "type your password in the macOS window",
                   waiting: "waiting for NXSPOOF to come up" };
let panel = null;        // the panel: null, or { kind: "not-ready", items: [...] }
let drawn = "null";      // the panel as last drawn (redrawn only when it changes, so buttons don't flicker)

// A button with its click handler (which gets the button).
function button(label, cls, onClick, title) {
  const b = el("button", cls, label);
  b.type = "button";
  if (title) b.title = title;
  b.addEventListener("click", () => onClick(b));
  return b;
}

// A button that only says what's happening (starting, stopping): greyed, not clickable.
function waitingButton(label) {
  const b = el("button", "btn", label);
  b.type = "button";
  b.disabled = true;
  return b;
}

// The header's main button into ctl, and what it waits for into note; s: the snapshot.
export function mainButton(ctl, note, s) {
  const center = s.center, phase = s.run.phase;
  note.textContent = (center && center.starting && !can("stop")) ? STARTING[center.starting] || "" : (center ? taskNote(center.task) || "" : "");
  if (S.ended) return;
  if (phase === "stopping") ctl.appendChild(waitingButton("Stopping…"));
  else if (can("stop")) ctl.appendChild(button("■ Stop", "btn danger", stop, "Stop NXSPOOF: firewall off, summary, Internet Sharing off."));
  else if (center && center.starting) ctl.appendChild(waitingButton("Starting…"));
  else if (center) {
    const b = button("▶ Start NXSPOOF", "btn primary", b => start({}, b),
      "Asks for your Mac password in macOS's own window, then starts NXSPOOF in the background. This page shows it.");
    b.disabled = !can("start_run");
    ctl.appendChild(b);
  }
  if (panel && !can("start_run")) panel = null;
  drawPanel();
}

// ■ Stop: asked first, then the run stops cleanly (the button waits until it has).
async function stop(b) {
  if (!confirm("Stop NXSPOOF?\n\nThe firewall is removed, the summary is written and Internet Sharing is turned off.")) return;
  b.disabled = true;
  try { toast((await action("stop")).message || "Stopping…"); }
  catch (e) { toast("Couldn't stop: " + e.message, true); b.disabled = false; }
}

// ▶ Start (payload {}, { fix: true } or { anyway: true }): not ready -> the panel; started -> the message.
async function start(payload, b) {
  b.disabled = true;
  try {
    const answer = await action("start_run", payload);
    panel = answer.not_ready ? { kind: "not-ready", items: answer.not_ready } : null;
    if (!panel) toast(answer.message);
    drawPanel();
    S.refresh();
  } catch (e) {
    toast("Couldn't start: " + e.message, true);
  }
  b.disabled = false;
}

// Draw the panel under the header (only when what it shows changed).
function drawPanel() {
  const box = document.getElementById("center-panel");
  const key = JSON.stringify(panel);
  if (!box || key === drawn) return;
  drawn = key;
  box.textContent = "";
  box.hidden = !panel;
  if (!panel) return;
  box.appendChild(el("b", null, "This Mac isn't ready for NXSPOOF yet:"));
  const list = el("ul");
  for (const item of panel.items) list.appendChild(el("li", null, item.title + ": " + item.casual));
  box.appendChild(list);
  const row = el("div", "panel-actions");
  const fixable = panel.items.every(item => item.fixable);
  if (fixable) row.appendChild(button("🛠️ Fix and start", "btn primary", b => start({ fix: true }, b)));
  row.appendChild(button("Open Configure", fixable ? "btn" : "btn primary", () => { panel = null; drawPanel(); showView("configure"); }));
  row.appendChild(button("Start anyway", "btn danger", b => start({ anyway: true }, b)));
  row.appendChild(button("Cancel", "btn", () => { panel = null; drawPanel(); }));
  box.appendChild(row);
}
