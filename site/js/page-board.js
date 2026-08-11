/* Schedule Board — time-based Gantt matching the design:
   - three labelled shifts across a 24h axis (1440 min)
   - line rows (Line 1-4): each order positioned by its REAL current-stage times
   - downstream stage rows (Final QC, Documentation, Packing, Dispatch)
   - named-resource rows (Burn-in B1/B2, Calibration C1-C3): an order in that
     resource appears in its row at the scheduled time
   Data from /api/board (live engine schedule). */
(function (window, document) {
  "use strict";
  var _lastBoard = null;   // last /api/board payload, for drag-reorder math
  // The board axis starts at Shift A (06:00 = 360 min) and spans one full
  // working cycle of three shifts through 06:00 the next day (360 -> 1800).
  var AXIS_START = 360;          // 06:00
  var DAY_MIN = 1440;            // window length in minutes (06:00 -> 06:00)
  function pct(min) {
    return Math.max(0, Math.min(100, ((min - AXIS_START) / DAY_MIN) * 100));
  }

  var STATUS_CLASS = {
    "RUNNING": "is-running", "ON TRACK": "is-ontrack",
    "RESCHEDULED": "is-resched", "HALTED": "is-halted",
    "RUSH": "is-rush", "AT RISK": "is-risk", "DONE": "is-done"
  };

  function block(o, b, opts) {
    opts = opts || {};
    var left = pct(b.startMin), right = pct(b.endMin);
    var width = Math.max(right - left, 3);
    var el = document.createElement("div");
    el.className = "gblock " + (STATUS_CLASS[o.status] || "is-ontrack");
    if (o.rush) el.classList.add("is-rush");
    el.style.left = left.toFixed(2) + "%";
    el.style.width = "calc(" + width.toFixed(2) + "% - 4px)";
    el.dataset.code = o.code;

    var badge = o.rush ? '<span class="gblock__badge">RUSH</span>' : "";
    var splitBadge = o.splitPart
      ? '<span class="gblock__badge gblock__badge--split">SPLIT ' + o.splitPart + "</span>" : "";
    var lockBadge = o.locked ? '<span class="gblock__badge gblock__badge--lock" title="Protected">\uD83D\uDD12</span>' : "";
    var holdBadge = o.qhold ? '<span class="gblock__badge gblock__badge--hold">HOLD</span>' : "";
    var staleBadge = o.stale ? '<span class="gblock__badge gblock__badge--stale" title="' + (o.staleNote || "stale") + '">\u23F1 STALE</span>' : "";
    var prog = (o.stageIndex && o.stageTotal) ? o.stageIndex + "/" + o.stageTotal : "";

    // On the bar: "SO-1044 · Burn-in 7/13", then dotted stage segments.
    var stageTxt = (opts.showStage !== false && o.currentStage)
      ? '<span class="gblock__stage">· ' + o.currentStage +
        (prog ? ' ' + prog : '') + '</span>' : '';

    // dotted stage segments (done = solid, remaining = faint)
    var ticks = "";
    if (o.stageTotal) {
      ticks = '<div class="gblock__ticks">';
      for (var i = 0; i < o.stageTotal; i++) {
        ticks += '<i class="' + (i < o.stageIndex ? "on" : "") + '"></i>';
      }
      ticks += "</div>";
    }

    el.innerHTML =
      '<div class="gblock__line">' +
        '<span class="gblock__code">' + o.code + '</span>' +
        stageTxt + splitBadge + lockBadge + holdBadge + badge +
      '</div>' + ticks +
      '<div class="gblock__hover">' +
        '<b>' + o.code + (o.splitPart ? '  · part ' + o.splitPart : '') + (o.rush ? '  RUSH' : '') + '</b>' +
        '<span>' + o.product + (o.line ? ' · ' + o.line : '') + '</span>' +
        '<span>at ' + (o.currentStage || '') + (prog ? '  ·  step ' + prog : '') + '</span>' +
        (o.updatedAgo ? '<span class="gblock__ago">\u21c4 ' + o.updatedAgo +
          (o.updateSource ? ' · ' + o.updateSource : '') + '</span>' : '') +
      '</div>';

    el.title = o.code + " · " + o.product + " · " + (o.currentStage || b.stage);
    el._startMin = b.startMin;
    el._endMin = b.endMin;
    // top rows: flip the hover popover downward so it isn't clipped at the top
    el.addEventListener("mouseenter", function () {
      var hv = el.querySelector(".gblock__hover");
      if (!hv) return;
      var r = el.getBoundingClientRect();
      if (r.top < 160) {                 // near the top of the board
        hv.style.bottom = "auto";
        hv.style.top = "calc(100% + 6px)";
      } else {
        hv.style.top = "auto";
        hv.style.bottom = "calc(100% + 6px)";
      }
    });
    el.addEventListener("click", function () {
      if (el._dragging) return;   // suppress click that follows a drag
      // Use the shared full order-detail panel (order-detail.js) so the board
      // shows the same parameters as the dashboard (status+reason, split, lock,
      // planned-vs-actual). Fall back to the local panel if it isn't present.
      if (window.openOrderDetailShared) window.openOrderDetailShared(o.code);
      else openOrderDetail(o.code);
    });

    // Whole-board drag: heads can drag a bar onto ANOTHER line to move the
    // order across lines (preview -> approve), or within its line to reorder.
    if (canWrite() && o.line) {
      el.draggable = true;
      el.dataset.line = o.line;
      el.classList.add("gblock--draggable");
      el.addEventListener("dragstart", function (ev) {
        el._dragging = true;
        el.classList.add("is-dragging");
        ev.dataTransfer.effectAllowed = "move";
        try {
          ev.dataTransfer.setData("text/plain",
            JSON.stringify({ code: o.code, line: o.line, product: o.product,
                             locked: !!o.locked, lockReason: o.lockReason || "" }));
        } catch (e) {}
      });
      el.addEventListener("dragend", function () {
        el.classList.remove("is-dragging");
        clearLineDropHints();
        setTimeout(function () { el._dragging = false; }, 60);
      });
    }
    return el;
  }

  function ganttRow(label, sub, color, children, opts) {
    opts = opts || {};
    var row = document.createElement("div");
    row.className = "grow" + (opts.support ? " grow--support" : "");
    var head = document.createElement("div");
    head.className = "grow__head";
    var lr = opts.reorder;
    head.innerHTML =
      '<div class="grow__head-top">' +
        (color ? '<span class="grow__swatch" style="background:' + color + '"></span>' : "") +
        '<div><div class="grow__label">' + label +
          (lr && lr.manual ? ' <span class="grow__manual-tag">MANUAL</span>' : "") + "</div>" +
        (sub ? '<div class="grow__sub">' + sub + "</div>" : "") + "</div>" +
      "</div>";
    var track = document.createElement("div");
    track.className = "grow__track";
    if (opts.emptyText && (!children || !children.length)) {
      var e = document.createElement("div");
      e.className = "grow__empty";
      e.textContent = opts.emptyText;
      track.appendChild(e);
    }
    // Stack overlapping blocks into lanes so they never overlap.
    var lanes = [];
    (children || []).forEach(function (c) {
      var s = c._startMin != null ? c._startMin : 0;
      var e2 = c._endMin != null ? c._endMin : s + 1;
      var laneIdx = -1;
      for (var i = 0; i < lanes.length; i++) {
        var fits = lanes[i].every(function (b) { return s >= b.e || e2 <= b.s; });
        if (fits) { laneIdx = i; break; }
      }
      if (laneIdx === -1) { lanes.push([]); laneIdx = lanes.length - 1; }
      lanes[laneIdx].push({ s: s, e: e2 });
      c.dataset.lane = String(laneIdx);
      track.appendChild(c);
    });
    var laneCount = Math.max(1, lanes.length);
    track.style.setProperty("--lanes", String(laneCount));
    Array.prototype.forEach.call(track.querySelectorAll(".gblock"), function (b) {
      var li = parseInt(b.dataset.lane || "0", 10);
      b.style.top = "calc(" + li + " * (100% / var(--lanes)) + 3px)";
      b.style.height = "calc((100% / var(--lanes)) - 6px)";
    });
    row.style.minHeight = Math.max(56, laneCount * 50) + "px";
    row.appendChild(head); row.appendChild(track);
    return row;
  }

  function shiftHeader(shifts, nowMin) {
    var wrap = document.createElement("div");
    wrap.className = "gantt__shifts";
    var spacer = document.createElement("div");
    spacer.className = "gantt__shifts-head";
    spacer.textContent = "RESOURCE";
    wrap.appendChild(spacer);
    var band = document.createElement("div");
    band.className = "gantt__shifts-band";
    shifts.forEach(function (s) {
      var seg = document.createElement("div");
      seg.className = "gantt__shift";
      seg.style.left = pct(s.startMin).toFixed(2) + "%";
      seg.style.width = (pct(s.endMin) - pct(s.startMin)).toFixed(2) + "%";
      seg.innerHTML = "<b>" + s.label + "</b> <span>" + s.time + "</span>";
      band.appendChild(seg);
    });
    if (nowMin != null) {
      var now = document.createElement("div");
      now.className = "gantt__now";
      now.style.left = pct(nowMin).toFixed(2) + "%";
      band.appendChild(now);
    }
    wrap.appendChild(band);
    return wrap;
  }

  function ruler() {
    var r = document.createElement("div");
    r.className = "gantt__ruler2";
    var head = document.createElement("div");
    head.className = "gantt__ruler-head";
    r.appendChild(head);
    var ticks = document.createElement("div");
    ticks.className = "gantt__ruler-ticks";
    // walk every 2h from the axis start (06:00) to the end of the window,
    // labelling hours mod 24 so the axis reads 06 08 … 22 00 02 04 06.
    for (var m = AXIS_START; m <= AXIS_START + DAY_MIN; m += 120) {
      var t = document.createElement("span");
      t.className = "gtick";
      t.style.left = pct(m).toFixed(2) + "%";
      t.textContent = String(Math.floor(m / 60) % 24).padStart(2, "0");
      ticks.appendChild(t);
    }
    r.appendChild(ticks);
    return r;
  }

  async function render(store) {
    var host = document.querySelector("[data-slot='gantt-rows']");
    if (!host) return;
    host.innerHTML = "";
    var rulerHost = document.querySelector("[data-slot='ruler']");
    if (rulerHost) rulerHost.innerHTML = "";

    var data;
    try {
      var res = await fetch("/api/board", { headers: { "Accept": "application/json" } });
      data = await res.json();
    } catch (e) {
      host.innerHTML = '<div class="grow__empty">Could not load the live schedule.</div>';
      return;
    }
    if (data.error) {
      host.innerHTML = '<div class="grow__empty">' + data.error + "</div>";
      return;
    }
    _lastBoard = data;   // for drag-reorder position math

    host.appendChild(shiftHeader(data.shifts, data.nowMin));
    host.appendChild(ruler());

    data.lineRows.forEach(function (lr) {
      var blocks = [];
      lr.orders.forEach(function (o) {
        if (o.current) blocks.push(block(o, o.current, { stageLabel: "at " + o.current.stage }));
      });
      var row = ganttRow(lr.name, lr.family + " \u00b7 " + lr.key, lr.color, blocks,
        { emptyText: "no active work", reorder: lr });
      row.dataset.line = lr.key;
      makeLineDropTarget(row, lr.key);
      host.appendChild(row);
    });

    (data.downstreamRows || []).forEach(function (dr) {
      var blocks = dr.blocks.map(function (b) {
        return block({ code: b.code, product: b.product, status: b.status, rush: false },
          b, { showStage: false });
      });
      host.appendChild(ganttRow(dr.name, "Quality & dispatch", "var(--phase-quality)",
        blocks, { emptyText: "no work at this stage" }));
    });

    var sep = document.createElement("div");
    sep.className = "gantt__section";
    sep.textContent = "NAMED WORK-CENTRES";
    host.appendChild(sep);

    (data.resourceRows || []).forEach(function (rr) {
      var blocks = rr.blocks.map(function (b) {
        var el = block({ code: b.code, product: b.product, status: b.status, rush: false },
          b, { showStage: false, product: false });
        if (b.isCurrent) el.classList.add("is-current-here");
        return el;
      });
      // maintenance / downtime windows for this chamber/bench
      (rr.maintenance || []).forEach(function (m) {
        blocks.push(maintenanceBlock(m));
      });
      var subLabel = rr.group + (rr.state === "maintenance" ? " \u00b7 under maintenance"
        : (rr.state === "down" ? " \u00b7 offline" : ""));
      var row = ganttRow(rr.name, subLabel, "var(--ink-3, #9aa7b2)", blocks,
        { support: true, emptyText: "available \u00b7 no work scheduled" });
      if (rr.state && rr.state !== "available") row.classList.add("grow--" + rr.state);
      host.appendChild(row);
    });
  }

  // A maintenance / downtime window drawn on a resource (chamber/bench) row.
  function maintenanceBlock(m) {
    var left = pct(m.startMin), right = pct(m.endMin);
    var width = Math.max(right - left, 4);
    var el = document.createElement("div");
    el.className = "gblock gmaint gmaint--" + (m.kind || "maintenance");
    el.style.left = left.toFixed(2) + "%";
    el.style.width = "calc(" + width.toFixed(2) + "% - 4px)";
    el.innerHTML =
      '<div class="gblock__line"><span class="gmaint__ico">\u2699</span>' +
      '<span class="gblock__code">' + (m.label || "Maintenance") + "</span></div>" +
      '<div class="gblock__hover"><b>' + (m.label || "Maintenance") + "</b>" +
      (m.note ? "<span>" + esc(m.note) + "</span>" : "") +
      (m.code ? '<span class="gmaint__code">' + m.code + "</span>" : "") + "</div>";
    return el;
  }

  // ---- order detail panel ----------------------------------------------
  async function openOrderDetail(code) {
    var existing = document.querySelector(".odetail-overlay");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.className = "odetail-overlay";
    overlay.innerHTML = '<div class="odetail"><div class="odetail__loading">Loading ' + code + '…</div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.remove();
    });

    var d;
    try {
      d = await fetch("/api/order/" + code).then(function (r) { return r.json(); });
    } catch (e) {
      overlay.querySelector(".odetail").innerHTML = "Could not load " + code;
      return;
    }
    if (d.error) { overlay.querySelector(".odetail").innerHTML = d.error; return; }

    var canWrite = window.MStore && window.MStore.canWrite;

    var steps = d.steps.map(function (s) {
      var mark = s.state === "done" ? "\u2713" : "";
      return '<div class="ostep is-' + s.state + '">' +
        '<div class="ostep__dot">' + mark + "</div>" +
        '<div class="ostep__name">' + s.stage + "</div>" +
        '<div class="ostep__state">' + s.state + "</div></div>";
    }).join('<div class="ostep__link"></div>');

    var refs = d.reference.map(function (r) {
      return '<div class="oref"><div class="oref__label">' + r.label + "</div>" +
        '<div class="oref__val">' + r.value + "</div>" +
        '<div class="oref__src">\uD83D\uDD12 ' + r.source + "</div></div>";
    }).join("");

    var audit = d.audit.length ? d.audit.map(function (a) {
      return '<div class="oaudit"><div class="oaudit__ts">' + a.ts + "</div>" +
        '<div><div class="oaudit__title">' + a.title + "</div>" +
        (a.actor ? '<div class="oaudit__who">' + a.actor + "</div>" : "") + "</div></div>";
    }).join("") : '<div class="muted sm">No history yet.</div>';

    var statusOpts = d.statuses.map(function (s) {
      return '<option' + (s === d.status ? " selected" : "") + ">" + s + "</option>";
    }).join("");

    var override = canWrite ? (
      '<div class="ooverride">' +
      '<div class="ooverride__head">Planner override</div>' +
      '<p class="ooverride__note">Manually correct status. A reason is required; the change is logged and tagged <b>Planner override</b> so it is never mistaken for floor reality.</p>' +
      '<div class="ooverride__row">' +
      '<label>New status<select data-ov="status">' + statusOpts + "</select></label>" +
      '<label class="grow1">Reason (required)<input data-ov="reason" placeholder="Why is this being overridden?"></label>' +
      '<button class="btn btn--primary" data-ov="apply">Apply override</button>' +
      "</div></div>") : "";

    var STATUS_PILL = {
      "RUNNING": "is-running", "ON TRACK": "is-ontrack",
      "RESCHEDULED": "is-resched", "HALTED": "is-halted",
      "RUSH": "is-rush", "AT RISK": "is-risk", "DONE": "is-done"
    };
    var statusPill = d.status
      ? '<span class="odetail__status ' + (STATUS_PILL[d.status] || "is-ontrack") +
        '">' + d.status + "</span>"
      : "";

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
      '<div class="ostepper">' + steps + "</div>" +
      '<div class="odetail__section">REFERENCE DATA · READ-ONLY · Knowledge Centre</div>' +
      '<div class="orefs">' + refs + "</div>" +
      override +
      '<div class="odetail__section">STATUS HISTORY · AUDIT TRAIL</div>' +
      '<div class="oaudits">' + audit + "</div>";

    overlay.querySelector(".odetail__close").addEventListener("click", function () {
      overlay.remove();
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
  // Expose the board's local panel under a distinct name; the SHARED panel from
  // order-detail.js owns window.openOrderDetail (it loads after this file).
  window.openOrderDetailBoard = openOrderDetail;

  // ---- forward plan (week / month) --------------------------------------
  var STATUS_C = {
    "RUNNING": "is-running", "ON TRACK": "is-ontrack", "RESCHEDULED": "is-resched",
    "HALTED": "is-halted", "RUSH": "is-rush", "AT RISK": "is-risk", "DONE": "is-done"
  };

  function planPct(min, spanMin) {
    return Math.max(0, Math.min(100, (min / spanMin) * 100));
  }

  function dayGrid(marks, spanMin) {
    var g = document.createElement("div");
    g.className = "plan__grid";
    marks.forEach(function (m) {
      var line = document.createElement("div");
      line.className = "plan__gridline" + (m.weekend ? " is-weekend" : "");
      line.style.left = planPct(m.min, spanMin).toFixed(2) + "%";
      g.appendChild(line);
    });
    return g;
  }

  function dayHeader(marks, spanMin) {
    var h = document.createElement("div");
    h.className = "plan__days";
    var spacer = document.createElement("div");
    spacer.className = "plan__days-head";
    h.appendChild(spacer);
    var band = document.createElement("div");
    band.className = "plan__days-band";
    marks.slice(0, -1).forEach(function (m, i) {
      var d = document.createElement("div");
      d.className = "plan__day" + (m.weekend ? " is-weekend" : "");
      d.style.left = planPct(m.min, spanMin).toFixed(2) + "%";
      d.style.width = planPct(marks[i + 1].min - m.min, spanMin).toFixed(2) + "%";
      d.textContent = m.label;
      band.appendChild(d);
    });
    h.appendChild(band);
    return h;
  }

  function planBar(b, spanMin, opts) {
    opts = opts || {};
    var left = planPct(b.startMin, spanMin);
    var width = Math.max(planPct(b.endMin, spanMin) - left, 0.8);
    var el = document.createElement("div");
    el.className = "pbar " + (STATUS_C[b.status] || "is-ontrack");
    if (b.rush) el.classList.add("is-rush");
    el.style.left = left.toFixed(2) + "%";
    el.style.width = width.toFixed(2) + "%";
    var label = opts.stage ? (b.code + " · " + b.stage) : b.code;
    var stag = b.splitPart ? '<span class="pbar__splittag">' + b.splitPart + "</span>" : "";
    if (b.splitPart) el.classList.add("is-split");
    el.innerHTML = '<span class="pbar__txt">' + label + stag + "</span>";
    el.title = b.code + (b.splitPart ? " (part " + b.splitPart + ")" : "") +
      " · " + b.product + (b.stage ? " · " + b.stage : "") +
      (b.due ? " · due " + b.due : "");
    return el;
  }

  function planRow(label, sub, color, children, spanMin, marks) {
    var row = document.createElement("div");
    row.className = "prow";
    var head = document.createElement("div");
    head.className = "prow__head";
    head.innerHTML =
      (color ? '<span class="prow__swatch" style="background:' + color + '"></span>' : "") +
      '<div><div class="prow__label">' + label + "</div>" +
      (sub ? '<div class="prow__sub">' + sub + "</div>" : "") + "</div>";
    var track = document.createElement("div");
    track.className = "prow__track";
    track.appendChild(dayGrid(marks, spanMin));
    if (!children.length) {
      var e = document.createElement("div");
      e.className = "grow__empty";
      e.textContent = "—";
      track.appendChild(e);
    }
    children.forEach(function (c) { track.appendChild(c); });
    row.appendChild(head);
    row.appendChild(track);
    return row;
  }

  async function renderPlan(scope) {
    var host = document.querySelector("[data-slot='gantt-rows']");
    if (!host) return;
    host.innerHTML = "";
    var rulerHost = document.querySelector("[data-slot='ruler']");
    if (rulerHost) rulerHost.innerHTML = "";

    var data;
    try {
      var res = await fetch("/api/plan?scope=" + scope, { headers: { "Accept": "application/json" } });
      data = await res.json();
    } catch (e) {
      host.innerHTML = '<div class="grow__empty">Could not load the plan.</div>';
      return;
    }
    if (data.error) { host.innerHTML = '<div class="grow__empty">' + data.error + "</div>"; return; }

    host.appendChild(dayHeader(data.dayMarks, data.spanMin));

    if (scope === "week") {
      // per-stage bars grouped by named resource (contention view)
      var sep = document.createElement("div");
      sep.className = "gantt__section";
      sep.textContent = "WORK-CENTRE LOADING · NEXT 7 DAYS";
      host.appendChild(sep);
      data.resourceRows.forEach(function (rr) {
        var bars = rr.bars.map(function (b) { return planBar(b, data.spanMin, { stage: true }); });
        host.appendChild(planRow(rr.name, rr.group, "var(--ink-3,#9aa7b2)",
          bars, data.spanMin, data.dayMarks));
      });
    } else {
      // order-level bars grouped by line (commitment view)
      var sep2 = document.createElement("div");
      sep2.className = "gantt__section";
      sep2.textContent = "ORDER DELIVERY PLAN · NEXT 5 WEEKS";
      host.appendChild(sep2);
      data.lineRows.forEach(function (lr) {
        // Lane-pack: assign each bar to the first vertical lane where it doesn't
        // overlap an earlier bar's time range, so overlapping orders stack into
        // separate rows instead of hiding each other.
        var sorted = (lr.bars || []).slice().sort(function (a, b) {
          return (a.startMin || 0) - (b.startMin || 0);
        });
        var lanes = [];   // each lane = end-min of its last bar
        sorted.forEach(function (b) {
          var placed = false;
          for (var i = 0; i < lanes.length; i++) {
            if ((b.startMin || 0) >= lanes[i] - 0.001) {   // fits after last bar in lane
              b._lane = i; lanes[i] = (b.endMin || 0); placed = true; break;
            }
          }
          if (!placed) { b._lane = lanes.length; lanes.push(b.endMin || 0); }
        });
        var laneCount = Math.max(1, lanes.length);
        var bars = sorted.map(function (b) {
          var el = planBar(b, data.spanMin, { stage: false });
          el.style.top = (4 + b._lane * 26) + "px";
          el.style.height = "22px";
          return el;
        });
        var row = planRow(lr.name, lr.bars.length + " orders", lr.color,
          bars, data.spanMin, data.dayMarks);
        // grow the track to fit all lanes
        var trk = row.querySelector(".prow__track");
        trk.style.minHeight = (8 + laneCount * 26) + "px";
        // add due markers into the track
        lr.bars.forEach(function (b) {
          if (b.dueMin != null) {
            var due = document.createElement("span");
            due.className = "pbar__due";
            due.style.left = planPct(b.dueMin, data.spanMin).toFixed(2) + "%";
            due.title = b.code + " due " + b.due;
            trk.appendChild(due);
          }
        });
        host.appendChild(row);
      });
    }
  }

  // ---- tab wiring -------------------------------------------------------
  function initTabs(store) {
    var tabs = document.querySelector("[data-slot='board-tabs']");
    if (!tabs) return;
    tabs.addEventListener("click", function (e) {
      var btn = e.target.closest(".board-tab");
      if (!btn) return;
      tabs.querySelectorAll(".board-tab").forEach(function (t) { t.classList.remove("is-active"); });
      btn.classList.add("is-active");
      var scope = btn.dataset.scope;
      if (scope === "today") { render(store); }
      else { renderPlan(scope); }
    });
  }

  // wrap render so tabs get wired once on first paint
  var _origRender = render;

  // ---- who is acting (for logging) -------------------------------------
  function actor() {
    var u = (window.MStore && window.MStore.user) || {};
    return { by: u.name || "Department Head", role: u.role || "Department Head" };
  }
  function canWrite() { return !!(window.MStore && window.MStore.canWrite); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---- board drag: reorder within a row, or move across rows ----------
  // A head drags any bar. Dropping it on the SAME line re-sequences the run
  // order (position by where it's dropped, saved + logged). Dropping on a
  // DIFFERENT line opens the cross-line move preview (approve to apply).
  function clearLineDropHints() {
    document.querySelectorAll(".grow.is-drop-target").forEach(function (r) {
      r.classList.remove("is-drop-target");
    });
  }

  function makeLineDropTarget(row, lineKey) {
    if (!canWrite()) return;
    row.addEventListener("dragover", function (ev) {
      var dragging = document.querySelector(".gblock.is-dragging");
      if (!dragging) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      var sameLine = dragging.dataset.line === lineKey;
      if (!row.classList.contains("is-drop-target")) {
        clearLineDropHints();
        row.classList.add("is-drop-target");
        row.classList.toggle("is-drop-reorder", sameLine);
      }
    });
    row.addEventListener("dragleave", function (ev) {
      if (!row.contains(ev.relatedTarget)) {
        row.classList.remove("is-drop-target", "is-drop-reorder");
      }
    });
    row.addEventListener("drop", function (ev) {
      ev.preventDefault();
      var wasReorder = row.classList.contains("is-drop-reorder");
      clearLineDropHints();
      row.classList.remove("is-drop-reorder");
      var payload = null;
      try { payload = JSON.parse(ev.dataTransfer.getData("text/plain")); } catch (e) {}
      if (!payload || !payload.code) return;
      // #4: a locked/protected order needs an explicit override to be moved,
      // whether within its row (reorder) or to another row (move).
      if (payload.locked) {
        if (!confirm("\uD83D\uDD12 " + payload.code + " is protected (" +
            (payload.lockReason || "prioritised") + ").\n\nMove it anyway?")) {
          return;
        }
      }
      if (payload.line === lineKey) {
        // same row -> reorder by drop x-position
        reorderWithinLine(row, lineKey, payload.code, ev.clientX, payload.locked);
      } else {
        openMovePreview(payload.code, lineKey, null, payload.locked);
      }
    });
  }

  // Compute the new run order for a line from where the bar was dropped, then
  // persist it (same endpoint the old chip strip used) and re-render.
  async function reorderWithinLine(row, lineKey, code, dropX, wasLocked) {
    var lr = _lastBoard && (_lastBoard.lineRows || [])
      .filter(function (l) { return l.key === lineKey; })[0];
    if (!lr) return;
    var prevSeq = (lr.orders || [])
      .filter(function (o) { return o.current; })
      .sort(function (a, b) { return (a.current.startMin || 0) - (b.current.startMin || 0); })
      .map(function (o) { return o.code; });
    var seq = prevSeq.slice();
    // where along the row was it dropped? map x to an insert index
    var bars = Array.prototype.slice.call(row.querySelectorAll(".gblock[data-code]"))
      .filter(function (b) { return b.dataset.line === lineKey; });
    var insertAt = seq.length;
    for (var i = 0; i < bars.length; i++) {
      var r = bars[i].getBoundingClientRect();
      if (dropX < r.left + r.width / 2) { insertAt = i; break; }
    }
    seq = seq.filter(function (x) { return x !== code; });
    var target = Math.max(0, Math.min(insertAt, seq.length));
    seq.splice(target, 0, code);
    // no change? do nothing
    if (seq.join(",") === prevSeq.join(",")) return;

    var a = actor();
    try {
      await fetch("/api/board/order", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line: lineKey, sequence: seq, by: a.by, role: a.role }) });
      render(window.MStore);
      showReorderUndoToast(lineKey, prevSeq, seq);
    } catch (e) { /* ignore */ }
  }

  // "Order saved · Undo" toast after a same-row reorder. Undo restores the
  // previous sequence (or clears the manual order if there wasn't one).
  function showReorderUndoToast(lineKey, prevSeq, newSeq) {
    var old = document.querySelector(".move-undo-toast");
    if (old) old.remove();
    var lineNames = { PT: "Line 1", TT: "Line 2", DP: "Line 3", LT: "Line 4" };
    var t = document.createElement("div");
    t.className = "move-undo-toast";
    t.innerHTML = "<span>\u2713 " + (lineNames[lineKey] || lineKey) + " order saved</span>" +
      '<button data-u="undo">Undo</button>' +
      '<button data-u="close" aria-label="Dismiss">\u00d7</button>';
    document.body.appendChild(t);
    var timer = setTimeout(function () { t.remove(); }, 9000);
    t.querySelector("[data-u='close']").addEventListener("click", function () {
      clearTimeout(timer); t.remove();
    });
    t.querySelector("[data-u='undo']").addEventListener("click", async function () {
      clearTimeout(timer);
      var a = actor();
      try {
        await fetch("/api/board/order", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ line: lineKey, sequence: prevSeq, by: a.by, role: a.role }) });
        t.remove();
        render(window.MStore);
      } catch (e) { alert("Could not undo."); }
    });
  }

  async function openMovePreview(code, targetLine, onApproved, preOverridden) {
    var overlay = document.createElement("div");
    overlay.className = "odetail-overlay";
    overlay.innerHTML = '<div class="movedlg"><div class="movedlg__loading">Building preview\u2026</div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });

    var pv;
    try {
      var res = await fetch("/api/board/move/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code, line: targetLine })
      });
      var out = await res.json();
      if (out.noop) { overlay.remove(); return; }
      if (!res.ok) { overlay.querySelector(".movedlg").innerHTML = esc(out.error || "Could not preview."); return; }
      pv = out.preview;
    } catch (e) { overlay.querySelector(".movedlg").innerHTML = "Could not build the preview."; return; }

    function stageList(arr) {
      return arr && arr.length ? arr.map(esc).join(", ") : "\u2014";
    }
    var remark = pv.remark
      ? '<div class="movedlg__remark">\u26a0 ' + esc(pv.remark) + "</div>" : "";
    var dueShift = (pv.oldFinish || pv.newFinish)
      ? '<div class="movedlg__dates">' +
          '<div><span>Projected finish (now)</span><b>' + esc(pv.oldFinish || "\u2014") + "</b></div>" +
          '<div class="movedlg__arrow">\u2192</div>' +
          '<div><span>Projected finish (after move)</span><b>' + esc(pv.newFinish || "\u2014") + "</b></div>" +
        "</div>" : "";

    overlay.querySelector(".movedlg").innerHTML =
      '<div class="movedlg__head"><div class="movedlg__eyebrow">PROPOSED MOVE \u00b7 AWAITING APPROVAL</div>' +
        '<h2 class="movedlg__title">' + esc(pv.code) + ' <span>' + esc(pv.product) +
        " \u00b7 " + pv.qty + " pcs</span></h2>" +
        '<button class="movedlg__close" aria-label="Close">\u00d7</button></div>' +
      '<div class="movedlg__lines">' +
        '<div class="movedlg__line"><span>FROM</span><b>' + esc(pv.fromLine.name) +
          "</b><i>" + esc(pv.fromLine.family) + "</i></div>" +
        '<div class="movedlg__arrow">\u2192</div>' +
        '<div class="movedlg__line movedlg__line--to"><span>TO</span><b>' + esc(pv.toLine.name) +
          "</b><i>" + esc(pv.toLine.family) + "</i></div>" +
      "</div>" +
      '<div class="movedlg__grid">' +
        '<div><span>Lands at stage</span><b>' + esc(pv.newStage || "\u2014") + "</b></div>" +
        '<div><span>Tests NOT performed</span><b class="movedlg__drop">' + stageList(pv.droppedStages) + "</b></div>" +
        '<div><span>Tests now required</span><b class="movedlg__add">' + stageList(pv.addedStages) + "</b></div>" +
      "</div>" +
      dueShift + remark +
      '<div class="movedlg__actions">' +
        '<button class="btn btn--primary" data-m="approve">Approve &amp; move</button>' +
        '<button class="btn" data-m="cancel">Discard</button></div>';

    overlay.querySelector(".movedlg__close").addEventListener("click", function () { overlay.remove(); });
    overlay.querySelector("[data-m='cancel']").addEventListener("click", function () { overlay.remove(); });
    overlay.querySelector("[data-m='approve']").addEventListener("click", async function () {
      var a = actor();
      var btn = overlay.querySelector("[data-m='approve']");
      btn.disabled = true; btn.textContent = "Moving\u2026";
      try {
        var res = await fetch("/api/board/move/apply", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: code, line: targetLine,
                                 override: !!preOverridden, by: a.by, role: a.role })
        });
        var out = await res.json();
        if (res.status === 409 && out.locked) {
          // #4: protected order — warn and let the head override
          btn.disabled = false; btn.textContent = "Approve & move";
          if (confirm("\u26a0 " + code + " is protected (" +
              (out.reason || "prioritised") + ").\n\nMove it anyway?")) {
            var res2 = await fetch("/api/board/move/apply", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ code: code, line: targetLine, override: true, by: a.by, role: a.role })
            });
            var out2 = await res2.json();
            if (!res2.ok) { alert(out2.error || "Could not move the order."); return; }
            overlay.remove();
            if (typeof onApproved === "function") { try { onApproved(); } catch (e) {} }
            render(window.MStore);
            showMoveUndoToast(code, out2.previous);
          }
          return;
        }
        if (!res.ok) { alert(out.error || "Could not move the order."); btn.disabled = false; btn.textContent = "Approve & move"; return; }
        overlay.remove();
        if (typeof onApproved === "function") { try { onApproved(); } catch (e) {} }
        render(window.MStore);
        showMoveUndoToast(code, out.previous);
      } catch (e) { alert("Could not move the order."); btn.disabled = false; btn.textContent = "Approve & move"; }
    });
  }

  // A brief "Moved · Undo" toast after a cross-line move, so a head can revert.
  function showMoveUndoToast(code, previous) {
    if (!previous) return;
    var old = document.querySelector(".move-undo-toast");
    if (old) old.remove();
    var t = document.createElement("div");
    t.className = "move-undo-toast";
    t.innerHTML = "<span>Moved " + code + "</span>" +
      '<button data-u="undo">Undo</button>' +
      '<button data-u="close" aria-label="Dismiss">\u00d7</button>';
    document.body.appendChild(t);
    var timer = setTimeout(function () { t.remove(); }, 8000);
    t.querySelector("[data-u='close']").addEventListener("click", function () {
      clearTimeout(timer); t.remove();
    });
    t.querySelector("[data-u='undo']").addEventListener("click", async function () {
      clearTimeout(timer);
      var a = actor();
      try {
        await fetch("/api/board/move/undo", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: code, previous: previous, by: a.by, role: a.role })
        });
        t.remove();
        render(window.MStore);
      } catch (e) { alert("Could not undo the move."); }
    });
  }


  // ---- floor insights at the bottom of the board ----------------------
  async function renderFloorInsights() {
    var host = document.querySelector("[data-slot='floor-insights']");
    if (!host) return;
    try {
      var items = await fetch("/api/floor-insights").then(function (r) { return r.json(); });
      var decisions = {};
      try {
        decisions = await fetch("/api/floor-insights/decisions").then(function (r) { return r.json(); });
      } catch (e) { decisions = {}; }

      host.innerHTML = "";
      (items || []).forEach(function (s) {
        var card = document.createElement("div");
        card.className = "in-card in-card--" + (s.kind || "info").toLowerCase();
        var dec = decisions[s.id];

        var head =
          '<div class="in-card__head"><span class="in-kind">' + esc(s.kind || "") + "</span>" +
          (s.ref ? '<span class="in-ref">' + esc(s.ref) + "</span>" : "") + "</div>" +
          '<div class="in-card__title">' + esc(s.title) + "</div>" +
          '<div class="in-card__detail">' + esc(s.detail) + "</div>" +
          (s.gain ? '<div class="in-card__gain">' + esc(s.gain) + "</div>" : "");

        card.innerHTML = head;
        renderInsightState(card, s, dec);
        host.appendChild(card);
      });
      if (!items || !items.length)
        host.innerHTML = '<p class="muted sm">Floor is balanced \u2014 no actions needed.</p>';
    } catch (e) { /* leave as-is */ }
  }

  // Render the decision area of a card: existing decision, action buttons,
  // or the remarks box (when rejecting). Re-called after each state change.
  function renderInsightState(card, insight, dec) {
    var old = card.querySelector(".in-card__actions, .in-remarks, .in-decided");
    while (old) { old.remove(); old = card.querySelector(".in-card__actions, .in-remarks, .in-decided"); }
    card.classList.remove("is-decided", "is-rejected");

    if (dec) {
      card.classList.add("is-decided");
      if (dec.decision === "rejected") card.classList.add("is-rejected");
      var when = dec.ts ? new Date(dec.ts).toLocaleString() : "";
      var box = document.createElement("div");
      box.className = "in-decided in-decided--" + dec.decision;
      box.innerHTML =
        '<span class="in-decided__tag">' + (dec.decision === "applied" ? "APPLIED" : "REJECTED") + "</span>" +
        "<div>" + (dec.remarks ? esc(dec.remarks) : (dec.decision === "applied" ? "Accepted by planner." : "Dismissed.")) +
        '<div class="in-decided__who">' + esc(dec.decided_by || "") + (when ? " \u00b7 " + esc(when) : "") + "</div></div>";
      card.appendChild(box);
      // allow changing the decision (heads only)
      if (canWrite()) {
        var again = document.createElement("div");
        again.className = "in-card__actions";
        again.innerHTML = '<button class="in-act" data-act="reopen">Change decision</button>';
        again.querySelector("[data-act='reopen']").addEventListener("click", function () {
          renderInsightState(card, insight, null);
        });
        card.appendChild(again);
      }
      return;
    }

    if (!canWrite()) return;   // employees see insights but can't decide

    var act = insight.action || null;
    var isSplit = (insight.kind || "").toUpperCase() === "SPLIT" ||
      (act && act.type === "split");
    var isMove = act && act.type === "move";
    var isOpen = act && act.type === "open";
    var applyLabel = isSplit ? "Apply \u2014 split order\u2026"
      : (isMove ? "Apply \u2014 preview move\u2026"
      : (isOpen ? "Review order\u2026" : "Apply"));

    var actions = document.createElement("div");
    actions.className = "in-card__actions";
    actions.innerHTML =
      '<button class="in-act in-act--apply" data-act="apply">' + applyLabel + "</button>" +
      (isOpen ? "" : '<button class="in-act in-act--reject" data-act="reject">Reject\u2026</button>');
    card.appendChild(actions);

    actions.querySelector("[data-act='apply']").addEventListener("click", function () {
      if (isMove) {
        openMovePreview(act.order, act.targetLine, function () {
          decide(card, insight, "applied",
            "Moved " + act.order + " to " + act.targetLine + " (approved).");
        });
      } else if (isSplit) {
        var ref = (act && act.order) || (insight.ref || "").trim();
        if (ref && window.openOrderDetail) openOrderDetail(ref);
        decide(card, insight, "applied", "Opened split for " + ref);
      } else if (isOpen) {
        // RISK insight — just open the order; no decision recorded
        if (act.order && window.openOrderDetail) openOrderDetail(act.order);
      } else {
        decide(card, insight, "applied", "");
      }
    });
    var rej = actions.querySelector("[data-act='reject']");
    if (rej) rej.addEventListener("click", function () {
      showRemarks(card, insight);
    });
  }

  function showRemarks(card, insight) {
    var acts = card.querySelector(".in-card__actions");
    if (acts) acts.remove();
    var wrap = document.createElement("div");
    wrap.className = "in-remarks";
    wrap.innerHTML =
      '<textarea placeholder="Why is this being rejected? (required \u2014 logged)"></textarea>' +
      '<div class="in-remarks__row">' +
      '<button class="in-act in-act--reject" data-act="confirm">Reject insight</button>' +
      '<button class="in-act" data-act="cancel">Cancel</button></div>';
    card.appendChild(wrap);
    var ta = wrap.querySelector("textarea");
    ta.focus();
    wrap.querySelector("[data-act='confirm']").addEventListener("click", function () {
      var remarks = ta.value.trim();
      if (!remarks) { ta.focus(); return; }
      decide(card, insight, "rejected", remarks);
    });
    wrap.querySelector("[data-act='cancel']").addEventListener("click", function () {
      renderInsightState(card, insight, null);
    });
  }

  async function decide(card, insight, decision, remarks) {
    var a = actor();
    try {
      var res = await fetch("/api/floor-insights/decide", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: insight.id, title: insight.title, decision: decision,
          remarks: remarks, by: a.by, role: a.role
        })
      });
      var out = await res.json();
      if (!res.ok) { alert(out.error || "Could not record the decision."); return; }
      renderInsightState(card, insight, out.decision);
    } catch (e) { alert("Could not record the decision."); }
  }

  window.MPage = {
    render: function (store) {
      initTabs(store);
      renderFloorInsights();
      var active = document.querySelector(".board-tab.is-active");
      var scope = active ? active.dataset.scope : "today";
      if (scope === "today") return _origRender(store);
      return renderPlan(scope);
    }
  };
})(window, document);
