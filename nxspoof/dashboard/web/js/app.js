/*
  js/app.js - starts the NXSPOOF dashboard page and keeps it up to date.

  Talks only to the NXSPOOF that served the page (this Mac):
    /api/state   polled every 2 s     -> header + console cards (cards.js), Configure's settings (settings.js)
    /api/data/*  when needed          -> the log pane's runs (runs.js), This Mac's checks (mac.js: at load, when Configure
                                         opens, after a task or a run), control center only
    /api/history at load, /api/events (live stream) -> the live log (feed.js)
  Two views (views.js, D26): the dashboard, and Configure (#configure).
    /api/action/<name>                -> the controls (core.js, needs this run's token); This Mac's tasks and the
                                         crash banner (tasks.js)
*/

"use strict";

import { S, load } from "./core.js";
import { renderFeed, addEvent, setView, setOrder, timeOrder } from "./feed.js";
import { renderHeader, renderCards } from "./cards.js";
import { renderSettings } from "./settings.js";
import { initRuns, updateRuns } from "./runs.js";
import { renderMac, updateMac } from "./mac.js";
import { renderLeftovers } from "./tasks.js";
import { initViews } from "./views.js";
import { initReport } from "./report.js";

// Ask for the current state; show a banner if NXSPOOF is gone.
async function poll() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    S.lastState = s;
    if (!S.view) setView(load("view") || "casual");
    renderHeader(s);
    renderCards(s);
    renderSettings(s);
    renderLeftovers(s);
    updateRuns(s);
    updateMac(s);
    if (S.ended) showEnded(); else document.getElementById("banner").style.display = "none";
  } catch (e) {
    const b = document.getElementById("banner");
    b.textContent = S.ended ? "NXSPOOF has stopped. This page shows the last state of the run."
      : S.lastState && S.lastState.center ? "Can't reach the dashboard any more: it was closed or stopped. Open NXSPOOF.app again: " +
        "a run that's still going shows up there, and if something was left on, it offers 🧹 Clean up."
      : "Can't reach NXSPOOF. It may have stopped.";
    b.style.display = "block";
    document.getElementById("controls").textContent = "";
  }
}

// Draw the view that was just picked (Configure runs This Mac's checks again when it opens).
function drawView(name) {
  if (name !== "configure") return;
  renderMac();
  if (S.lastState) renderSettings(S.lastState);
}

// The run (or the control center) is over: say so clearly, freeze and dim the cards.
function showEnded() {
  const b = document.getElementById("banner");
  const center = S.lastState && S.lastState.center;
  b.textContent = center
    ? (center.run ? "The dashboard was closed; NXSPOOF keeps running. Open NXSPOOF again to see it and stop it."
      : "The dashboard was closed. Open NXSPOOF again whenever you need it.")
    : "This NXSPOOF run has ended. Showing its final state (console statuses are as of the stop).";
  b.style.display = "block";
  document.getElementById("cards").classList.add("ended");
  document.getElementById("controls").textContent = "";
  document.getElementById("leftovers").hidden = true;
  document.querySelectorAll("#cards .addform, #cards .btn").forEach(n => n.remove());
  if (S.lastState) renderSettings(S.lastState);      // read only from now on
}

// Load recent events, then follow the live stream.
async function start() {
  S.refresh = poll;
  initViews(drawView);
  initRuns();
  initReport();
  document.getElementById("btn-technical").addEventListener("click", () => setView("technical"));
  document.getElementById("btn-casual").addEventListener("click", () => setView("casual"));
  document.getElementById("btn-oldest").addEventListener("click", () => setOrder("oldest"));
  document.getElementById("btn-newest").addEventListener("click", () => setOrder("newest"));
  setOrder(load("order") || "oldest");
  await poll();
  renderMac();                                   // the checks, for ⚙️ Configure's ✋ count (and the column, if it shows)
  if (S.lastState) renderSettings(S.lastState);
  try {
    const h = await fetch("/api/history", { cache: "no-store" });
    if (h.ok) { S.events = timeOrder(await h.json()); renderFeed(); }      // in time order (a relayed run's history arrives late)
  } catch (e) {}
  const es = new EventSource("/api/events");
  es.onmessage = (m) => { try { addEvent(JSON.parse(m.data)); } catch (e) {} };
  es.addEventListener("bye", () => { S.ended = true; es.close(); showEnded(); });
  setInterval(() => { if (!S.ended) poll(); }, 2000);
}
start();
