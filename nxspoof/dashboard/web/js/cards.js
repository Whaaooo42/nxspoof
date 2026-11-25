/*
  js/cards.js - the header (chips, the main button, ⚙️ Configure) and the console cards of the NXSPOOF dashboard:
  "Edit" and "Remove" for consoles in settings.json, "Add this console" for the ones not in it yet.
*/

"use strict";

import { S, COLORS, el, dur, hms, toast, action, can } from "./core.js";
import { mainButton } from "./start.js";

// #############################################################################################################################################################
// ##  HEADER                                                                                                                                                 ##
// #############################################################################################################################################################

// Header (D27, one row): the chips on the left (the hotspot, with how long NXSPOOF has run; the firewall, sharing, FTP
// watch), then the main button (js/start.js: ▶ Start or ■ Stop) and ⚙️ Configure on the right.
export function renderHeader(s) {
  const r = s.run;
  const chips = document.getElementById("chips");
  chips.textContent = "";
  const chip = (label, value, state) => {
    const c = el("span", "chip" + (state ? " " + state : ""));
    c.appendChild(document.createTextNode(label + " "));
    c.appendChild(el("b", null, value));
    chips.appendChild(c);
  };
  const idle = r.phase === "idle";
  const where = r.hotspot_down ? "DOWN, putting it back…" : r.hotspot_ip || (r.dry_run ? "dry run" : (idle ? "off" : "not up yet"));
  const since = r.started && !idle && !r.hotspot_down ? " · " + dur((r.now || Date.now() / 1000) - r.started) : "";
  chip("📡 Hotspot", (r.hotspot_name ? r.hotspot_name + " · " + where : where) + since, r.hotspot_down ? "off" : (r.hotspot_ip ? "on" : ""));
  chip("🛡 Firewall", r.firewall_loaded ? "ON" : (r.firewall ? (idle ? "on at start" : "pending") : "OFF"), r.firewall_loaded ? "on" : (r.firewall ? "" : "off"));
  chip("🌐 Sharing", r.auto_sharing ? "automatic" : "manual");
  chip("📁 FTP watch", r.ftp_watch ? "on" : "off");
  const ctl = document.getElementById("controls");
  ctl.textContent = "";
  mainButton(ctl, document.getElementById("head-note"), s);
  document.getElementById("footer").textContent =
    "Only visible on this Mac. The log file is always technical" + (r.log_file ? ": " + r.log_file : ".");
}

// #############################################################################################################################################################
// ##  CONSOLE CARDS                                                                                                                                          ##
// #############################################################################################################################################################

const openBlocked = new Set();   // cards whose "What was blocked" list you opened
const drafts = {};               // what you typed in the add forms, kept across the 2-second refreshes
const editing = {};              // consoles whose Edit form is open -> what you typed so far
const newConsole = { open: false, mac: "", name: "", model: "", owner: "", color: "yellow", err: "" };   // the "Add a console" form

// Copy text to the clipboard and flash the button.
function copyBtn(text) {
  const b = el("button", "btn", "Copy");
  b.type = "button";
  b.addEventListener("click", () => {
    const done = () => { b.textContent = "Copied"; setTimeout(() => b.textContent = "Copy", 1200); };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => {});
  });
  return b;
}

// Keep the browser's AutoFill (contacts, addresses) and spell checking out of a console field.
function noAutofill(input) {
  input.autocomplete = "off";
  input.setAttribute("autocorrect", "off");
  input.setAttribute("autocapitalize", "off");
  input.spellcheck = false;
  input.setAttribute("data-form-type", "other");
  input.name = "nxspoof-" + Math.random().toString(36).slice(2);     // a name no AutoFill rule recognizes
}

// A MAC typed or pasted in any form -> "aa:bb:cc:dd:ee:ff" so far (only hex digits kept, colons added).
export function maskMac(text) {
  const hex = String(text).replace(/[^0-9a-fA-F]/g, "").toLowerCase().slice(0, 12);
  return (hex.match(/.{1,2}/g) || []).join(":");
}
const MAC_RX = /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/;

// A labelled one-line field bound to a draft value (what you type is kept across refreshes).
function textField(f, label, d, key, placeholder, maxLength) {
  const l = el("label");
  l.appendChild(document.createTextNode(label));
  const i = el("input");
  i.value = d[key] === undefined || d[key] === null ? "" : d[key];
  i.placeholder = placeholder || ""; i.maxLength = maxLength || 40;
  noAutofill(i);
  i.addEventListener("input", () => { d[key] = i.value; });
  l.appendChild(i);
  f.appendChild(l);
  return i;
}

