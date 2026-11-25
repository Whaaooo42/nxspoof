/*
  js/report.js - 🐞 Report a problem (the header's link): the report NXSPOOF makes for a GitHub issue (app/reporting.py),
  shown first in a panel under the header, so whoever shares it reads it; then 📋 Copy, and 🔗 Copy and open GitHub (its
  new issue page, once NXSPOOF has a repository address). Names, MAC addresses and the Mac's name are already replaced
  by NXSPOOF (features/report.py); the text is shown as plain text, never as HTML.
*/

"use strict";

import { el, toast } from "./core.js";

let shown = null;        // the report on screen: { text, issue_url }, or null when the panel is closed

// A button with its click handler.
function button(label, cls, onClick) {
  const b = el("button", cls, label);
  b.type = "button";
  b.addEventListener("click", () => onClick(b));
  return b;
}

// The report selected on the page, then the browser's older copy command; true if that copied it.
function selectAndCopy() {
  const pre = document.querySelector("#report-panel .report-text");
  if (!pre) return false;
  const range = document.createRange();
  range.selectNodeContents(pre);
  const sel = getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  try { return document.execCommand("copy"); } catch (e) { return false; }
}

// Copy the report. Called right in the click (the clipboard is only allowed then): the browser's clipboard, else the
// older copy command, else the text stays selected for ⌘C. Returns a promise of true when it's copied.
function copy(text) {
  const said = (ok) => {
    toast(ok ? "📋 Copied: paste it into the issue." : "The browser didn't let me copy: the report is selected, press ⌘C.", !ok);
    return ok;
  };
  if (!navigator.clipboard) return Promise.resolve(said(selectAndCopy()));
  return navigator.clipboard.writeText(text).then(() => said(true), () => said(selectAndCopy()));
}

// Ask NXSPOOF for the report (full: the newest run's whole log too), then draw the panel.
async function load(full) {
  const box = document.getElementById("report-panel");
  box.hidden = false;
  box.textContent = "";
  box.appendChild(el("b", null, "🐞 Report a problem"));
  box.appendChild(el("p", "muted", "Making the report…"));
  try {
    const r = await fetch("/api/data/report" + (full ? "?full=1" : ""), { cache: "no-store" });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ("error " + r.status));
    shown = data;
    draw(full);
  } catch (e) {
    box.lastChild.textContent = "Couldn't make the report: " + e.message + ". In Terminal: python3 -B nx_spoof.py report";
  }
}

// The panel: what this is, the report itself, the choice of the whole log, and the buttons.
function draw(full) {
  const box = document.getElementById("report-panel");
  box.textContent = "";
  box.appendChild(el("b", null, "🐞 Report a problem"));
  box.appendChild(el("p", "muted", "This is what NXSPOOF would put in a GitHub issue: this Mac, its setup, and the last runs' " +
    "problems. Console names, MAC addresses, the hotspot's name, your name and your Mac's name are replaced. " +
    "Read it first, then add in your own words what happened."));
  const pick = el("label", "report-full");
  const tick = el("input");
  tick.type = "checkbox";
  tick.checked = !!full;
  tick.addEventListener("change", () => load(tick.checked));
  pick.append(tick, " Add the whole log of the last run (for the hard cases)");
  box.appendChild(pick);
  box.appendChild(el("pre", "report-text", shown.text));
  const row = el("div", "panel-actions");
  if (shown.issue_url) {
    row.appendChild(button("🔗 Copy and open GitHub", "btn primary", () => {
      copy(shown.text);                                          // started first, while the page has the focus...
      window.open(shown.issue_url, "_blank", "noopener");       // ...then the new tab, in the same click (no popup block)
    }));
    row.appendChild(button("📋 Copy", "btn", () => copy(shown.text)));
  } else {
    row.appendChild(button("📋 Copy", "btn primary", () => copy(shown.text)));
    row.appendChild(el("span", "muted", "No GitHub address in this copy of NXSPOOF yet: paste it wherever you ask for help."));
  }
  row.appendChild(button("Close", "btn", close));
  box.appendChild(row);
}

// Close the panel.
function close() {
  shown = null;
  const box = document.getElementById("report-panel");
  box.textContent = "";
  box.hidden = true;
}

// The header's 🐞 link opens (or closes) the panel.
export function initReport() {
  document.getElementById("report").addEventListener("click", (ev) => {
    ev.preventDefault();
    if (shown) close(); else load(false);
  });
}
