/* Insights — full analytics dashboard matching the design:
   order volume, on-time trend, constraint causes, line utilisation,
   cycle-time per stage, and "how to improve next month" cards.
   All from the existing /api/insights (services.insight_charts). */
(function (window, document) {
  "use strict";

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  async function render() {
    var mount = document.querySelector("[data-slot='insights-mount']");
    if (!mount) return;
    mount.innerHTML = '<p class="muted">Loading…</p>';
    var d;
    try { d = await fetch("/api/insights").then(function (r) { return r.json(); }); }
    catch (e) { mount.innerHTML = '<p class="muted">Could not load insights.</p>'; return; }
    if (!d || typeof d !== "object") {
      mount.innerHTML = '<p class="muted">Could not load insights.</p>'; return;
    }
    try {
      _renderBody(mount, d);
    } catch (e) {
      // never leave the page blank on a single bad series — show what we can
      mount.innerHTML = '<p class="muted">Some insights could not be drawn (' +
        (e && e.message ? e.message : "error") + ").</p>";
      try { _renderCharts(mount, d); } catch (e2) {}
    }
  }

  function _renderBody(mount, d) {
    mount.innerHTML = "";

    // ---- history/rollup status + manual refresh -------------------------
    var meta = d.historyMeta || {};
    var bar = el("div", "in-rollupbar");
    function fmtWhen(iso) {
      if (!iso) return "not yet computed";
      try {
        var dt = new Date(iso);
        var mins = Math.round((Date.now() - dt.getTime()) / 60000);
        if (mins < 1) return "just now";
        if (mins < 60) return mins + "m ago";
        if (mins < 1440) return Math.round(mins / 60) + "h ago";
        return Math.round(mins / 1440) + "d ago";
      } catch (e) { return iso; }
    }
    var _canWrite = false;
    try { _canWrite = !!(window.MStore && window.MStore.data && window.MStore.canWrite); }
    catch (e) { _canWrite = false; }
    bar.innerHTML =
      '<span class="in-rollup__dot ' + (meta.live ? "is-live" : "is-sample") + '"></span>' +
      '<span class="in-rollup__txt">' +
        (meta.live
          ? "Insights computed from <b>" + (meta.eventsSeen || 0) + "</b> recorded events"
          : "Showing <b>sample</b> insights — import history to refine") +
        ' · <span class="in-rollup__when">updated ' + fmtWhen(meta.computedAt) + "</span></span>" +
      (_canWrite
        ? '<button class="in-rollup__btn" data-act="refresh">\u21bb Refresh</button>' : "");
    mount.appendChild(bar);
    var refreshBtn = bar.querySelector("[data-act='refresh']");
    if (refreshBtn) refreshBtn.addEventListener("click", async function () {
      refreshBtn.disabled = true; refreshBtn.textContent = "Refreshing…";
      try {
        await fetch("/api/insights/rollup", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ windowDays: 90 }) });
        render(window.MStore);
      } catch (e) {
        refreshBtn.disabled = false; refreshBtn.textContent = "\u21bb Refresh";
      }
    });

    // ---- history overview banner (15-year synthetic incident base) ------
    fetch("/api/insights/history-overview").then(function (r) { return r.json(); })
      .then(function (o) {
        if (!o || !o.incidentCount) return;
        var banner = el("div", "in-histbanner");
        banner.innerHTML =
          '<div class="in-histbanner__main">' +
            '<span class="in-histbanner__big">' + o.incidentCount + "</span>" +
            '<span class="in-histbanner__lbl">incidents analysed<br>' + (o.yearsSpan || "") + "</span></div>" +
          '<div class="in-histbanner__stat"><span>' + (o.totalLossFmt || "") + "</span><label>total loss</label></div>" +
          '<div class="in-histbanner__stat"><span>' + (o.themeCount || 0) + "</span><label>recurring themes</label></div>" +
          '<div class="in-histbanner__hint">Click any chart point below to see the detail behind it.</div>';
        mount.insertBefore(banner, bar.nextSibling);
      }).catch(function () {});

    // ---- charts ----
    _renderCharts(mount, d);
  }

  function _renderCharts(mount, d) {
    // each series guarded independently so one bad series can't blank the rest
    try {
      var row1 = el("div", "in-row2");
      if (d.volume) row1.appendChild(volumeCard(d.volume));
      if (d.on_time) row1.appendChild(ontimeCard(d.on_time, d.on_time_last));
      mount.appendChild(row1);
    } catch (e) {}
    try {
      var row2 = el("div", "in-row2");
      if (d.causes) row2.appendChild(causesCard(d.causes));
      if (d.utilisation) row2.appendChild(utilCard(d.utilisation));
      mount.appendChild(row2);
    } catch (e) {}
    try { if (d.stage_days) mount.appendChild(cycleCard(d.stage_days)); } catch (e) {}
    // NOTE: the "how to improve" optimization suggestions have moved to the
    // Dashboard (Optimization Suggested). The Insights tab keeps only charts.
  }

  function volumeCard(volume) {
    var c = el("div", "card");
    c.appendChild(el("h2", "card__label", "ORDER VOLUME · SIX MONTHS"));
    var hint = el("p", "muted sm", "Click a month to see the orders behind it.");
    hint.style.margin = "-4px 0 10px";
    c.appendChild(hint);
    var chart = el("div", "iv-vol");
    var max = Math.max.apply(null, volume.map(function (v) { return v.value; }).concat([1]));
    volume.forEach(function (v, i) {
      var h = Math.round(v.value / max * 100);
      var last = i === volume.length - 1;
      var col = el("div", "iv-vol__col" + (v.orders && v.orders.length ? " is-clickable" : ""));
      col.innerHTML =
        '<div class="iv-vol__val">' + v.value + "</div>" +
        '<div class="iv-vol__bar' + (last ? " is-last" : "") + '" style="height:' + h + '%"></div>' +
        '<div class="iv-vol__lbl">' + v.label + "</div>";
      if (v.orders && v.orders.length) {
        col.addEventListener("click", function () {
          openOrderDrill(v.label + " — orders this month", v.orders,
                    v.value + " orders placed, sorted by quantity. Large batches are flagged.");
        });
      }
      chart.appendChild(col);
    });
    c.appendChild(chart);
    return c;
  }

  function ontimeCard(points, last) {
    var c = el("div", "card");
    var head = el("div", "iv-head");
    head.innerHTML = '<span class="card__label">ON-TIME DELIVERY TREND</span>' +
      '<span class="iv-big">' + (last || "") + "</span>";
    c.appendChild(head);
    var hint = el("p", "muted sm", "Click a point to see what happened that month.");
    hint.style.margin = "-4px 0 10px";
    c.appendChild(hint);
    // build an SVG line from the values
    var w = 340, h = 150, pad = 10;
    var max = 100, min = Math.min.apply(null, points.map(function (p) { return p.value; }).concat([80])) - 5;
    var coords = points.map(function (p, i) {
      var x = pad + (i / (points.length - 1)) * (w - 2 * pad);
      var y = pad + (1 - (p.value - min) / (max - min)) * (h - 2 * pad);
      return { x: x, y: y };
    });
    var pts = coords.map(function (p) { return p.x.toFixed(1) + "," + p.y.toFixed(1); }).join(" ");
    var svg =
      '<svg viewBox="0 0 ' + w + " " + h + '" class="iv-line" preserveAspectRatio="none">' +
      '<polyline points="' + pts + '" fill="none" stroke="#2f7d4f" stroke-width="2.5" ' +
      'stroke-linejoin="round" stroke-linecap="round"/></svg>';
    var labels = '<div class="iv-line__lbls">' +
      points.map(function (p) { return "<span>" + p.label + "</span>"; }).join("") + "</div>";
    var wrap = el("div", "iv-linewrap", svg + labels);
    var dotsHost = el("div", "iv-line__pts");
    coords.forEach(function (co, i) {
      var p = points[i];
      var clickable = p.breakdown && p.breakdown.length;
      var dot = el("button", "iv-line__pt" + (clickable ? " is-clickable" : ""));
      dot.type = "button";
      dot.style.left = (co.x / w * 100).toFixed(2) + "%";
      dot.style.top = co.y.toFixed(1) + "px";
      dot.setAttribute("aria-label", p.label + " " + p.value + "%");
      if (clickable) {
        dot.addEventListener("click", function () {
          openDrill(p.label + " — " + p.value + "% on-time", p.breakdown, "#c0453b", p.note || "");
        });
      }
      dotsHost.appendChild(dot);
    });
    wrap.appendChild(dotsHost);
    c.appendChild(wrap);
    return c;
  }

  function causesCard(causes) {
    var c = el("div", "card");
    c.appendChild(el("h2", "card__label", "WHAT STOPS THE FLOOR · CONSTRAINT CAUSES"));
    var hint = el("p", "muted sm", "Click a cause to see the breakdown.");
    hint.style.margin = "-4px 0 10px";
    c.appendChild(hint);
    var list = el("div", "iv-bars");
    causes.forEach(function (x) {
      var row = el("div", "iv-bar" + (x.breakdown && x.breakdown.length ? " is-clickable" : ""));
      row.innerHTML =
        '<div class="iv-bar__top"><span>' + x.label + "</span><span>" + x.pct + "%</span></div>" +
        '<div class="iv-bar__track"><i style="width:' + x.pct + '%;background:' + x.color + '"></i></div>';
      if (x.breakdown && x.breakdown.length) {
        row.addEventListener("click", function () {
          openDrill(x.label + " — breakdown", x.breakdown, x.color,
                    "Share of “" + x.label + "” incidents this month.");
        });
      }
      list.appendChild(row);
    });
    c.appendChild(list);
    return c;
  }

  // shared drill-down modal for clickable insight charts
  function openDrill(title, rows, color, sub) {
    var prev = document.querySelector(".iv-drill");
    if (prev) prev.remove();
    var max = Math.max.apply(null, rows.map(function (r) { return r.pct || 0; })) || 100;
    var ov = el("div", "iv-drill");
    ov.innerHTML =
      '<div class="iv-drill__panel">' +
        '<div class="iv-drill__head"><span>' + title + "</span>" +
          '<button class="iv-drill__x" aria-label="Close">\u00d7</button></div>' +
        (sub ? '<p class="iv-drill__sub">' + sub + "</p>" : "") +
        '<div class="iv-drill__bars">' +
        rows.map(function (r) {
          var w = Math.round((r.pct || 0) / max * 100);
          return '<div class="iv-drill__row">' +
            '<div class="iv-drill__top"><span>' + r.label + "</span><span>" + r.pct + "%</span></div>" +
            '<div class="iv-drill__track"><i style="width:' + w + '%;background:' +
              (color || "#5878b5") + '"></i></div></div>';
        }).join("") +
        "</div></div>";
    ov.addEventListener("click", function (e) { if (e.target === ov) ov.remove(); });
    ov.querySelector(".iv-drill__x").addEventListener("click", function () { ov.remove(); });
    document.body.appendChild(ov);
  }

  // order-list drill-down (volume chart) — real orders, qty-sorted, large
  // batches flagged instead of a percentage breakdown
  function openOrderDrill(title, orders, sub) {
    var prev = document.querySelector(".iv-drill");
    if (prev) prev.remove();
    var ov = el("div", "iv-drill");
    var rows = orders.map(function (o) {
      var late = /late/i.test(o.status || "");
      return '<div class="iv-drill__order' + (o.large ? " is-large" : "") + '">' +
        '<div class="iv-drill__order-main">' +
          '<span class="iv-drill__order-code">' + o.code + "</span>" +
          '<span class="iv-drill__order-prod">' + o.product + "</span>" +
          (o.large ? '<span class="iv-drill__order-flag">LARGE BATCH</span>' : "") +
        "</div>" +
        '<div class="iv-drill__order-meta">' +
          '<span>' + o.line + "</span>" +
          '<span class="iv-drill__order-qty">' + o.qty + " pcs</span>" +
          '<span class="' + (late ? "iv-drill__order-status is-late" : "iv-drill__order-status") + '">' + o.status + "</span>" +
        "</div></div>";
    }).join("");
    ov.innerHTML =
      '<div class="iv-drill__panel">' +
        '<div class="iv-drill__head"><span>' + title + "</span>" +
          '<button class="iv-drill__x" aria-label="Close">\u00d7</button></div>' +
        (sub ? '<p class="iv-drill__sub">' + sub + "</p>" : "") +
        '<div class="iv-drill__orders">' + rows + "</div></div>";
    ov.addEventListener("click", function (e) { if (e.target === ov) ov.remove(); });
    ov.querySelector(".iv-drill__x").addEventListener("click", function () { ov.remove(); });
    document.body.appendChild(ov);
  }

  function utilCard(util) {
    var c = el("div", "card");
    c.appendChild(el("h2", "card__label", "LINE UTILISATION THIS MONTH"));
    var list = el("div", "iv-bars");
    util.forEach(function (u) {
      var row = el("div", "iv-bar" + (u.breakdown && u.breakdown.length ? " is-clickable" : ""));
      row.innerHTML =
        '<div class="iv-bar__top"><span>' + u.label + "</span>" +
          '<span class="iv-note">' + u.note + " " + u.pct + "%</span></div>" +
        '<div class="iv-bar__track"><i style="width:' + u.pct + '%;background:' + u.color + '"></i></div>';
      if (u.breakdown && u.breakdown.length) {
        row.addEventListener("click", function () {
          openDrill(u.label + " — where the load sits", u.breakdown, u.color,
                    "Share of this line's utilisation by stage.");
        });
      }
      list.appendChild(row);
    });
    c.appendChild(list);
    return c;
  }

  function cycleCard(stages) {
    var c = el("div", "card");
    c.appendChild(el("h2", "card__label", "WHERE THE CYCLE TIME SITS · AVG DAYS PER STAGE"));
    var hint = el("p", "muted sm", "Click a stage to see what makes up its time.");
    hint.style.margin = "-4px 0 10px";
    c.appendChild(hint);
    var grid = el("div", "iv-cycle");
    stages.forEach(function (s) {
      var row = el("div", "iv-cyc" + (s.breakdown && s.breakdown.length ? " is-clickable" : ""));
      row.innerHTML =
        '<div class="iv-cyc__top"><span>' + s.label + "</span><span>" + s.value + " d</span></div>" +
        '<div class="iv-bar__track"><i style="width:' + s.pct + '%;background:' +
          (s.hot ? "#c0453b" : "#7ea3c9") + '"></i></div>';
      if (s.breakdown && s.breakdown.length) {
        row.addEventListener("click", function () {
          openDrill(s.label + " — " + s.value + " d breakdown", s.breakdown,
                    s.hot ? "#c0453b" : "#7ea3c9",
                    "What makes up the average time an order spends at " + s.label + ".");
        });
      }
      grid.appendChild(row);
    });
    c.appendChild(grid);
    return c;
  }

  function actionsCard(actions) {
    var c = el("div", "card in-improve");
    c.appendChild(el("div", "in-improve__head",
      '<span class="in-improve__ai">AI</span><div>' +
      '<div class="in-improve__title">HOW TO IMPROVE NEXT MONTH</div>' +
      '<div class="in-improve__sub">ranked by expected effect on throughput</div></div>'));
    var grid = el("div", "in-acts");
    (actions || []).forEach(function (a) {
      var card = el("div", "in-act");
      card.innerHTML =
        '<div class="in-act__title">' + a.title + "</div>" +
        '<div class="in-act__detail">' + a.detail + "</div>" +
        (a.gain ? '<div class="in-act__gain">' + a.gain + "</div>" : "");
      grid.appendChild(card);
    });
    c.appendChild(grid);
    return c;
  }

  if (document.readyState !== "loading") render();
  else document.addEventListener("DOMContentLoaded", render);
})(window, document);
