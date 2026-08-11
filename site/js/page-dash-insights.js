/* Dashboard — compact overall-insights strip (on-time, volume, top cause). */
(function (window, document) {
  "use strict";
  async function render() {
    var host = document.querySelector("[data-slot='dash-insights']");
    if (!host) return;
    var d;
    try { d = await fetch("/api/insights").then(function (r) { return r.json(); }); }
    catch (e) { return; }

    var lastVol = d.volume[d.volume.length - 1] || {};
    var topCause = (d.causes || [])[0] || {};
    var sat = (d.utilisation || []).slice().sort(function (a, b) { return b.pct - a.pct; })[0] || {};

    var strip = document.createElement("div");
    strip.className = "dash-ins";
    strip.innerHTML =
      tile("On-time delivery", d.on_time_last || "—", "trending", "#2f7d4f") +
      tile("Orders this month", lastVol.value != null ? lastVol.value : "—", "volume", "#3a86c8") +
      tile("Top floor stopper", (topCause.label || "—"), (topCause.pct || 0) + "% of constraints", topCause.color || "#c0453b") +
      tile("Busiest line", (sat.label || "—").replace(" - ", " · "), (sat.note || "") + " " + (sat.pct || 0) + "%", sat.color || "#5878b5");
    host.appendChild(strip);

    // one headline action
    if (d.actions && d.actions.length) {
      var a = d.actions[0];
      var act = document.createElement("div");
      act.className = "dash-ins__act";
      act.innerHTML =
        '<span class="dash-ins__ai">AI</span>' +
        '<div><b>Top opportunity: </b>' + a.title +
        (a.gain ? ' <span class="dash-ins__gain">' + a.gain + "</span>" : "") +
        '<div class="dash-ins__detail">' + a.detail + "</div></div>" +
        '<a class="dash-ins__more" href="insights.html">See all insights →</a>';
      host.appendChild(act);
    }
  }
  function tile(label, value, sub, color) {
    return '<div class="dash-tile"><div class="dash-tile__bar" style="background:' + color + '"></div>' +
      '<div class="dash-tile__label">' + label + "</div>" +
      '<div class="dash-tile__value">' + value + "</div>" +
      '<div class="dash-tile__sub">' + sub + "</div></div>";
  }
  if (document.readyState !== "loading") render();
  else document.addEventListener("DOMContentLoaded", render);
})(window, document);
