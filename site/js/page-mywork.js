/* My Work — the employee's own productivity view: a summary of their submitted
   work (approved / rejected / pending, approval rate) and a table of every stage
   they've submitted, with shift and review status. Data from /api/my-work. */
(function (window, document) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmt(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(iso);
      return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) +
        " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    } catch (e) { return "—"; }
  }
  var STATUS_LABEL = { approved: "Approved", rejected: "Rejected", submitted: "Pending" };

  async function render() {
    var sumHost = document.querySelector("[data-slot='mw-summary']");
    var rowsHost = document.querySelector("[data-slot='mw-rows']");
    var countHost = document.querySelector("[data-slot='mw-count']");
    if (!rowsHost) return;
    var d;
    try {
      d = await fetch("/api/my-work?t=" + Date.now(), { cache: "no-store" })
        .then(function (r) { return r.json(); });
    }
    catch (e) { rowsHost.innerHTML = '<tr><td colspan="7" class="muted">Could not load your work.</td></tr>'; return; }

    var s = d.summary || {};
    if (countHost) countHost.textContent = (s.total || 0) + " submissions";
    if (sumHost) {
      var rate = s.approvalRate == null ? "—" : s.approvalRate + "%";
      sumHost.innerHTML =
        card("Submitted", s.total || 0, "total tasks logged") +
        card("Approved", s.approved || 0, "signed off by a head", "is-ok") +
        card("Rejected", s.rejected || 0, "sent back for rework", "is-rej") +
        card("Pending", s.pending || 0, "awaiting review") +
        card("Approval rate", rate, "of decided work approved", "is-rate");
    }

    var items = d.items || [];
    if (!items.length) {
      rowsHost.innerHTML = '<tr><td colspan="7" class="muted">No submitted work yet. Complete a stage checklist and submit it for approval — it\'ll appear here.</td></tr>';
      return;
    }
    rowsHost.innerHTML = items.map(function (it) {
      var st = it.status || "submitted";
      var cls = st === "approved" ? "is-ok" : st === "rejected" ? "is-rej" : "is-pending";
      var checklist = (it.itemsDone != null && it.itemsTotal)
        ? it.itemsDone + "/" + it.itemsTotal : "—";
      return "<tr>" +
        "<td><b>" + esc(it.order) + "</b></td>" +
        "<td>" + esc(it.stage) + "</td>" +
        "<td>" + esc(it.shift) + "</td>" +
        '<td class="right">' + checklist + "</td>" +
        "<td>" + fmt(it.submittedAt) + "</td>" +
        "<td>" + esc(it.reviewedBy || "—") + "</td>" +
        '<td><span class="mw-badge ' + cls + '">' + (STATUS_LABEL[st] || st) + "</span>" +
          (it.remarks ? '<div class="mw-remark">"' + esc(it.remarks) + '"</div>' : "") +
        "</td></tr>";
    }).join("");
  }

  function card(label, value, sub, cls) {
    return '<div class="mw-card ' + (cls || "") + '">' +
      '<div class="mw-card__val">' + esc(value) + "</div>" +
      '<div class="mw-card__label">' + esc(label) + "</div>" +
      '<div class="mw-card__sub">' + esc(sub) + "</div></div>";
  }

  if (document.readyState !== "loading") render();
  else document.addEventListener("DOMContentLoaded", render);
  // refresh when the user returns to this tab (e.g. after finishing a task
  // on the checklist page), so newly-submitted work shows without a manual reload.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") render();
  });
  window.addEventListener("pageshow", function (e) { if (e.persisted) render(); });
})(window, document);
