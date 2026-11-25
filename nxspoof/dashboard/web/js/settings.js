/*
  js/settings.js - Configure's right column, "how NXSPOOF behaves", and its one Save (D26).

  Built entirely from the server's state.settings rows (nxspoof/settings/schema.py): one collapsible section per group
  (closed at first; a closed one sums up its values, and says how many changes wait in it), one row per setting with its
  help, its default, and whether a change applies now or at the next start. A setting that belongs to This Mac
  (ON_THIS_MAC: the bridge's address) is drawn on its check's line instead (js/mac.js, through settingInput).
  Changing something only marks it (a blue edge on the row): the Save slot in Configure's head (always there, and the
  head stays in view while you scroll) says how many changes wait, with Save
  (control "set_settings": every value checked, all or nothing, one log line per change) and Discard. Closing the page
  with changes waiting asks first. 📤 Export downloads settings.json; 📥 Import reads a file you pick, shows what it would
  change (control "import_settings", nothing written), then imports it on your OK (the old file is kept); only while
  nothing runs and nothing waits to be saved.
*/

"use strict";

import { S, el, toast, action, can, load, store } from "./core.js";
import { currentView } from "./views.js";

const ON_THIS_MAC = ["bridge.address"];      // drawn on This Mac's check lines, not in a section here
const SECTION_AS = { logs: "Advanced" };     // groups shown under another title (D30: what only a Terminal run needs is a --flag)
const ORDER = ["FTP watch", "Advanced"];     // the sections' order (any other comes after)
const drafts = {};          // setting name -> the value you chose, not saved yet
const errors = {};          // setting name -> why the last Save refused it
const rowsShown = {};       // setting name -> its row element (to mark it without redrawing)
const listeners = [];       // called when the waiting changes change (js/mac.js: Save and fix)
const savedListeners = [];  // called with the names just saved (js/mac.js: the checks again after a This Mac setting)
let importing = null;       // a file being imported: {name, text, summary, preview} until Import or Cancel
let drawnKey = null;        // what the column was last drawn from (redrawn only when that changes)
let open = new Set(JSON.parse(load("open-sections") || "[]"));

// A value in words: on/off for switches, the value itself otherwise.
function show(v) { return typeof v === "boolean" ? (v ? "on" : "off") : (v === "" ? "(empty)" : String(v)); }

// Same value? (numbers typed as text count as the number)
function same(a, b) { return JSON.stringify(a) === JSON.stringify(b) || String(a) === String(b); }

// Every setting row the server sent.
function allRows() { return S.lastState && S.lastState.settings ? S.lastState.settings : []; }

// One setting's row by its name (or undefined).
export function settingRow(name) { return allRows().find(r => r.name === name); }

// The value a row shows: what you chose, or what's saved.
function current(row) { return drafts[row.name] !== undefined ? drafts[row.name] : row.saved; }

// The settings with a change waiting to be saved.
export function waiting() { return Object.keys(drafts); }

// Call fn whenever the waiting changes change.
export function onWaiting(fn) { listeners.push(fn); }

// Call fn(names) after each Save, with the settings it saved.
export function onSaved(fn) { savedListeners.push(fn); }

// You chose a value: it waits to be saved (choosing the saved value again takes the change back).
function choose(row, value) {
  if (same(value, row.saved)) delete drafts[row.name]; else drafts[row.name] = value;
  delete errors[row.name];
  marks();
}

// The input for one setting (a switch, a list of choices, a number or a text field), wired to the waiting changes.
export function settingInput(row, editable) {
  const value = current(row);
  let input;
  if (row.kind === "bool") {
    input = el("input"); input.type = "checkbox"; input.checked = !!value;
    input.addEventListener("change", () => choose(row, input.checked));
  } else if (row.choices.length) {
    input = el("select");
    for (const c of row.choices) { const o = el("option", null, c); o.value = c; if (String(c) === String(value)) o.selected = true; input.appendChild(o); }
    input.addEventListener("change", () => choose(row, input.value));
  } else {
    input = el("input");
    input.type = row.kind === "text" ? "text" : "number";
    if (row.min !== null) input.min = row.min;
    if (row.max !== null) input.max = row.max;
    if (row.kind === "number") input.step = "any";
    input.value = value;
    input.addEventListener("input", () => choose(row, input.value));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") saveAll(); });
  }
  input.disabled = !editable;
  input.dataset.setting = row.name;
  return input;
}

// The control part of a row: the input, and ↺ Default when the value isn't the default (it waits to be saved too).
function control(row, editable) {
  const box = el("div", "set-ctl");
  box.appendChild(settingInput(row, editable));
  if (editable && !same(current(row), row.default)) {
    const b = el("button", "btn", "↺ Default");
    b.type = "button";
    b.title = "Back to the default: " + show(row.default) + " (then Save)";
    b.addEventListener("click", () => { choose(row, row.default); drawnKey = null; renderSettings(S.lastState); });
    box.appendChild(b);
  }
  return box;
}

