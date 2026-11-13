// Front page: filter the article list by board and by words, in the browser. Nothing is sent anywhere.
(function () {
  "use strict";

  var form = document.querySelector("form[data-filters]");
  var list = document.getElementById("latest-list");
  if (!form || !list) {
    return;
  }
  var input = document.getElementById("filter-q");
  var select = document.getElementById("filter-board");
  var status = document.getElementById("filter-status");
  var empty = document.getElementById("filter-empty");
  var rows = Array.prototype.slice.call(list.querySelectorAll(".lrow"));
  var months = Array.prototype.slice.call(list.querySelectorAll("[data-month]"));
  var cards = Array.prototype.slice.call(document.querySelectorAll(".scard"));

  form.hidden = false;

  function matches(el, board, terms) {
    if (board && el.getAttribute("data-board") !== board) {
      return false;
    }
    var text = el.getAttribute("data-text") || "";
    return terms.every(function (term) {
      return text.indexOf(term) !== -1;
    });
  }

  function apply() {
    var board = select.value;
    var terms = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    var filtering = Boolean(board) || terms.length > 0;
    // While filtering, the stories shown above the list are listed too, so none is missed.
    list.classList.toggle("filtering", filtering);
    var shown = 0;
    rows.forEach(function (row) {
      var ok = matches(row, board, terms);
      row.hidden = !ok;
      if (ok && (filtering || !row.classList.contains("is-top"))) {
        shown += 1;
      }
    });
    months.forEach(function (month) {
      var any = Array.prototype.some.call(month.querySelectorAll(".lrow"), function (row) {
        return !row.hidden && (filtering || !row.classList.contains("is-top"));
      });
      month.hidden = !any;
    });
    cards.forEach(function (card) {
      card.hidden = filtering;
    });
    var lead = document.querySelector(".lead");
    if (lead) {
      lead.hidden = filtering;
    }
    empty.hidden = !filtering || shown > 0;
    status.textContent = filtering ? shown + (shown === 1 ? " article" : " articles") : "";
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
  });
  input.addEventListener("input", apply);
  select.addEventListener("change", apply);
})();
