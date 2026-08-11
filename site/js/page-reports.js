/* Reports — monthly performance table matching the design, with PDF download.
   Data from the existing /api/reports (services.report_rows). */
(function (window, document) {
  "use strict";

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  async function render() {
    var mount = document.querySelector("[data-slot='reports-mount']");
    if (!mount) return;
    mount.innerHTML = '<p class="muted">Loading…</p>';
    var months;
    try { months = await fetch("/api/reports").then(function (r) { return r.json(); }); }
    catch (e) { mount.innerHTML = '<p class="muted">Could not load reports.</p>'; return; }
    if (!months || !months.length) { mount.innerHTML = '<p class="muted">No report data.</p>'; return; }
    mount.innerHTML = "";

    var last = months[months.length - 1];
    var prev = months[months.length - 2] || {};

    // header with download
    var head = el("div", "rp-head");
    head.innerHTML =
      '<div><h2 class="rp-h">Monthly reports</h2>' +
      '<p class="rp-sub">Order volume, constraints and delivery performance · last six months</p></div>' +
      '<button class="btn btn--primary rp-dl" data-a="pdf">\u2193 Download PDF</button>';
    mount.appendChild(head);

    // stat cards
    var machineMat = "";  // small detail line for constraints
    var grid = el("div", "rp-cards");
    grid.appendChild(statCard("ORDERS THIS MONTH", last.total,
      (last.growth != null ? (last.growth >= 0 ? "+" : "") + last.growth + "% vs " + shortPrev(prev) : "") +
      " · " + last.shipped + " shipped"));
    grid.appendChild(statCard("ON-TIME DELIVERY", last.on_time + "%",
      bestMonth(months) ? "best month in the last six" : "delivery performance"));
    grid.appendChild(statCard("CONSTRAINTS RAISED", last.constraints,
      (prev.constraints != null ? (last.constraints <= prev.constraints ? "down from " : "up from ") + prev.constraints : "")));
    grid.appendChild(statCard("AVG CYCLE TIME", last.cycle + " d",
      (prev.cycle != null ? (last.cycle - prev.cycle).toFixed(1) + " d vs " + shortPrev(prev) : "") +
      " · burn-in still the peak"));
    mount.appendChild(grid);

    // month-by-month table
    var tableCard = el("div", "card");
    tableCard.appendChild(el("div", "rp-tlabel", "MONTH-BY-MONTH · GROWTH"));
    var t = el("table", "rp-table");
    t.innerHTML =
      "<thead><tr><th>Month</th><th>Orders</th><th>Shipped</th><th>Constraints</th>" +
      "<th>On-time</th><th>Avg cycle</th><th>Growth</th></tr></thead>";
    var tb = document.createElement("tbody");
    months.forEach(function (m, i) {
      var last2 = i === months.length - 1;
      var tr = document.createElement("tr");
      if (last2) tr.className = "is-last";
      tr.innerHTML =
        "<td class='rp-month'>" + m.label + "</td>" +
        "<td>" + m.total + "</td>" +
        "<td>" + m.shipped + "</td>" +
        "<td>" + m.constraints + "</td>" +
        "<td>" + m.on_time + "%</td>" +
        "<td>" + m.cycle + " d</td>" +
        "<td class='rp-growth'>" + (m.growth == null ? "\u2014" :
          '<span class="rp-up">+' + m.growth + "%</span>") + "</td>";
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    tableCard.appendChild(t);
    mount.appendChild(tableCard);

    mount.appendChild(el("p", "rp-foot",
      "Growth is month-on-month order volume. Constraints counts every stoppage "
      + "raised on the floor and approved by the production head."));

    // PDF download — print the report region
    head.querySelector("[data-a='pdf']").addEventListener("click", function () {
      downloadPdf(months, last);
    });
  }

  function statCard(label, value, sub) {
    return (function () {
      var c = el("div", "rp-card");
      c.innerHTML = '<div class="rp-card__label">' + label + "</div>" +
        '<div class="rp-card__value">' + value + "</div>" +
        '<div class="rp-card__sub">' + (sub || "") + "</div>";
      return c;
    })();
  }
  function shortPrev(p) { return p.label ? p.label.split(" ")[0] : "prev"; }
  function bestMonth(months) {
    var last = months[months.length - 1];
    return months.every(function (m) { return m.on_time <= last.on_time; });
  }

  /* PDF: open a print window with a clean report layout -> user saves as PDF.
     Uses the browser's print-to-PDF (no external library needed). */
  function downloadPdf(months, last) {
    var w = window.open("", "_blank");
    if (!w) return;
    var lastLabel = (last && last.label) ? last.label.replace(" ", "_") : "report";
    var docTitle = "Meridian_Monthly_Report_" + lastLabel;
    var rows = months.map(function (m) {
      return "<tr><td>" + m.label + "</td><td>" + m.total + "</td><td>" + m.shipped +
        "</td><td>" + m.constraints + "</td><td>" + m.on_time + "%</td><td>" + m.cycle +
        " d</td><td>" + (m.growth == null ? "\u2014" : "+" + m.growth + "%") + "</td></tr>";
    }).join("");
    w.document.write(
      "<html><head><title>" + docTitle + "</title><style>" +
      "body{font-family:Arial,Helvetica,sans-serif;color:#1a2733;padding:28px;}" +
      "h1{font-size:20px;margin:0;}p.sub{color:#667;margin:4px 0 20px;}" +
      ".cards{display:flex;gap:12px;margin-bottom:20px;}" +
      ".c{flex:1;border:1px solid #dce3e9;border-radius:8px;padding:12px 14px;}" +
      ".c b{font-size:20px;display:block;}.c small{color:#667;font-size:10px;letter-spacing:.04em;}" +
      "table{width:100%;border-collapse:collapse;font-size:12px;}" +
      "th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #e3e8ed;}" +
      "th{color:#667;font-size:10px;letter-spacing:.04em;text-transform:uppercase;}" +
      "tr:last-child td{font-weight:600;}" +
      "footer{margin-top:16px;color:#8a97a3;font-size:10px;}" +
      "</style></head><body>" +
      "<h1>Meridian Instruments — Monthly Report</h1>" +
      "<p class='sub'>Order volume, constraints and delivery performance · last six months</p>" +
      "<div class='cards'>" +
        "<div class='c'><small>ORDERS THIS MONTH</small><b>" + last.total + "</b>" + last.shipped + " shipped</div>" +
        "<div class='c'><small>ON-TIME DELIVERY</small><b>" + last.on_time + "%</b></div>" +
        "<div class='c'><small>CONSTRAINTS RAISED</small><b>" + last.constraints + "</b></div>" +
        "<div class='c'><small>AVG CYCLE TIME</small><b>" + last.cycle + " d</b></div>" +
      "</div>" +
      "<table><thead><tr><th>Month</th><th>Orders</th><th>Shipped</th><th>Constraints</th>" +
      "<th>On-time</th><th>Avg cycle</th><th>Growth</th></tr></thead><tbody>" + rows + "</tbody></table>" +
      "<footer>Generated " + new Date().toLocaleString() +
      " · Growth is month-on-month order volume.</footer>" +
      "</body></html>");
    w.document.close();
    setTimeout(function () { w.print(); }, 300);
  }

  if (document.readyState !== "loading") render();
  else document.addEventListener("DOMContentLoaded", render);
})(window, document);
