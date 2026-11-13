// Search page: filters the search index in the browser. Nothing is sent to a server.
(function () {
  "use strict";

  var form = document.querySelector("form.search-form[data-index]");
  if (!form) {
    return;
  }
  var input = document.getElementById("search-q");
  var status = document.getElementById("search-status");
  var list = document.getElementById("search-results");
  var indexUrl = form.getAttribute("data-index");
  var index = null;
  var MAX_RESULTS = 60;

  function haystack(record) {
    return [record.title, record.board, record.dek, (record.tags || []).join(" "), record.text, record.date]
      .join(" ")
      .toLowerCase();
  }

  function loadIndex() {
    if (index) {
      return Promise.resolve(index);
    }
    return fetch(indexUrl, { credentials: "omit" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("index " + response.status);
        }
        return response.json();
      })
      .then(function (data) {
        index = data.map(function (record) {
          record._haystack = haystack(record);
          return record;
        });
        return index;
      });
  }

  function render(matches, query) {
    list.textContent = "";
    matches.slice(0, MAX_RESULTS).forEach(function (record) {
      var item = document.createElement("li");
      item.className = "story card";

      var kicker = document.createElement("p");
      kicker.className = "kicker";
      kicker.textContent = record.board + " · " + record.date;
      item.appendChild(kicker);

      var heading = document.createElement("h3");
      heading.className = "story__title";
      var link = document.createElement("a");
      link.href = record.url;
      link.textContent = record.title;
      heading.appendChild(link);
      item.appendChild(heading);

      if (record.dek) {
        var dek = document.createElement("p");
        dek.className = "story__dek";
        dek.textContent = record.dek;
        item.appendChild(dek);
      }
      list.appendChild(item);
    });
    if (matches.length === 0) {
      status.textContent = 'No stories match "' + query + '".';
    } else if (matches.length > MAX_RESULTS) {
      status.textContent = "Showing the first " + MAX_RESULTS + " of " + matches.length + " stories for “" + query + "”.";
    } else {
      status.textContent = matches.length + (matches.length === 1 ? " story" : " stories") + " for “" + query + "”.";
    }
  }

  function run() {
    var query = input.value.trim().toLowerCase();
    if (!query) {
      list.textContent = "";
      status.textContent = "Search runs in your browser. Nothing you type is sent anywhere.";
      return;
    }
    status.textContent = "Searching…";
    loadIndex()
      .then(function (records) {
        var terms = query.split(/\s+/);
        var matches = records.filter(function (record) {
          return terms.every(function (term) {
            return record._haystack.indexOf(term) !== -1;
          });
        });
        render(matches, input.value.trim());
      })
      .catch(function () {
        list.textContent = "";
        status.textContent = "Search is unavailable right now. Browse the latest stories instead.";
      });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    run();
  });
  input.addEventListener("input", run);

  var params = new URLSearchParams(window.location.search);
  if (params.get("q")) {
    input.value = params.get("q");
    run();
  }
})();
