// NXSPOOF docs - the one script of the docs pages (no inline scripts: the dashboard's safety rules; it also works on file://).
// A search box (<input class="search" data-filter="ID">) filters the .sym cards inside the element with that id: every word
// typed must appear in a card; a section (<section>) left without a visible card hides too. Nothing is turned into HTML.

// Set up every search box on the page.
function setUpSearch() {
  document.querySelectorAll("input.search[data-filter]").forEach(function (box) {
    var area = document.getElementById(box.getAttribute("data-filter"));
    var count = document.getElementById(box.getAttribute("data-count") || "");
    if (!area) { return; }
    var cards = Array.prototype.slice.call(area.querySelectorAll(".sym"));
    box.addEventListener("input", function () { filter(box.value, area, cards, count); });
    filter(box.value, area, cards, count);
  });
}

// Show the cards holding every word of the query; hide the sections left empty; say how many are shown.
function filter(query, area, cards, count) {
  var words = query.toLowerCase().split(/\s+/).filter(Boolean);
  var shown = 0;
  cards.forEach(function (card) {
    var text = card.textContent.toLowerCase();
    var hit = words.every(function (w) { return text.indexOf(w) >= 0; });
    card.hidden = !hit;
    shown += hit ? 1 : 0;
  });
  area.querySelectorAll("section").forEach(function (section) {
    section.hidden = !section.querySelector(".sym:not([hidden])");
  });
  if (count) { count.textContent = words.length ? shown + " of " + cards.length : cards.length + " problems"; }
}

document.addEventListener("DOMContentLoaded", setUpSearch);
