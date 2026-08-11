/* Quality & Dispatch — approvals + dispatch queue (head),
   today's checklist (employee). */
(function (window, document) {
  "use strict";
  const MU = window.MU, MC = window.MC;

  function render(store) {
    const headView = document.querySelector("[data-region='head-view']");
    const employeeView = document.querySelector("[data-region='employee-view']");
    headView.hidden = !store.canWrite;
    employeeView.hidden = store.canWrite;

    document.querySelector("[data-slot='view']")
      .classList.toggle("view--narrow", !store.canWrite);

    if (store.canWrite) paintHead(store);
    else paintEmployee(store);
  }

  function paintHead(store) {
    renderPendingApprovals(store);
    const confirms = store.confirmations;
    document.querySelector("[data-region='floor-confirms']").hidden = !confirms.length;
    MU.render("[data-slot='floor-confirms']", confirms.map(function (c) {
      const row = MU.fromTemplate("tpl-confirm-row");
      return MU.fill(row, {
        item: c.item, meta: c.order + " · " + c.stage, operator: c.operator
      });
    }));

    const approvals = store.constraints;
    MU.render("[data-slot='approvals']", approvals.length
      ? approvals.map(function (c) {
          return MC.approvalCard(c, store, function () { render(store); });
        })
      : [MU.empty("No constraints or approvals outstanding.")]);
  }

  function paintEmployee(store) {
    const job = store.job;
    const hasJob = Boolean(job.order && job.stage);
    document.querySelector("[data-region='checklist']").hidden = !hasJob;
    document.querySelector("[data-region='no-job']").hidden = hasJob;
    if (!hasJob) return;

    const items = store.checklistFor(job.stage);
    const done = job.done.length;

    MU.fill(document.querySelector("[data-region='checklist']"), {
      order: job.order, stage: job.stage,
      progress: done + " of " + items.length + " complete"
    });
    document.querySelector("[data-slot='progress-fill']").style.width =
      (items.length ? (done / items.length) * 100 : 0) + "%";

    MU.render("[data-slot='checklist']", items.map(function (item) {
      const row = MU.fromTemplate("tpl-check-row");
      const isDone = job.done.indexOf(item) > -1;
      if (isDone) row.classList.add("is-done");
      MU.fill(row, { label: item, box: isDone ? "✓" : "" });
      row.addEventListener("click", function () {
        store.toggleCheck(item);
        render(store);
      });
      return row;
    }));

    // Submit-for-approval control (appears when all items are ticked)
    var checklistCard = document.querySelector("[data-region='checklist']");
    var old = checklistCard.querySelector(".sc-submit");
    if (old) old.remove();
    var wrap = document.createElement("div");
    wrap.className = "sc-submit";
    var allDone = items.length > 0 && done >= items.length;
    if (allDone) {
      wrap.innerHTML =
        '<p class="sc-submit__hint">All items done. Submit this stage for your ' +
        'department head\u2019s approval \u2014 the order advances only once approved.</p>' +
        '<button class="btn btn--primary" data-a="submit">Submit for approval</button>' +
        '<span class="sc-submit__msg"></span>';
    } else {
      wrap.innerHTML = '<p class="sc-submit__hint muted">Tick all ' + items.length +
        ' items to submit this stage for approval.</p>';
    }
    checklistCard.appendChild(wrap);
    var btn = wrap.querySelector("[data-a='submit']");
    if (btn) {
      btn.addEventListener("click", async function () {
        var user = store.user || {};
        try {
          var res = await fetch("/api/stage/submit", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order: job.order, stage: job.stage,
              by: user.name, byId: user.id, itemsDone: done, itemsTotal: items.length })
          }).then(function (r) { return r.json(); });
          if (res.ok) {
            wrap.querySelector(".sc-submit__msg").innerHTML =
              '<span class="sc-ok">\u2713 Submitted for approval. You\u2019ll be notified once your head reviews it.</span>';
            btn.disabled = true;
          } else {
            wrap.querySelector(".sc-submit__msg").innerHTML =
              '<span class="sc-err">' + (res.error || "Could not submit") + "</span>";
          }
        } catch (e) {
          wrap.querySelector(".sc-submit__msg").innerHTML = '<span class="sc-err">Could not submit.</span>';
        }
      });
    }
  }

  window.MPage = { render: render };

  // Head: pending stage submissions -> approve (advances board) / reject (remarks)
  async function renderPendingApprovals(store) {
    var headView = document.querySelector("[data-region='head-view']");
    if (!headView) return;
    var host = headView.querySelector("[data-slot='pending-approvals']");
    if (!host) {
      var card = document.createElement("section");
      card.className = "card card--flush sc-approvals";
      card.innerHTML =
        '<div class="card__head"><h2 class="card__title">STAGE APPROVALS</h2>' +
        '<p class="muted sm">operators submitted these stages \u2014 approve to complete on the board, or reject with remarks</p></div>' +
        '<div data-slot="pending-approvals"></div>';
      headView.insertBefore(card, headView.firstChild);
      host = card.querySelector("[data-slot='pending-approvals']");
    }
    var data;
    try { data = await fetch("/api/stage/submissions?status=submitted").then(function (r) { return r.json(); }); }
    catch (e) { return; }
    var subs = (data && data.submissions) || [];
    if (!subs.length) {
      host.innerHTML = '<p class="muted sm" style="padding:12px 16px">No stages awaiting approval.</p>';
      renderPastApprovals(host, store);
      return;
    }
    host.innerHTML = "";
    subs.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "sc-approval";
      row.innerHTML =
        '<div class="sc-approval__main">' +
          '<div class="sc-approval__title">' + s.order + ' \u00b7 ' + s.stage + "</div>" +
          '<div class="sc-approval__meta">submitted by ' + s.submittedBy +
            ' \u00b7 ' + s.itemsDone + "/" + s.itemsTotal + " items</div>" +
        "</div>" +
        '<div class="sc-approval__acts">' +
          '<button class="btn btn--primary" data-a="approve">Approve &amp; complete</button>' +
          '<button class="btn" data-a="reject">Reject\u2026</button>' +
        "</div>" +
        '<div class="sc-approval__reject" hidden>' +
          '<input type="text" data-a="remarks" placeholder="Remarks (required) \u2014 what needs rework?" />' +
          '<button class="btn" data-a="send">Send back</button>' +
        "</div>";
      var by = (store.user && store.user.name) || "Department Head";

      row.querySelector("[data-a='approve']").addEventListener("click", async function () {
        try {
          var r = await fetch("/api/stage/approve", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: s.id, by: by })
          }).then(function (x) { return x.json(); });
          if (r.ok) {
            store.pushChat && store.pushChat("ai", "Scheduler AI",
              s.order + " \u00b7 " + s.stage + " approved" +
              (r.advancedTo ? " \u2014 advanced to " + r.advancedTo + " on the board." : " \u2014 order completed."));
            await store.refresh();
            render(store);
            if (window.MPage && window.MPage.render) { /* board refresh handled by its own page */ }
          }
        } catch (e) {}
      });

      row.querySelector("[data-a='reject']").addEventListener("click", function () {
        row.querySelector(".sc-approval__reject").hidden = false;
      });
      row.querySelector("[data-a='send']").addEventListener("click", async function () {
        var remarks = row.querySelector("[data-a='remarks']").value.trim();
        if (!remarks) { row.querySelector("[data-a='remarks']").focus(); return; }
        try {
          var r = await fetch("/api/stage/reject", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: s.id, by: by, remarks: remarks })
          }).then(function (x) { return x.json(); });
          if (r.ok) { renderPendingApprovals(store); }
        } catch (e) {}
      });
      host.appendChild(row);
    });
    renderPastApprovals(host, store);
  }

  // Collapsible dropdown of PAST (approved / rejected) stage decisions —
  // separate from the pending list so the head sees only what needs action up
  // top, with history tucked away.
  async function renderPastApprovals(host, store) {
    var data;
    try {
      var appr = await fetch("/api/stage/submissions?status=approved").then(function (r) { return r.json(); });
      var rej = await fetch("/api/stage/submissions?status=rejected").then(function (r) { return r.json(); });
      data = ((appr && appr.submissions) || []).concat((rej && rej.submissions) || []);
    } catch (e) { return; }
    if (!data.length) return;
    // sort most-recent first
    data.sort(function (a, b) {
      return String(b.reviewedAt || b.submittedAt || "").localeCompare(String(a.reviewedAt || a.submittedAt || ""));
    });

    // group by DATE (day) — a dropdown per day of confirmed/decided actions
    function dayKey(s) {
      var iso = s.reviewedAt || s.submittedAt || "";
      return iso ? iso.slice(0, 10) : "—";
    }
    function dayLabel(key) {
      if (key === "—") return "Undated";
      try {
        var d = new Date(key + "T00:00:00");
        return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
      } catch (e) { return key; }
    }
    var days = [];
    var byDay = {};
    data.forEach(function (s) {
      var k = dayKey(s);
      if (!byDay[k]) { byDay[k] = []; days.push(k); }
      byDay[k].push(s);
    });

    var wrap = document.createElement("div");
    wrap.className = "sc-past-wrap";
    wrap.innerHTML =
      '<div class="sc-past-head">Past approvals · by date</div>' +
      days.map(function (k, di) {
        var rows = byDay[k].map(function (s) {
          var ok = s.status === "approved";
          var t = (s.reviewedAt || s.submittedAt || "");
          var time = t ? t.slice(11, 16) : "";
          return '<div class="sc-past__row">' +
            '<span class="sc-past__badge ' + (ok ? "is-ok" : "is-rej") + '">' +
              (ok ? "\u2713 Approved" : "\u2715 Rejected") + "</span>" +
            '<span class="sc-past__order">' + s.order + " \u00b7 " + s.stage + "</span>" +
            '<span class="sc-past__meta">' + (time ? time + " \u00b7 " : "") +
            (s.reviewedBy || s.submittedBy || "") +
            (s.remarks ? ' \u00b7 "' + s.remarks + '"' : "") + "</span></div>";
        }).join("");
        return '<details class="sc-past"' + (di === 0 ? " open" : "") + ">" +
          '<summary class="sc-past__sum"><span class="sc-past__chev">\u25be</span>' +
          dayLabel(k) + '<span class="sc-past__count">' + byDay[k].length + "</span></summary>" +
          '<div class="sc-past__list">' + rows + "</div></details>";
      }).join("");
    host.appendChild(wrap);
  }
})(window, document);
