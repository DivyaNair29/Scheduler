/* Activity Log — the append-only audit trail (/api/log), filterable by kind,
   grouped by day. Every constraint, override, confirmation, assignment, and
   sync the app writes lands here. */
(function (window, document) {
  "use strict";

  var KINDS = [
    { key: "", label: "All" },
    { key: "constraint", label: "Constraints" },
    { key: "approval", label: "Approvals" },
    { key: "confirm", label: "Confirmations" },
    { key: "assign", label: "Assignments" },
    { key: "override", label: "Overrides" },
    { key: "schedule", label: "Schedule" },
    { key: "manpower", label: "Manpower" },
    { key: "sync", label: "Sync" },
  ];
  var KIND_COLOR = {
    constraint: "#d98218", approval: "#2f9e78", confirm: "#3a86c8",
    assign: "#8e44ad", override: "#c0453b", schedule: "#5878b5",
    manpower: "#159a8c", sync: "#9aa7b2", order: "#3a86c8",
  };

  var activeKind = "";
  var activeOrder = "";

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function fmtDay(iso) {
    var d = new Date(iso);
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }
  function fmtTime(iso) {
    var d = new Date(iso);
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  async function load(mount) {
    var body = mount.querySelector("[data-slot='log-body']");
    body.innerHTML = '<p class="muted">Loading…</p>';
    var url = "/api/log?limit=200" + (activeKind ? "&kind=" + activeKind : "");
    var events;
    try { events = await fetch(url).then(function (r) { return r.json(); }); }
    catch (e) { body.innerHTML = '<p class="muted">Could not load the log.</p>'; return; }

    // order-number filter (client-side; matches the code in title or detail)
    if (activeOrder) {
      var needle = activeOrder.toUpperCase();
      events = events.filter(function (e) {
        return ((e.title || "") + " " + (e.detail || "")).toUpperCase().indexOf(needle) > -1;
      });
    }

    if (!events.length) {
      body.innerHTML = '<p class="muted sm">No activity' +
        (activeKind ? " of this kind" : "") +
        (activeOrder ? " for " + activeOrder : "") + ".</p>";
      return;
    }

    // group by day
    var days = [];
    var map = {};
    events.forEach(function (e) {
      var day = fmtDay(e.ts);
      if (!map[day]) { map[day] = []; days.push(day); }
      map[day].push(e);
    });

    body.innerHTML = "";
    days.forEach(function (day, di) {
      // each day is a collapsible dropdown; the most recent day starts open
      var det = document.createElement("details");
      det.className = "log-day-group";
      if (di === 0) det.open = true;
      var sum = document.createElement("summary");
      sum.className = "log-day";
      sum.innerHTML = '<span class="log-day__chev">\u25be</span>' +
        '<span class="log-day__label">' + day + "</span>" +
        '<span class="log-day__count">' + map[day].length + "</span>";
      det.appendChild(sum);
      var list = el("div", "log-list");
      map[day].forEach(function (e) {
        var color = KIND_COLOR[e.kind] || "#9aa7b2";
        var row = el("div", "log-row");
        row.innerHTML =
          '<div class="log-time">' + fmtTime(e.ts) + "</div>" +
          '<div class="log-dot" style="background:' + color + '"></div>' +
          '<div class="log-main">' +
            '<div class="log-title">' + e.title + "</div>" +
            (e.detail ? '<div class="log-detail">' + e.detail + "</div>" : "") +
            '<div class="log-meta">' +
              '<span class="log-kind" style="color:' + color + '">' + e.kind + "</span>" +
              (e.actor ? ' · ' + e.actor : "") +
              (e.role ? ' <span class="log-role">' + e.role + "</span>" : "") +
            "</div>" +
          "</div>";
        list.appendChild(row);
      });
      det.appendChild(list);
      body.appendChild(det);
    });
  }

  async function render() {
    var mount = document.querySelector("[data-slot='activity-mount']");
    if (!mount) return;

    // filter chips
    var filters = el("div", "log-filters");
    KINDS.forEach(function (k) {
      var b = el("button", "log-chip" + (k.key === activeKind ? " is-on" : ""), k.label);
      b.addEventListener("click", function () {
        activeKind = k.key;
        filters.querySelectorAll(".log-chip").forEach(function (c) { c.classList.remove("is-on"); });
        b.classList.add("is-on");
        load(mount);
      });
      filters.appendChild(b);
    });

    // order-number filter
    var orderFilter = el("div", "log-orderfilter");
    var input = document.createElement("input");
    input.type = "text";
    input.className = "log-orderinput";
    input.placeholder = "Filter by order no. (e.g. SO-1044)";
    input.value = activeOrder;
    var clearBtn = el("button", "log-orderclear" + (activeOrder ? "" : " is-hidden"), "\u00d7");
    var _t = null;
    input.addEventListener("input", function () {
      activeOrder = input.value.trim();
      clearBtn.classList.toggle("is-hidden", !activeOrder);
      clearTimeout(_t);
      _t = setTimeout(function () { load(mount); }, 220);
    });
    clearBtn.addEventListener("click", function () {
      activeOrder = ""; input.value = ""; clearBtn.classList.add("is-hidden"); load(mount);
    });
    orderFilter.appendChild(input);
    orderFilter.appendChild(clearBtn);

    var card = el("div", "card");
    card.appendChild(filters);
    card.appendChild(orderFilter);
    card.appendChild(el("div", "log-body", null));
    card.querySelector(".log-body").setAttribute("data-slot", "log-body");
    mount.innerHTML = "";
    mount.appendChild(card);
    load(mount);
  }

  if (document.readyState !== "loading") render();
  else document.addEventListener("DOMContentLoaded", render);
})(window, document);
