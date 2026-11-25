/*
  js/tasks.js - This Mac's tasks that need your password (control "mac_task", app/tasks.py), and the crash banner.

  A task (setup, undo, cleanup) asks with macOS's own password window, then runs with no Terminal
  window; the snapshot's center.task says where it is (password, running, done, cancelled, failed) and each step as
  it's written (from the task's records). taskPanel() draws it in Configure's This Mac column; taskNote() is the header's line.
  The banner under the header (center.leftovers) says what a crash may have left behind, with 🧹 Clean up.
*/

"use strict";

import { el, toast, action, can } from "./core.js";

export const BUSY = ["password", "running"];
const ICONS = { ok: "✅", fixed: "🔧", fixable: "✋", manual: "✋", info: "ℹ️", skipped: "⏭️", undone: "🧹", done: "🧹", failed: "✋" };
let banner = "";            // the banner as last drawn (redrawn only when it changes)
let dismissed = "";         // the findings "Not now" was pressed for (shown again only if they change)

// Start a task (kind, plus the steps for setup); the answer is a toast, the rest shows in the snapshot.
export async function runTask(kind, extra) {
  try { toast((await action("mac_task", Object.assign({ kind: kind }, extra || {}))).message); } catch (e) { toast(e.message, true); }
}

// The header's line while a task isn't over (or null).
export function taskNote(task) {
  if (!task || !BUSY.includes(task.phase)) return null;
  return task.phase === "password" ? "🔑 To " + task.doing + ", type your password in the macOS window…" : "⏳ " + cap(task.doing) + "…";
}

// "set up this Mac" -> "Set up this Mac".
function cap(text) { return text.charAt(0).toUpperCase() + text.slice(1); }

// The task's box for Configure's This Mac column: where it is, each step, its log.
export function taskPanel(task) {
  const box = el("div", "card task-panel " + task.phase);
  const head = { password: "🔑 Type your Mac password in the macOS window to " + task.doing + ".",
                 running: "⏳ " + cap(task.doing) + "… (no Terminal window; each step shows here as it's done)",
                 done: task.todo ? "✋ " + cap(task.doing) + ": done, but something still needs you (below)." : "✅ " + cap(task.doing) + ": done.",
                 cancelled: "✋ Cancelled: nothing was changed.",
                 failed: "😕 Couldn't " + task.doing + ": " + task.message + "." }[task.phase] || task.phase;
  box.appendChild(el("div", "task-head", head));
  for (const s of task.steps) {
    box.appendChild(el("div", "task-step " + s.state, (ICONS[s.state] || "•") + " " + s.title + ": " + s.says));
    if (s.step === "hotspot" && (s.state === "manual" || s.state === "info") && can("open_sharing_settings")) box.appendChild(sharingButton());
  }
  if (task.log) box.appendChild(el("div", "set-help", "Its full log: " + task.log + " in the data folder."));
  return box;
}

// "Open System Settings → Sharing", for the hotspot's name, channel and password (also on This Mac's hotspot line).
export function sharingButton() {
  const b = el("button", "btn", "Open System Settings → Sharing");
  b.type = "button";
  b.addEventListener("click", async () => {
    try { toast((await action("open_sharing_settings")).message); } catch (e) { toast(e.message, true); }
  });
  return b;
}

// The banner under the header: what a crash may have left behind (center.leftovers), with 🧹 Clean up and Not now.
export function renderLeftovers(s) {
  const box = document.getElementById("leftovers");
  const found = (s && s.center && s.center.leftovers) || [];
  const task = s && s.center ? s.center.task : null;
  const cleaning = !!(task && task.kind === "cleanup" && BUSY.includes(task.phase));
  const key = JSON.stringify(found) + (can("mac_task") ? "+" : "-") + (cleaning ? task.phase : "");
  const show = found.length > 0 && JSON.stringify(found) !== dismissed;
  if (!box || key + show === banner) return;
  banner = key + show;
  box.textContent = "";
  box.hidden = !show;
  if (!show) return;
  box.appendChild(el("b", null, "⚠️ Something may have been left on after the last run"));
  const list = el("ul");
  for (const f of found) list.appendChild(el("li", null, f.text));
  box.appendChild(list);
  if (cleaning) { box.appendChild(el("div", "starting-note", taskNote(task))); return; }
  const row = el("div", "panel-actions");
  const clean = el("button", "btn primary", "🧹 Clean up");
  clean.type = "button";
  clean.title = "Firewall rules out, Internet Sharing off, virtual cable out: asks for your password in macOS's window (the same as menu 7 → 4).";
  clean.disabled = !can("mac_task");
  clean.addEventListener("click", () => runTask("cleanup"));
  const later = el("button", "btn", "Not now");
  later.type = "button";
  later.addEventListener("click", () => { dismissed = JSON.stringify(found); renderLeftovers(s); });
  row.appendChild(clean);
  row.appendChild(later);
  box.appendChild(row);
}
