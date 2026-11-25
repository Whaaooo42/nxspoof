/*
  js/mac.js - Configure's left column, This Mac (control center only): doctor's checks from /api/data/mac, and fixing them.

  One line per requirement (settings.json, Python, Thunderbolt Bridge, the virtual cable, Internet Sharing, the hotspot,
  ports, macOS's firewall, FileZilla): ✅ fine, ✋ needs you (with what to do), ℹ️ good to know. What setup can fix has a tick
  box (ticked): 🛠️ Fix selected asks for your password once, in macOS's own window, then setup runs with no
  Terminal window and each step shows above the checks as it's done (js/tasks.js). ↩ Undo setup… asks how (before NXSPOOF, or
  macOS's defaults), here first, then the same way. (No Unplug: a Stop and 🧹 Clean up take the cable out.) The hotspot is macOS's: its line
  has Open System Settings → Sharing (NXSPOOF only reads it). The bridge's address is chosen on the bridge's line (a
  setting, ON_THIS_MAC in js/settings.js): it waits for Save like the others, and 🛠️ becomes "Save and fix" (saved first,
  then the bridge fixed with it). While NXSPOOF runs the column is one line. The header's ⚙️ Configure shows how many
  checks need you (js/views.js). The Terminal way (the setup
  windows) is in menu.command. Not while NXSPOOF runs, starts, or another task runs.
*/

"use strict";

import { S, el, toast, can } from "./core.js";
import { BUSY, runTask, sharingButton, taskPanel } from "./tasks.js";
import { onSaved, onWaiting, saveAll, settingInput, settingRow, waiting } from "./settings.js";
import { setNeeds } from "./views.js";

const ICONS = { ok: "✅", fixable: "✋", manual: "✋", info: "ℹ️" };
let shownTask = "null";      // the task box as last drawn (redrawn only when the task changed)
let wasBusy = false;         // a task was under way at the last poll (when it ends, the checks are run again)
let wasRunning = null;       // a run was active at the last poll (when that changes, the column is drawn again)
let canFix = false;          // the last checks had something setup can fix (🛠️ then works without a This Mac change)
const STEP_OF = { "bridge.address": "bridge" };   // a This Mac setting -> the setup step that applies it

// A button with its click handler.
function button(label, cls, onClick, title) {
  const b = el("button", cls, label);
  b.type = "button";
  if (title) b.title = title;
  b.addEventListener("click", onClick);
  return b;
}

// The steps to fix: the ticked ones, and the step of each This Mac setting waiting to be saved (in the checks' order).
function ticked() {
  const steps = Array.from(document.querySelectorAll("#mac .mac-fix:checked")).map(b => b.value);
  for (const name of waiting()) if (STEP_OF[name] && !steps.includes(STEP_OF[name])) steps.push(STEP_OF[name]);
  return steps;
}

// True when a This Mac setting waits to be saved (then 🛠️ saves it first, and fixes its step).
function macChange() { return waiting().some(n => STEP_OF[n]); }

// 🛠️'s words: "Save and fix" while changes wait to be saved and there's something to fix (else it can't do anything).
function fixLabel() { return waiting().length && (canFix || macChange()) ? "🛠️ Save and fix" : "🛠️ Fix selected"; }

// The buttons under the checks: fix the ticked ones, undo setup, to macOS's defaults (or why they're missing).
// The Terminal way (the setup windows, asking before each change) is menu.command's s, f and u.
function setupButtons(fixable) {
  canFix = fixable > 0;
  const box = el("div");
  const row = el("div", "mac-actions");
  row.appendChild(button("↻ Check again", "btn", renderMac));
  box.appendChild(row);
  if (!can("mac_task")) {
    row.appendChild(el("span", "set-help", "Setup isn't offered while NXSPOOF runs, starts, or another task runs."));
    return box;
  }
  const fix = button(fixLabel(), fixable || macChange() ? "btn primary" : "btn", async () => {
    const steps = ticked();
    if (!steps.length) { toast("Tick at least one thing to fix.", true); return; }
    if (waiting().length && !(await saveAll())) return;
    runTask("setup", { steps: steps });
  }, "One password in macOS's window, then setup fixes what you ticked, with no Terminal window (changes waiting are saved first).");
  fix.id = "mac-fix-button";
  fix.disabled = !fixable && !macChange();
  row.appendChild(fix);
  const choice = el("div", "card undo-choice");
  choice.hidden = true;
  row.appendChild(button("↩ Undo setup…", "btn danger", () => { choice.hidden = !choice.hidden; },
    "Put this Mac back: you choose how, next."));
  if (!fixable) row.appendChild(el("span", "set-help", "Nothing setup can fix right now."));
  box.appendChild(undoChoice(choice));
  return box;
}

// Undo setup's choice (shown under the buttons): back to how this Mac was before NXSPOOF, or macOS's defaults. Either way
// the virtual cable goes and Internet Sharing goes off; your settings and logs stay. macOS asks for your password next.
function undoChoice(box) {
  box.appendChild(el("div", "task-head", "↩ Undo setup: put this Mac back how?"));
  box.appendChild(el("div", "set-help", "Either way the virtual cable goes and Internet Sharing goes off; your settings and logs " +
    "stay, and you can set up again any time. macOS asks for your password next."));
  const row = el("div", "panel-actions");
  row.appendChild(button("Back to how it was before NXSPOOF", "btn danger", () => runTask("undo"),
    "Thunderbolt Bridge as NXSPOOF found it (automatic if it has no record)."));
  row.appendChild(button("Back to macOS's defaults", "btn danger", () => runTask("undo_defaults"),
    "Thunderbolt Bridge on automatic, whatever it was before."));
  row.appendChild(button("Cancel", "btn", () => { box.hidden = true; }));
  box.appendChild(row);
  return box;
}