// A labelled several-line field (one note per line) bound to a draft value.
function notesField(f, label, d, key) {
  const l = el("label", "top");
  l.appendChild(document.createTextNode(label));
  const t = el("textarea");
  t.value = d[key]; t.rows = 3; t.placeholder = "one note per line";
  noAutofill(t);
  t.addEventListener("input", () => { d[key] = t.value; });
  l.appendChild(t);
  f.appendChild(l);
}

// A labelled color picker bound to a draft value.
function colorField(f, d) {
  const l = el("label");
  l.appendChild(document.createTextNode("Color"));
  const sel = el("select");
  for (const col of ["yellow", "cyan", "magenta", "green", "blue", "red", "white"]) {
    const o = el("option", null, col); o.value = col; if (col === d.color) o.selected = true; sel.appendChild(o);
  }
  sel.addEventListener("change", () => { d.color = sel.value; });
  l.appendChild(sel);
  f.appendChild(l);
}

// A labelled model picker (the server's list, state.models), bound to a draft value; a console's current text
// that isn't on the list is kept as "(current) ..." so nothing changes until you pick a model.
function modelField(f, d) {
  const l = el("label");
  l.appendChild(document.createTextNode("Model"));
  const sel = el("select");
  noAutofill(sel);
  const add = (value, text, title) => {
    const o = el("option", null, text); o.value = value; if (title) o.title = title;
    if (value === (d.model || "")) o.selected = true;
    sel.appendChild(o);
  };
  add("", "not set");
  const models = (S.lastState && S.lastState.models) || [];
  for (const m of models) add(m.name, m.name, m.detail);
  if (d.model && !models.some(m => m.name === d.model)) add(d.model, "(current) " + d.model, "not on the list: kept as it is");
  // The chosen model's details as the picker's hint.
  const hint = () => { const m = models.find(x => x.name === sel.value); sel.title = m ? m.detail : ""; };
  sel.addEventListener("change", () => { d.model = sel.value; hint(); });
  hint();
  l.appendChild(sel);
  f.appendChild(l);
}

// The form's last row: its buttons and the error message (if any).
function formEnd(f, d, buttons) {
  const end = el("div", "row-end");
  for (const b of buttons) end.appendChild(b);
  if (d.err) end.appendChild(el("span", "err", d.err));
  f.appendChild(end);
}

// A button (type "button" unless given).
function button(cls, text, type) {
  const b = el("button", cls, text);
  b.type = type || "button";
  return b;
}

// "Add this console" form for an unknown console: name, model, owner, color -> settings.json.
function addForm(c) {
  const d = drafts[c.mac] || (drafts[c.mac] = { name: "", model: "", owner: "", color: "yellow", err: "" });
  const f = el("form", "addform");
  f.appendChild(el("div", "title", "🙋 New console. Add it to settings.json?"));
  const nameIn = textField(f, "Name", d, "name", "e.g. Max's Switch");
  modelField(f, d);
  textField(f, "Owner", d, "owner", "e.g. Max");
  colorField(f, d);
  const btn = button("btn primary", "➕ Add to settings.json", "submit");
  formEnd(f, d, [btn]);
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!d.name.trim()) { d.err = "Please give it a name."; nameIn.focus(); renderCards(S.lastState, true); return; }
    btn.disabled = true;
    try {
      toast((await action("add_console", { mac: c.mac, name: d.name, model: d.model, owner: d.owner, color: d.color })).message);
      delete drafts[c.mac];
      if (document.activeElement) document.activeElement.blur();
      S.refresh();
    } catch (err) { d.err = err.message; btn.disabled = false; renderCards(S.lastState, true); }
  });
  return f;
}

// "Edit" form for a console in settings.json, filled with its current settings.
function editForm(c) {
  const d = editing[c.mac];
  const f = el("form", "addform editform");
  f.appendChild(el("div", "title", "✏️ Edit " + c.name + " (settings.json)"));
  const nameIn = textField(f, "Name", d, "name", "e.g. Max's Switch");
  modelField(f, d);
  textField(f, "Owner", d, "owner", "e.g. Max");
  colorField(f, d);
  textField(f, "FTP port", d, "ftp_port", "5000", 5);
  textField(f, "Serial", d, "serial", "optional");
  textField(f, "System version", d, "system_version", "optional");
  notesField(f, "Notes", d, "notes");
  const save = button("btn primary", "💾 Save", "submit");
  const cancel = button("btn", "Cancel");
  cancel.addEventListener("click", () => { delete editing[c.mac]; renderCards(S.lastState, true); });
  formEnd(f, d, [save, cancel]);
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!d.name.trim()) { d.err = "Please give it a name."; nameIn.focus(); renderCards(S.lastState, true); return; }
    save.disabled = true;
    const payload = Object.assign({ mac: c.mac }, d);
    delete payload.err;
    try {
      toast((await action("edit_console", payload)).message);
      delete editing[c.mac];
      if (document.activeElement) document.activeElement.blur();
      S.refresh();
    } catch (err) { d.err = err.message; save.disabled = false; renderCards(S.lastState, true); }
  });
  return f;
}

