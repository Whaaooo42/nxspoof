/*
  js/runs.js - the log pane's run picker (control center only, D26): ● Live, or any run in logs/, newest first.

  The list comes from /api/data/runs (date, start, duration, consoles seen, the checklist's counts); a run from
  /api/data/run?name=... : its checklist (the same as menu 6 → 1) and its lines as the live log's own events, so the pane draws
  it exactly like the live log (casual or technical, either order; js/feed.js). Above them, the checklist folded into one
  line (● Live, at the top of the picker, goes back). The page always opens on the live log; when a run starts, it goes back
  to live by itself.
  Read only: nothing here changes a file.
*/

"use strict";

import { S, el, toast } from "./core.js";
import { renderFeed } from "./feed.js";

const ICONS = { pass: "✅", fail: "❌", skip: "⏭️" };
let runs = [];                // the list, newest first
let chosen = "";              // the run shown ("" = live)
let wasIdle = null;           // nothing ran at the last poll (null before the first one)

// Get JSON from the control center (throws with the reason).
async function getData(path) {
  const r = await fetch(path, { cache: "no-store" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ("error " + r.status));
  return data;
}

// One run as the picker says it: when, how long, who came, the checklist's counts.
function label(run) {
  const c = run.checks;
  const who = run.consoles.length ? run.consoles.map(x => x[0]).join(", ") : "no console";
  return run.date + " " + run.start + (run.dry_run ? " (dry run)" : "") + " · " + (run.duration || "—") + " · " + who +
         " · ✅ " + c.pass + (c.fail ? " ❌ " + c.fail : "");
}

// Read the list of runs again, and draw the picker's choices.
async function refreshList() {
  try { runs = await getData("/api/data/runs"); } catch (e) { return; }
  const pick = document.getElementById("run-pick");
  pick.textContent = "";
  const live = el("option", null, "● Live");
  live.value = "";
  pick.appendChild(live);
  for (const r of runs) { const o = el("option", null, label(r)); o.value = r.name; pick.appendChild(o); }
  pick.value = chosen;
}

// The checklist folded into one line (open it for each check and its evidence).
function drawSummary(box, d) {
  box.textContent = "";
  box.hidden = false;
  const count = s => d.items.filter(i => i.status === s).length;
  const pass = count("pass"), fail = count("fail"), skip = count("skip");
  const det = el("details", "run-sum");
  det.appendChild(el("summary", fail ? "bad" : null, (fail ? "❌ " : "✅ ") + pass + " of " + (pass + fail) + " checks passed" +
    (skip ? ", " + skip + " skipped" : "") + " · " + d.info.start + " → " + d.info.end + " · " + (d.info.duration || "") + " · " + d.info.stopped));
  for (const it of d.items) {
    const item = el("div", "check " + it.status);
    item.appendChild(el("div", "check-title", ICONS[it.status] + " " + it.title));
    for (const e of it.evidence) item.appendChild(el("div", "check-evidence", e));
    det.appendChild(item);
  }
  box.appendChild(det);
}

// Show a run in the log pane ("" = back to live).
export async function choose(name) {
  chosen = name || "";
  const pick = document.getElementById("run-pick");
  const title = document.getElementById("log-title");
  const summary = document.getElementById("run-summary");
  pick.value = chosen;
  if (!chosen) {
    S.past = null;
    title.textContent = "Live log";
    summary.hidden = true;
    summary.textContent = "";
    renderFeed();
    return;
  }
  try {
    const d = await getData("/api/data/run?name=" + encodeURIComponent(chosen));
    if (chosen !== name) return;                     // another choice came meanwhile
    S.past = { name: chosen, events: d.events };
    title.textContent = "Run of " + d.info.date + " " + d.info.start;
    drawSummary(summary, d);
    renderFeed();
  } catch (e) { toast("Couldn't read that run: " + e.message, true); choose(""); }
}

// Follow the state: the picker only with the control center; back to live when a run starts; the new run in the list when
// it ends.
export function updateRuns(s) {
  const center = !!(s && s.center);
  document.getElementById("run-pick").hidden = !center;
  if (!center) return;
  const idle = s.run.phase === "idle";
  if (wasIdle === null) {
    wasIdle = idle;
    refreshList();
    return;
  }
  if (wasIdle && !idle && chosen) choose("");
  if (!wasIdle && idle) refreshList();
  wasIdle = idle;
}

// Wire the picker.
export function initRuns() {
  const pick = document.getElementById("run-pick");
  pick.addEventListener("change", () => choose(pick.value));
}
