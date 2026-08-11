/* Shared order-detail panel — opened from the board Gantt AND the dashboard
   kanban. Shows status, routing stepper, planned-vs-actual analysis (day-of /
   total, due vs projected, variance), reference data, override, and audit. */
(function (window, document) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  async function openOrderDetail(code) {
    var existing = document.querySelector(".odetail-overlay");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.className = "odetail-overlay";
    overlay.innerHTML = '<div class="odetail"><div class="odetail__loading">Loading ' + code + '…</div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });

    var d;
    try { d = await fetch("/api/order/" + code).then(function (r) { return r.json(); }); }
    catch (e) { overlay.querySelector(".odetail").innerHTML = "Could not load " + code; return; }
    if (d.error) { overlay.querySelector(".odetail").innerHTML = d.error; return; }

    var canWrite = window.MStore && window.MStore.canWrite;

    var steps = d.steps.map(function (s) {
      var mark = s.state === "done" ? "\u2713" : "";
      return '<div class="ostep is-' + s.state + '"><div class="ostep__dot">' + mark + "</div>" +
        '<div class="ostep__name">' + s.stage + "</div>" +
        '<div class="ostep__state">' + s.state + "</div></div>";
    }).join('<div class="ostep__link"></div>');

    // ---- planned vs actual analysis ----
    var a = d.analysis || {};
    var analysisHtml = "";
    if (a.dayOf != null || a.due) {
      var late = a.varianceDays != null && a.varianceDays > 0;
      var vcls = a.onTime === false ? "is-late" : "is-ok";
      var dayPct = (a.totalDays ? Math.min(100, Math.round((a.dayOf / a.totalDays) * 100)) : 0);
      analysisHtml =
        '<div class="odetail__section">PLANNED vs ACTUAL</div>' +
        '<div class="oanalysis">' +
          '<div class="oan oan--progress">' +
            '<div class="oan__label">Day of cycle</div>' +
            '<div class="oan__value">' + (a.dayOf != null ? a.dayOf : "\u2014") +
              ' <span class="oan__unit">/ ' + (a.totalDays != null ? a.totalDays : "\u2014") + " d</span></div>" +
            '<div class="oan__track"><i style="width:' + dayPct + '%"></i></div>' +
            '<div class="oan__sub">' + dayPct + '% through planned cycle</div></div>' +
          '<div class="oan">' +
            '<div class="oan__label">Stages complete</div>' +
            '<div class="oan__value">' + a.stagesDone +
              ' <span class="oan__unit">/ ' + a.stagesTotal + "</span></div></div>" +
          '<div class="oan">' +
            '<div class="oan__label">Due</div>' +
            '<div class="oan__value">' + (a.due || "\u2014") + "</div></div>" +
          '<div class="oan">' +
            '<div class="oan__label">Projected finish</div>' +
            '<div class="oan__value ' + vcls + '">' + (a.projectedFinish || "\u2014") + "</div></div>" +
          '<div class="oan oan--variance ' + vcls + '">' +
            '<div class="oan__label">Variance</div>' +
            '<div class="oan__value">' +
              (a.varianceDays == null ? "\u2014" :
                (a.varianceDays > 0 ? "+" : "") + a.varianceDays + " d") + "</div>" +
            '<div class="oan__sub">' +
              (a.varianceDays == null ? "" : (late ? "behind schedule" : "on / ahead of time")) +
              "</div></div>" +
        "</div>";
    }

    var audit = d.audit.length ? d.audit.map(function (x) {
      return '<div class="oaudit"><div class="oaudit__ts">' + x.ts + "</div>" +
        '<div><div class="oaudit__title">' + x.title + "</div>" +
        (x.actor ? '<div class="oaudit__who">' + x.actor + "</div>" : "") + "</div></div>";
    }).join("") : '<div class="muted sm">No history yet.</div>';

    var statusOpts = d.statuses.map(function (s) {
      return '<option' + (s === d.status ? " selected" : "") + ">" + s + "</option>";
    }).join("");

    var override = canWrite ? (
      '<div class="ooverride"><div class="ooverride__head">Planner override</div>' +
      '<p class="ooverride__note">Manually correct status. A reason is required; the change is logged and tagged <b>Planner override</b> so it is never mistaken for floor reality.</p>' +
      '<div class="ooverride__row">' +
      '<label>New status<select data-ov="status">' + statusOpts + "</select></label>" +
      '<label class="grow1">Reason (required)<input data-ov="reason" placeholder="Why is this being overridden?"></label>' +
      '<button class="btn btn--primary" data-ov="apply">Apply override</button>' +
      "</div></div>") : "";

    // ---- status banner (with reason) ----
    var si = d.statusInfo || { status: d.status };
    var st = (si.status || d.status || "").toUpperCase();
    var stressed = ["HALTED", "AT RISK", "RESCHEDULED", "RUSH"].indexOf(st) > -1;
    var stTone = ({ "HALTED": "halt", "AT RISK": "risk", "RESCHEDULED": "resched",
      "RUSH": "rush", "RUNNING": "run", "ON TRACK": "run", "DONE": "done" })[st] || "run";
    var statusBanner =
      '<div class="ostatus ostatus--' + stTone + '">' +
        '<div class="ostatus__row">' +
          '<span class="ostatus__badge">' + st + "</span>" +
          (si.reasonType ? '<span class="ostatus__type">' + si.reasonType + "</span>" : "") +
          (si.ref ? '<span class="ostatus__ref">' + si.ref + "</span>" : "") +
        "</div>" +
        (si.reason
          ? '<div class="ostatus__reason">' + si.reason + "</div>"
          : (stressed ? '<div class="ostatus__reason ostatus__reason--muted">No recorded reason for this status.</div>' : "")) +
        ((si.by || si.since || si.atStage)
          ? '<div class="ostatus__meta">' +
              (si.atStage ? "at " + si.atStage : "") +
              (si.by ? (si.atStage ? " · " : "") + "raised by " + si.by : "") +
              (si.since ? " · " + si.since : "") + "</div>"
          : "") +
      "</div>";

    var STATUS_PILL = { "HALTED": "is-halted", "AT RISK": "is-risk",
      "RESCHEDULED": "is-resched", "RUSH": "is-rush", "RUNNING": "is-running",
      "ON TRACK": "is-ontrack", "DONE": "is-done" };
    var statusPill = st
      ? '<span class="odetail__status ' + (STATUS_PILL[st] || "is-ontrack") + '">' + st + "</span>"
      : "";

    // ---- batch-split lineage + control ----
    var LINES = { PT: "Line 1", TT: "Line 2", DP: "Line 3", LT: "Line 4" };
    function codeLinks(codes) {
      return (codes || []).map(function (c) {
        return '<button class="osplit__link" data-open="' + c + '">' + c + "</button>";
      }).join(" + ");
    }
    var splitBlock = "";
    var si2 = d.splitInfo;
    if (si2 && si2.role === "parent") {
      splitBlock =
        '<div class="osplit osplit--parent"><span class="osplit__tag">SPLIT</span>' +
        "This order was split into " + codeLinks(si2.children) +
        ". It has been retired from the live board.</div>";
    } else if (si2 && si2.role === "child") {
      splitBlock =
        '<div class="osplit osplit--child"><span class="osplit__tag">PART ' +
        (si2.part || "") + '</span>Split from ' + codeLinks([si2.parent]) +
        (si2.siblings && si2.siblings.length
          ? ". Sibling: " + codeLinks(si2.siblings) : "") + "</div>";
    } else if (d.canSplit && canWrite) {
      splitBlock =
        '<div class="osplit osplit--action">' +
        '<button class="btn btn--sm" data-split="open">\u2702 Split this order\u2026</button>' +
        '<span class="osplit__hint">create two part-batches that each show on the board</span>' +
        "</div>";
    }

    // #4 lock/protect control
    var lockBlock = "";
    if (d.locked) {
      lockBlock = '<div class="oprot"><span class="oprot__tag">\uD83D\uDD12 PROTECTED</span>' +
        (d.lockReason ? esc(d.lockReason) : "This order is prioritised and won't be moved.") +
        (canWrite ? ' <button class="btn btn--xs" data-lock="off">Unlock</button>' : "") + "</div>";
    } else if (canWrite) {
      lockBlock = '<div class="oprot oprot--off">' +
        '<button class="btn btn--xs" data-lock="on">\uD83D\uDD12 Protect (lock priority)</button>' +
        '<span class="osplit__hint">warns before this order can be moved</span></div>';
    }

    // #5 quality hold + rework
    var qBlock = "";
    if (d.qhold) {
      var stageOpts = ["Kitting","Sensor","Electronics","Assembly","Calibration"]
        .map(function (s) { return '<option>' + s + "</option>"; }).join("");
      qBlock = '<div class="oqhold"><div class="oqhold__head"><span class="oqhold__tag">\u26D4 QUALITY HOLD</span>' +
        (d.qholdReason ? esc(d.qholdReason) : "Blocked pending quality sign-off.") + "</div>" +
        (canWrite ? '<div class="oqhold__actions">' +
          '<button class="btn btn--sm btn--primary" data-q="release">Release (passed)</button>' +
          '<span class="oqhold__rework">Rework at <select data-q="stage">' + stageOpts + "</select>" +
          '<button class="btn btn--sm" data-q="rework">Send to rework</button></span>' +
          '<button class="btn btn--sm" data-q="scrap">Scrap</button>' +
          "</div>" : "") + "</div>";
    } else if (d.reworkStage) {
      qBlock = '<div class="oqhold oqhold--rework"><span class="oqhold__tag">\u21BB REWORK</span>' +
        "Reworking from " + esc(d.reworkStage) + ".</div>";
    }

    overlay.querySelector(".odetail").innerHTML =
      '<div class="odetail__head">' +
        '<div><div class="odetail__eyebrow">ORDER DETAIL · LINE-AWARE ROUTING</div>' +
        '<h2 class="odetail__title">' + d.code + '  <span class="odetail__prod">' +
          d.product + " · " + d.family + " · " + d.lineName + "</span></h2>" +
        '<div class="odetail__meta">\u21c4 Source: ' + d.source +
          (d.updatedAgo ? " · " + d.updatedAgo : "") + "</div></div>" +
        '<div class="odetail__head-actions">' + statusPill +
          '<button class="odetail__close" aria-label="Close">\u00d7</button></div>' +
      "</div>" +
      statusBanner +
      lockBlock +
      qBlock +
      splitBlock +
      '<div class="ostepper">' + steps + "</div>" +
      analysisHtml +
      override +
      '<details class="ohist" data-slot="status-history">' +
        '<summary class="ohist__summary">' +
          '<span>STATUS HISTORY</span>' +
          '<span class="ohist__tools">' +
            '<button class="ohist__export" data-hist="export" title="Export status history">\u2193 Export</button>' +
            '<span class="ohist__chev">\u25be</span>' +
          "</span></summary>" +
        '<div class="oaudits">' + (audit || '<div class="muted sm" style="padding:8px 2px">No status history yet.</div>') + "</div>" +
      "</details>";

    overlay.querySelector(".odetail__close").addEventListener("click", function () { overlay.remove(); });

    // export status history as CSV
    var exportBtn = overlay.querySelector("[data-hist='export']");
    if (exportBtn) exportBtn.addEventListener("click", function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      var rows = (d.audit || []).map(function (x) {
        return [x.ts || "", (x.title || "").replace(/"/g, '""'),
                (x.detail || x.by || "").replace(/"/g, '""')];
      });
      var csv = "Timestamp,Event,Detail\n" +
        rows.map(function (r) { return '"' + r.join('","') + '"'; }).join("\n");
      var blob = new Blob([csv], { type: "text/csv" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = d.code + "_status_history.csv";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });

    // clicking a linked split code re-opens that order's panel
    overlay.querySelectorAll("[data-open]").forEach(function (b) {
      b.addEventListener("click", function () { openOrderDetail(b.dataset.open); });
    });

    // split dialog (heads/admin only)
    var splitOpen = overlay.querySelector("[data-split='open']");
    if (splitOpen) {
      splitOpen.addEventListener("click", function () {
        showSplitDialog(overlay, d);
      });
    }

    // #4 lock / unlock
    function actorInfo() {
      var u = (window.MStore && window.MStore.user) || {};
      return { by: u.name || "Department Head", role: u.role || "Department Head" };
    }
    var lockOn = overlay.querySelector("[data-lock='on']");
    var lockOff = overlay.querySelector("[data-lock='off']");
    if (lockOn) lockOn.addEventListener("click", async function () {
      var a = actorInfo();
      var reason = prompt("Protect " + d.code + " — reason (optional):", "Prioritised — hold its slot") || "Protected by planner";
      await fetch("/api/order/" + d.code + "/lock", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ locked: true, reason: reason, by: a.by, role: a.role }) });
      overlay.remove(); openOrderDetail(d.code);
      if (window.MPage && window.MPage.render) window.MPage.render(window.MStore);
    });
    if (lockOff) lockOff.addEventListener("click", async function () {
      var a = actorInfo();
      await fetch("/api/order/" + d.code + "/lock", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ locked: false, by: a.by, role: a.role }) });
      overlay.remove(); openOrderDetail(d.code);
      if (window.MPage && window.MPage.render) window.MPage.render(window.MStore);
    });

    // #5 quality hold resolution
    async function resolveQuality(action, stage) {
      var a = actorInfo();
      var r = await fetch("/api/order/" + d.code + "/quality", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action, stage: stage, by: a.by, role: a.role }) });
      var out = await r.json();
      if (!r.ok) { alert(out.error || "Could not resolve the hold."); return; }
      overlay.remove(); openOrderDetail(d.code);
      if (window.MPage && window.MPage.render) window.MPage.render(window.MStore);
    }
    var qRelease = overlay.querySelector("[data-q='release']");
    if (qRelease) qRelease.addEventListener("click", function () { resolveQuality("release"); });
    var qRework = overlay.querySelector("[data-q='rework']");
    if (qRework) qRework.addEventListener("click", function () {
      var st = overlay.querySelector("[data-q='stage']").value;
      resolveQuality("rework", st);
    });
    var qScrap = overlay.querySelector("[data-q='scrap']");
    if (qScrap) qScrap.addEventListener("click", function () {
      if (confirm("Scrap " + d.code + "? This retires the order.")) resolveQuality("scrap");
    });

    if (canWrite) {
      overlay.querySelector("[data-ov='apply']").addEventListener("click", async function () {
        var status = overlay.querySelector("[data-ov='status']").value;
        var reason = overlay.querySelector("[data-ov='reason']").value.trim();
        if (!reason) { overlay.querySelector("[data-ov='reason']").focus(); return; }
        try {
          await fetch("/api/order/" + code + "/override", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: status, reason: reason })
          });
          overlay.remove();
          if (window.MStore) await window.MStore.refresh();
          if (window.MPage) window.MPage.render(window.MStore);
        } catch (e) { /* ignore */ }
      });
    }
  }

  function showSplitDialog(overlay, d) {
    var LINES = { PT: "Line 1", TT: "Line 2", DP: "Line 3", LT: "Line 4" };
    var total = d.qty || 1;
    var existing = overlay.querySelector(".osplit-dialog");
    if (existing) { existing.remove(); return; }

    var opts = Object.keys(LINES).map(function (lc) {
      return '<option value="' + lc + '"' + (lc === d.line ? " selected" : "") +
        ">" + LINES[lc] + " (" + lc + ")</option>";
    }).join("");

    var dlg = document.createElement("div");
    dlg.className = "osplit-dialog";
    dlg.innerHTML =
      '<div class="osplit-dialog__title">Split ' + d.code + " (" + total + " pcs)</div>" +
      '<div class="osplit-dialog__row">' +
        '<label>Part A keeps ' + (LINES[d.line] || d.line) + ' at the current stage' +
          '<div class="osplit-dialog__pctwrap">' +
            '<input type="range" min="10" max="90" step="5" value="60" data-s="pct">' +
            '<span data-s="pctlabel">60%</span>' +
          "</div></label>" +
      "</div>" +
      '<div class="osplit-dialog__row">' +
        '<label>Part B moves to<select data-s="lineb">' + opts + "</select></label>" +
        '<span class="osplit-dialog__split" data-s="preview"></span>' +
      "</div>" +
      '<p class="osplit-dialog__note">Part A stays on its line at the current stage; ' +
        "Part B restarts at Kitting on the chosen line. Both appear on the board & plan; " +
        "this order is retired. The action is logged.</p>" +
      '<div class="osplit-dialog__actions">' +
        '<button class="btn btn--primary btn--sm" data-s="do">Split order</button>' +
        '<button class="btn btn--sm" data-s="cancel">Cancel</button>' +
      "</div>";

    var action = overlay.querySelector(".osplit--action");
    if (action) action.appendChild(dlg); else overlay.querySelector(".odetail").appendChild(dlg);

    var pct = dlg.querySelector("[data-s='pct']");
    var pctLabel = dlg.querySelector("[data-s='pctlabel']");
    var preview = dlg.querySelector("[data-s='preview']");
    function refresh() {
      var a = Math.max(1, Math.round(total * pct.value / 100));
      var b = total - a; if (b < 1) { a = total - 1; b = 1; }
      pctLabel.textContent = pct.value + "%";
      preview.textContent = "A: " + a + " pcs  ·  B: " + b + " pcs";
    }
    pct.addEventListener("input", refresh); refresh();

    dlg.querySelector("[data-s='cancel']").addEventListener("click", function () { dlg.remove(); });
    dlg.querySelector("[data-s='do']").addEventListener("click", async function () {
      var u = (window.MStore && window.MStore.user) || {};
      var btn = dlg.querySelector("[data-s='do']"); btn.disabled = true; btn.textContent = "Splitting…";
      try {
        var res = await fetch("/api/order/" + d.code + "/split", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pct_a: parseInt(pct.value, 10),
            line_b: dlg.querySelector("[data-s='lineb']").value,
            by: u.name || "Department Head", role: u.role || "Department Head"
          })
        });
        var out = await res.json();
        if (!res.ok) { alert(out.error || "Could not split the order."); btn.disabled = false; btn.textContent = "Split order"; return; }
        overlay.remove();
        if (window.MStore && window.MStore.refresh) await window.MStore.refresh();
        if (window.MPage) window.MPage.render(window.MStore);
      } catch (e) { alert("Could not split the order."); btn.disabled = false; btn.textContent = "Split order"; }
    });
  }

  window.openOrderDetail = openOrderDetail;
  window.openOrderDetailShared = openOrderDetail;
})(window, document);