// "Add a console" form: any console by its MAC address, without waiting for it to connect.
function newConsoleForm() {
  const d = newConsole;
  const f = el("form", "addform editform");
  f.appendChild(el("div", "title", "➕ Add a console to settings.json"));
  f.appendChild(el("div", "set-help form-hint", "On the Switch: System Settings › Internet › System MAC Address."));
  const macIn = textField(f, "MAC", d, "mac", "e.g. 48:f1:eb:5a:22:c2", 17);
  macIn.classList.add("mono");
  macIn.addEventListener("input", () => { macIn.value = maskMac(macIn.value); d.mac = macIn.value; });
  const nameIn = textField(f, "Name", d, "name", "e.g. Max's Switch");
  modelField(f, d);
  textField(f, "Owner", d, "owner", "e.g. Max");
  colorField(f, d);
  const add = button("btn primary", "➕ Add", "submit");
  const cancel = button("btn", "Cancel");
  cancel.addEventListener("click", () => { d.open = false; d.err = ""; renderCards(S.lastState, true); });
  formEnd(f, d, [add, cancel]);
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!MAC_RX.test(d.mac)) { d.err = "A MAC has 12 hex digits (0-9, a-f), e.g. 48:f1:eb:5a:22:c2."; macIn.focus(); renderCards(S.lastState, true); return; }
    if (!d.name.trim()) { d.err = "Please give it a name."; nameIn.focus(); renderCards(S.lastState, true); return; }
    add.disabled = true;
    try {
      toast((await action("add_console", { mac: d.mac, name: d.name, model: d.model, owner: d.owner, color: d.color })).message);
      Object.assign(d, { open: false, mac: "", name: "", model: "", owner: "", color: "yellow", err: "" });
      if (document.activeElement) document.activeElement.blur();
      S.refresh();
    } catch (err) { d.err = err.message; add.disabled = false; renderCards(S.lastState, true); }
  });
  return f;
}

// The "➕ Add a console" button (opens the form above).
function newConsoleButton() {
  const b = button("btn add-console", "➕ Add a console");
  b.title = "Add any console by its MAC address, without waiting for it to connect";
  b.addEventListener("click", () => { newConsole.open = true; renderCards(S.lastState, true); });
  return b;
}

// "Edit" and "Remove" buttons for a console in settings.json.
function cardActions(c) {
  const row = el("div", "card-actions");
  if (can("edit_console")) {
    const edit = button("btn", "✏️ Edit");
    edit.addEventListener("click", () => {
      const st = c.settings || {};
      editing[c.mac] = { name: st.name || "", model: st.model || "", owner: st.owner || "", color: st.color || "white",
                         ftp_port: st.ftp_port || "", serial: st.serial || "", system_version: st.system_version || "",
                         notes: (st.notes || []).join("\n"), err: "" };
      renderCards(S.lastState, true);
    });
    row.appendChild(edit);
  }
  if (can("remove_console")) {
    const rm = button("btn danger", "🗑️ Remove");
    rm.addEventListener("click", async () => {
      if (!confirm("Remove " + c.name + " from settings.json?\n\nIts settings (model, owner, notes…) are deleted from the file." +
                   "\nIf it's connected now, its card stays as an unknown console, and you can add it back.")) return;
      rm.disabled = true;
      try { toast((await action("remove_console", { mac: c.mac })).message); S.refresh(); }
      catch (e) { toast("Couldn't remove it: " + e.message, true); rm.disabled = false; }
    });
    row.appendChild(rm);
  }
  return row;
}

// The Consoles heading with who's here: "Consoles · 1 here, 1 away, 1 not seen yet" ("Consoles (3)" with no run).
function renderTitle(s) {
  const here = s.consoles.filter(c => c.present).length;
  const away = s.consoles.filter(c => c.seen && !c.present).length;
  const unseen = s.consoles.length - here - away;
  const parts = [[here, "here"], [away, "away"], [unseen, "not seen yet"]].filter(p => p[0]).map(p => p[0] + " " + p[1]);
  document.getElementById("consoles-title").textContent = s.run.phase === "idle"
    ? "Consoles (" + s.consoles.length + ")" : "Consoles" + (parts.length ? " · " + parts.join(", ") : "");
}