// One setting: name and badges, the control, help and default, what this run uses if it differs, why Save refused it.
function settingLine(row, editable) {
  const r = el("div", "set-row");
  const head = el("div", "set-name", row.label);
  head.appendChild(el("span", "badge " + row.apply, row.apply === "live" ? "applies now" : "next start"));
  if (row.overridden_by) head.appendChild(el("span", "badge option", row.overridden_by + " for this run"));
  r.appendChild(head);
  r.appendChild(control(row, editable));
  r.appendChild(el("div", "set-help", row.help + " · default: " + show(row.default) + " · " + row.name));
  if (!same(row.saved, row.in_force)) {
    r.appendChild(el("div", "set-pending", "This run uses " + show(row.in_force) +
      (row.overridden_by ? " (" + row.overridden_by + " was given at start)." : " until the next start.")));
  }
  r.appendChild(el("div", "set-error"));
  rowsShown[row.name] = r;
  return r;
}

// Mark what waits to be saved (rows, section headers, the bar) and what Save refused, without redrawing anything.
function marks() {
  for (const [name, r] of Object.entries(rowsShown)) {
    r.classList.toggle("changed", drafts[name] !== undefined);
    r.classList.toggle("refused", !!errors[name]);
    r.querySelector(".set-error").textContent = errors[name] || "";
  }
  document.querySelectorAll("#configure [data-setting]").forEach(i => i.classList.toggle("changed", drafts[i.dataset.setting] !== undefined));
  document.querySelectorAll("#settings .set-sec").forEach(sec => {
    const count = sec.querySelectorAll(".set-row.changed").length;
    const tag = sec.querySelector(".sec-changed");
    tag.textContent = count ? count + " changed" : "";
    sec.classList.toggle("changed", count > 0);
  });
  saveBar();
  listeners.forEach(fn => fn(waiting().length));
}

// The Save slot in Configure's head (always there, so nothing on the page moves): "All saved", or how many changes wait,
// with Discard and Save (greyed while nothing waits).
function saveBar() {
  const bar = document.getElementById("savebar");
  const count = waiting().length;
  bar.className = count ? "waiting" : "";
  bar.textContent = "";
  bar.appendChild(el("span", "savebar-text", count ? count + " change" + (count === 1 ? "" : "s") + " not saved" : "✓ All saved"));
  const discard = el("button", "btn", "Discard");
  discard.type = "button";
  discard.addEventListener("click", discardAll);
  const save = el("button", "btn primary", "Save");
  save.type = "button";
  save.addEventListener("click", () => saveAll());
  discard.disabled = save.disabled = !count;
  bar.appendChild(discard);
  bar.appendChild(save);
}

// Forget every waiting change.
function discardAll() {
  for (const k of Object.keys(drafts)) delete drafts[k];
  for (const k of Object.keys(errors)) delete errors[k];
  document.querySelectorAll("#configure [data-setting]").forEach(input => {    // inputs outside this column too (This Mac's)
    const row = settingRow(input.dataset.setting);
    if (!row) return;
    if (input.type === "checkbox") input.checked = !!row.saved; else input.value = row.saved;
  });
  drawnKey = null;
  renderSettings(S.lastState);
  marks();
}

// Save every waiting change at once (the server checks them all first); true if saved, or nothing waited.
export async function saveAll() {
  if (!waiting().length) return true;
  try {
    const answer = await action("set_settings", { values: Object.assign({}, drafts) });
    for (const k of Object.keys(drafts)) delete drafts[k];
    for (const k of Object.keys(errors)) delete errors[k];
    toast(answer.message);
    await S.refresh();
    drawnKey = null;
    renderSettings(S.lastState);
    marks();
    savedListeners.forEach(fn => fn(answer.saved || []));
    return true;
  } catch (e) {
    const loose = [];
    for (const part of e.message.split("; ")) {
      const m = part.match(/^([a-z_]+\.[a-z_]+): (.*)$/);
      if (m && drafts[m[1]] !== undefined) errors[m[1]] = m[2]; else loose.push(part);
    }
    marks();
    toast("Not saved: " + (loose.length ? loose.join("; ") : "see the marked settings."), true);
    return false;
  }
}

// Download settings.json as it's saved now (the tidy layout).
async function exportSettings() {
  try {
    const r = await fetch("/api/data/settings_file", { cache: "no-store" });
    const file = await r.json();
    if (!r.ok) throw new Error(file.error || r.status);
    const link = el("a");
    link.href = URL.createObjectURL(new Blob([file.text], { type: "application/json" }));
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    toast("settings.json downloaded (no password is ever in it).");
  } catch (e) { toast("Couldn't export: " + e.message, true); }
}

// A file was picked: ask what importing it would change (nothing is written yet).
async function pickedFile(file) {
  if (!file) return;
  try {
    const text = await file.text();
    const answer = await action("import_settings", { text: text });
    importing = { name: file.name, text: text, summary: answer.summary, preview: answer.preview };
    drawnKey = null;
    S.refresh();
  } catch (e) { toast("Can't import " + file.name + ": " + e.message, true); }
}

