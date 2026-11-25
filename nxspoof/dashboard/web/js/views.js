/*
  js/views.js - the page's two views (D26): the dashboard (console cards and the log pane), and Configure (This Mac and
  how NXSPOOF behaves, js/mac.js and js/settings.js).

  The header's button steps into Configure (⚙️ Configure, with ✋ N when This Mac has something that needs you) and back
  (← Dashboard). The address says which view shows (#configure), so the browser's Back button and a reload keep it.
*/

"use strict";

import { el } from "./core.js";

let current = "dashboard";
let changed = () => {};       // called with the new view's name
let needs = 0;                // This Mac's "needs you" count, for the button

// The view showing now: "dashboard" or "configure".
export function currentView() { return current; }

// Show one view (and put it in the address, so Back and a reload keep it).
export function showView(name) {
  name = name === "configure" ? "configure" : "dashboard";
  const hash = name === "configure" ? "#configure" : "";
  if (location.hash !== hash) history.pushState(null, "", hash || location.pathname);
  apply(name);
}

// Draw the header's button as the view and the count say.
function drawButton() {
  const b = document.getElementById("view-switch");
  b.textContent = "";
  if (current === "configure") {
    b.appendChild(document.createTextNode("← Dashboard"));
    b.title = "Back to the consoles and the log.";
    return;
  }
  b.appendChild(document.createTextNode("⚙️ Configure"));
  if (needs) b.appendChild(el("span", "needs", "✋ " + needs));
  b.title = needs ? "This Mac has " + needs + " thing" + (needs === 1 ? "" : "s") + " that need you." : "This Mac, and how NXSPOOF behaves.";
}

// Switch to a view without touching the address.
function apply(name) {
  current = name;
  document.body.classList.toggle("view-configure", name === "configure");
  drawButton();
  changed(name);
}

// This Mac's "needs you" count changed (js/mac.js): the button shows ✋ N.
export function setNeeds(count) {
  if (count === needs) return;
  needs = count;
  drawButton();
}

// Wire the button and the address; onChange(name) runs whenever the view changes (to draw it).
export function initViews(onChange) {
  changed = onChange || changed;
  document.getElementById("view-switch").addEventListener("click", () => showView(current === "configure" ? "dashboard" : "configure"));
  window.addEventListener("popstate", () => apply(location.hash === "#configure" ? "configure" : "dashboard"));
  current = location.hash === "#configure" ? "configure" : "dashboard";
  document.body.classList.toggle("view-configure", current === "configure");
  drawButton();
}
