/* ============================================================================
   util.js — DOM + formatting helpers shared by every page script.
   No app state, no data. Attaches to window.MU.
   ==========================================================================*/
(function (window, document) {
  "use strict";

  /** Clone a <template> by id and return its first element child. */
  function fromTemplate(id) {
    const tpl = document.getElementById(id);
    if (!tpl) throw new Error("missing template: " + id);
    return tpl.content.firstElementChild.cloneNode(true);
  }

  /** Fill an element's [data-field] descendants from a plain object. */
  function fill(root, values) {
    root.querySelectorAll("[data-field]").forEach(function (node) {
      const key = node.dataset.field;
      if (!(key in values)) return;
      const value = values[key];
      if (value === null || value === undefined) { node.textContent = ""; return; }
      node.textContent = String(value);
    });
    return root;
  }

  /** Replace a container's children. */
  function render(target, nodes) {
    const host = typeof target === "string" ? document.querySelector(target) : target;
    if (!host) return null;
    host.replaceChildren.apply(host, [].concat(nodes).filter(Boolean));
    return host;
  }

  function empty(message) {
    const p = document.createElement("p");
    p.className = "empty-state";
    p.textContent = message;
    return p;
  }

  const initials = (name) =>
    String(name || "").replace(/[^A-Za-z ]/g, "").split(" ").filter(Boolean)
      .map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  const slug = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");

  function greeting(date) {
    const hour = (date || new Date()).getHours();
    return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  }

  function longDate(date) {
    const d = date || new Date();
    return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })
      + " · " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  function clock(date) {
    return (date || new Date()).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }

  function stamp(date) {
    const d = date || new Date();
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const pad = (n) => String(n).padStart(2, "0");
    return d.getDate() + " " + months[d.getMonth()] + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  window.MU = { fromTemplate, fill, render, empty, initials, slug, greeting, longDate, clock, stamp };
})(window, document);