// One card per console: status, address, time on the Wi-Fi, tests, FTP, blocked list.
export function renderCards(s, force) {
  renderTitle(s);
  const box = document.getElementById("cards");
  const a = document.activeElement;
  if (!force && a && box.contains(a) && /INPUT|SELECT|TEXTAREA/.test(a.tagName)) return;   // you're typing
  box.textContent = "";
  if (!s.consoles.length) box.appendChild(el("div", "empty", "No consoles in settings.json yet."));
  const now = s.run.now || Date.now() / 1000;
  for (const c of s.consoles) {
    const st = c.present ? (c.online ? "online" : "connected") : (c.seen ? "away" : "not seen yet");
    const card = el("div", "card " + (c.present ? "here" : (c.seen ? "away" : "unseen")));
    card.style.setProperty("--accent", COLORS[c.color] || "var(--green)");
    const head = el("div", "card-head");
    const dot = el("span", "dot");
    dot.style.background = COLORS[c.color] || "var(--faint)";
    head.appendChild(dot);
    head.appendChild(el("span", "cname", c.name));
    if (c.model) head.appendChild(el("span", "model", c.model));
    head.appendChild(el("span", "status " + st.replace(/ /g, "-"), st));
    card.appendChild(head);
    const rows = el("div", "rows");
    const row = (k, v) => { rows.appendChild(el("div", "k", k)); const d = el("div", "v"); if (v instanceof Node) d.appendChild(v); else d.textContent = v; rows.appendChild(d); return d; };
    row("MAC", c.mac).classList.add("mono");
    if (c.ip) row("Address", c.ip).classList.add("mono");
    if (c.joined.length) {
      let total = 0;
      c.joined.forEach((j, i) => { total += (c.left[i] || now) - j; });
      row("On Wi-Fi", c.present ? "since " + hms(c.joined[c.joined.length - 1]) + " · total " + dur(total)
                                : "left " + hms(c.left[c.left.length - 1] || now) + " · total " + dur(total));
    }
    if (c.seen) row("Online check", c.tests ? (c.tests === 1 ? "passed once" : "passed " + c.tests + " times") : "not passed yet");
    if (c.ftp && c.ftp.url) {
      const v = el("span", "ftp-value");
      v.appendChild(el("span", c.ftp.up ? "ftp-up" : "ftp-down", c.ftp.up ? "UP" : "not running"));
      v.appendChild(el("span", "mono", c.ftp.url));
      if (c.ftp.up) v.appendChild(copyBtn(c.ftp.url));
      if (c.ftp.up && can("open_filezilla") && s.run.filezilla) {
        const fz = el("button", "btn", "📂 Open in FileZilla");
        fz.type = "button";
        fz.addEventListener("click", async () => {
          fz.disabled = true;
          try { toast((await action("open_filezilla", { mac: c.mac })).message); }
          catch (e) { toast("Couldn't open FileZilla: " + e.message, true); }
          setTimeout(() => { fz.disabled = false; }, 2000);
        });
        v.appendChild(fz);
      }
      row("FTP", v);
    }
    if (c.seen) row("Blocked", c.blocked_total + (c.blocked_total === 1 ? " attempt" : " attempts"));
    card.appendChild(rows);
    if (c.blocked.length) {
      const d = el("details"), sum = el("summary", null, "What was blocked (" + c.blocked.length + (c.blocked.length === 1 ? " name)" : " names)"));
      d.open = openBlocked.has(c.mac);
      d.addEventListener("toggle", () => { if (d.open) openBlocked.add(c.mac); else openBlocked.delete(c.mac); });
      d.appendChild(sum);
      const ul = el("ul");
      for (const [name, n] of c.blocked) ul.appendChild(el("li", null, n + " × " + name));
      d.appendChild(ul);
      card.appendChild(d);
    }
    for (const ua of c.uas) card.appendChild(el("div", "ua", "User-Agent: " + ua));
    if (c.known && editing[c.mac]) card.appendChild(editForm(c));
    else if (c.known) card.appendChild(cardActions(c));
    if (!c.known && can("add_console")) card.appendChild(addForm(c));
    else if (!c.known && c.snippet) {
      const sn = el("div", "snippet");
      sn.appendChild(document.createTextNode("🙋 Not in settings.json yet. Paste into \"consoles\":\n"));
      sn.appendChild(el("span", null, c.snippet));
      sn.appendChild(copyBtn(c.snippet));
      card.appendChild(sn);
    }
    box.appendChild(card);
  }
  if (can("add_console")) box.appendChild(newConsole.open ? newConsoleForm() : newConsoleButton());
}