// One check's line: its tick box when setup can fix it, the bridge's address, the hotspot's System Settings button.
function checkRow(row) {
  const line = el("div", "mac-row " + row.state);
  const text = el("div", "mac-text");
  const title = el("div", "mac-title");
  if (row.fixable && can("mac_task")) {
    const box = el("input", "mac-fix");
    box.type = "checkbox"; box.value = row.name; box.checked = true;
    box.title = "Let setup fix this";
    title.appendChild(box);
  }
  title.appendChild(document.createTextNode(ICONS[row.state] + " " + row.title + ": " + row.casual));
  text.appendChild(title);
  text.appendChild(el("div", "set-help", row.tech));
  for (const e of row.explain) text.appendChild(el("div", "mac-explain", e));
  line.appendChild(text);
  const setting = settingRow(Object.keys(STEP_OF).find(n => STEP_OF[n] === row.name) || "");
  if (setting) {
    const input = settingInput(setting, can("set_settings"));
    input.title = setting.help + " (waits for Save, or 🛠️ Save and fix)";
    line.appendChild(input);
  }
  if (row.name === "hotspot" && can("open_sharing_settings")) {
    const b = sharingButton();
    b.textContent = "System Settings → Sharing";
    b.title = "The hotspot is macOS's: its name, channel and password are changed there (the ⓘ next to Internet Sharing, then " +
              "Wi-Fi Options…). If the form shows an old name, quit System Settings (⌘Q) and open it again.";
    line.appendChild(b);
  }
  return line;
}

// While changes wait to be saved, 🛠️ says "Save and fix" (and offers itself for a This Mac setting).
onWaiting(() => {
  const b = document.getElementById("mac-fix-button");
  if (!b) return;
  b.textContent = fixLabel();
  b.disabled = !canFix && !macChange();
  b.className = canFix || macChange() ? "btn primary" : "btn";
});

// A This Mac setting was saved: run the checks again (a task about to start redraws them anyway).
onSaved(names => { if (names.some(n => STEP_OF[n]) && can("mac_task")) renderMac(); });

// Draw the task's box above the checks (when it changed); when a task or a run has just ended, run the checks again.
export function updateMac(s) {
  const running = !!(s && s.center && s.center.run);
  if (wasRunning !== null && running !== wasRunning) { wasRunning = running; renderMac(); return; }
  wasRunning = running;
  const task = s && s.center ? s.center.task : null;
  const busy = !!(task && BUSY.includes(task.phase));
  const slot = document.getElementById("mac-task");
  const key = JSON.stringify(task);
  if (slot && key !== shownTask) {
    shownTask = key;
    slot.textContent = "";
    if (task) slot.appendChild(taskPanel(task));
  }
  if (wasBusy && !busy) { wasBusy = false; renderMac(); return; }
  if (busy && !wasBusy) {                       // a task just began: its buttons and tick boxes wait (↻ Check again stays)
    document.querySelectorAll("#mac .mac-actions button, #mac .mac-row button, #mac .mac-fix").forEach(b => {
      if (b.textContent !== "↻ Check again") b.disabled = true;
    });
  }
  wasBusy = busy;
}

// Draw the This Mac column (the checks take a second or two: networksetup, the firewall's state...); one line while a run
// is active, or where there are no checks (a run's own dashboard, started from Terminal).
export async function renderMac() {
  const box = document.getElementById("mac");
  box.textContent = "";
  const center = S.lastState && S.lastState.center;
  if (!center || center.run) {
    setNeeds(0);                                // no checks while a run is active: no stale ✋ (they come back when it ends)
    box.appendChild(el("div", "card set-help", center ? "Setup waits until NXSPOOF stops: the checks and fixes come back then."
      : "This Mac's checks are on the dashboard NXSPOOF.app opens (this one belongs to a run started from Terminal)."));
    return;
  }
  const slot = el("div");
  slot.id = "mac-task";
  box.appendChild(slot);
  shownTask = "null";
  updateMac(S.lastState);
  const card = el("div", "card mac-card");
  card.appendChild(el("div", "empty", "Checking…"));
  box.appendChild(card);
  const buttons = setupButtons(0);
  box.appendChild(buttons);
  try {
    const r = await fetch("/api/data/mac", { cache: "no-store" });
    const rows = await r.json();
    if (!r.ok) throw new Error(rows.error || r.status);
    card.textContent = "";
    for (const row of rows) card.appendChild(checkRow(row));
    const todo = rows.filter(r => r.state === "fixable" || r.state === "manual").length;
    setNeeds(todo);
    const fixable = rows.filter(r => r.fixable).length;
    card.appendChild(el("div", "mac-verdict", todo ? "✋ Not ready yet: " + todo + " thing" + (todo === 1 ? "" : "s") + " to fix" +
      (fixable ? ": tick what setup should fix, then press 🛠️ Fix selected." : ".") : "✅ This Mac is ready for NXSPOOF."));
    box.replaceChild(setupButtons(fixable), buttons);
  } catch (e) { card.textContent = ""; card.appendChild(el("div", "err", "Couldn't check: " + e.message)); }
}
