/* Orders — filterable register of every order across all phases. */
(function (window, document) {
  "use strict";
  const MU = window.MU;

  let filterPhase = "all";
  let query = "";

  function render(store) {
    const colorOf = {}, phaseColorOf = {};
    store.data.lines.forEach(function (l) { colorOf[l.code] = l.color; });
    store.data.phases.forEach(function (p) { phaseColorOf[p.key] = p.color; });

    // filters
    const filterHost = document.querySelector("[data-slot='phase-filters']");
    if (!filterHost.childElementCount) {
      const options = [{ key: "all", label: "All phases" }].concat(
        store.data.phases.map((p) => ({ key: p.key, label: p.label })));
      MU.render(filterHost, options.map(function (option) {
        const button = MU.fromTemplate("tpl-filter-chip");
        button.textContent = option.label;
        button.dataset.filter = option.key;
        if (option.key === filterPhase) button.classList.add("is-on");
        button.addEventListener("click", function () {
          filterPhase = option.key;
          filterHost.querySelectorAll("[data-filter]").forEach(function (chip) {
            chip.classList.toggle("is-on", chip.dataset.filter === filterPhase);
          });
          paint(store, colorOf, phaseColorOf);
        });
        return button;
      }));
    }

    const search = document.querySelector("[data-input='order-search']");
    search.addEventListener("input", function () {
      query = search.value.trim().toLowerCase();
      paint(store, colorOf, phaseColorOf);
    });

    paint(store, colorOf, phaseColorOf);
  }

  function paint(store, colorOf, phaseColorOf) {
    const rows = store.orders.filter(function (order) {
      const phaseOk = filterPhase === "all" || order.phase === filterPhase;
      const text = (order.code + " " + order.product + " " + order.family + " " + order.stage).toLowerCase();
      return phaseOk && (!query || text.indexOf(query) > -1);
    });

    document.querySelector("[data-slot='order-count']").textContent =
      rows.length + " of " + store.orders.length + " orders";

    MU.render("[data-slot='order-rows']", rows.length ? rows.map(function (order, index) {
      const tr = MU.fromTemplate("tpl-order-row");
      tr.dataset.order = order.code;
      MU.fill(tr, {
        seq: String(index + 1).padStart(2, "0"),
        code: order.code, product: order.product, family: order.family,
        qty: String(order.qty), phase: order.phase, stage: order.stage,
        due: order.due, status: order.status
      });
      tr.querySelector("[data-slot='line-swatch']").style.background = colorOf[order.lineCode];
      tr.querySelector("[data-slot='phase-swatch']").style.background = phaseColorOf[order.phase];
      // lead time in DAYS (summed from real stage durations, not a stage count)
      var mfg = tr.querySelector("[data-slot='mfgday']");
      if (mfg) {
        if (order.leadTotalDays != null) {
          var done = order.phase === "closed";
          var dayNum = done ? order.leadTotalDays : order.leadDay;
          mfg.textContent = "Day " + dayNum + " / " + order.leadTotalDays + " d";
          mfg.className = "mfgday" + (done ? " is-done" : "");
          mfg.title = order.stageCount + " stages · " + order.leadTotalDays +
            " planned days (engineering estimate, scales with qty " + order.qty + ")";
        } else {
          mfg.textContent = "—";
          mfg.className = "mfgday is-na";
        }
      }
      tr.querySelector("[data-field='status']").className = "badge badge--" + MU.slug(order.status);
      return tr;
    }) : [emptyRow()]);
  }

  function emptyRow() {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 10;
    td.className = "empty-state";
    td.textContent = "No orders match this filter.";
    tr.append(td);
    return tr;
  }

  window.MPage = { render: render };
})(window, document);