// Import the picked file for real (the old settings are kept as settings.json.before-import-…).
async function confirmImport(b) {
  b.disabled = true;
  try {
    toast((await action("import_settings", { text: importing.text, apply: true })).message);
    importing = null;
    drawnKey = null;
    S.refresh();
  } catch (e) { toast("Not imported: " + e.message, true); b.disabled = false; }
}

// A button with its click handler.
function button(label, cls, onClick, title) {
  const b = el("button", cls, label);
  b.type = "button";
  if (title) b.title = title;
  b.addEventListener("click", onClick);
  return b;
}

// The Export / Import buttons (Configure's top right).
function transferButtons(box) {
  box.textContent = "";
  box.appendChild(button("📤 Export settings", "btn", exportSettings, "Download settings.json: your consoles and settings, in the tidy layout."));
  const input = el("input");
  input.type = "file"; input.accept = ".json,application/json"; input.hidden = true;
  input.addEventListener("change", () => { pickedFile(input.files[0]); input.value = ""; });
  const imp = button("📥 Import settings…", "btn", () => {
    if (waiting().length) { toast("Save or discard your changes first.", true); return; }
    input.click();
  }, "Pick a settings.json: you see what it changes before anything is written; your current settings are kept.");
  if (!can("import_settings")) { imp.disabled = true; imp.title = "Import is offered while NXSPOOF isn't running."; }
  box.appendChild(imp);
  box.appendChild(input);
}

// What importing the picked file changes, with Import and Cancel.
function importPreview(box) {
  box.textContent = "";
  if (!importing) return;
  const card = el("div", "card task-panel");
  card.appendChild(el("div", "task-head", "📥 Importing " + importing.name + ": " + importing.summary + "."));
  for (const w of importing.preview.warnings) card.appendChild(el("div", "task-step manual", "⚠️ " + w));
  card.appendChild(el("div", "set-help", "Your current settings are kept as settings.json.before-import-… in the data folder."));
  const row = el("div", "panel-actions");
  const ok = button("Import", "btn primary", () => confirmImport(ok));
  row.appendChild(ok);
  row.appendChild(button("Cancel", "btn", () => { importing = null; drawnKey = null; S.refresh(); }));
  card.appendChild(row);
  box.appendChild(card);
}

// One collapsible section: its title, a summary of its values while closed, how many changes wait in it, its rows.
function section(title, rows, editable) {
  const sec = el("details", "card set-sec");
  sec.open = open.has(title);
  const head = el("summary");
  head.appendChild(el("span", "sec-title", title));
  head.appendChild(el("span", "sec-sum", rows.map(r => r.label + " " + show(current(r))).join(" · ")));
  head.appendChild(el("span", "sec-changed"));
  sec.appendChild(head);
  for (const row of rows) sec.appendChild(settingLine(row, editable));
  sec.addEventListener("toggle", () => {
    if (sec.open) open.add(title); else open.delete(title);
    store("open-sections", JSON.stringify(Array.from(open)));
  });
  return sec;
}

// Draw Configure's head and right column, only when what they show changed (never while you type in a field).
export function renderSettings(s) {
  if (currentView() !== "configure" || !s || !s.settings) return;
  const editable = can("set_settings");
  if (!can("import_settings")) importing = null;
  const key = JSON.stringify([s.settings, editable, can("import_settings"), importing && importing.name]);
  const a = document.activeElement;
  if (key === drawnKey || (a && a.dataset && a.dataset.setting && /INPUT/.test(a.tagName) && a.type !== "checkbox")) return;
  drawnKey = key;
  const intro = document.getElementById("cfg-intro");
  intro.textContent = "";
  for (const line of ["Is this Mac ready, and how NXSPOOF behaves. Changes wait for Save; \"applies now\" ones reach a running NXSPOOF at once."]
                     .concat(editable ? [] : ["The dashboard was closed: these are the saved settings, read only."])) {
    intro.appendChild(el("div", null, line));
  }
  transferButtons(document.getElementById("cfg-transfer"));
  importPreview(document.getElementById("cfg-import"));
  const box = document.getElementById("settings");
  box.textContent = "";
  for (const k of Object.keys(rowsShown)) delete rowsShown[k];
  const groups = [];
  for (const row of s.settings.filter(r => !ON_THIS_MAC.includes(r.name))) {
    const title = SECTION_AS[row.section] || row.section_title;
    let g = groups.find(x => x.title === title);
    if (!g) { g = { title: title, rows: [] }; groups.push(g); }
    g.rows.push(row);
  }
  const rank = t => (ORDER.indexOf(t) + 1) || ORDER.length + 1;
  groups.sort((a, b) => rank(a.title) - rank(b.title));
  for (const g of groups) box.appendChild(section(g.title, g.rows, editable));
  marks();
}

// Closing or reloading the page with changes waiting asks first.
window.addEventListener("beforeunload", (e) => { if (waiting().length) { e.preventDefault(); e.returnValue = ""; } });
