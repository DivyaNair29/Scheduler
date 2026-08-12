/* Dashboard — phase kanban + needs-attention approvals (head),
   or today's job + constraint raising (employee). */
(function (window, document) {
  "use strict";
  const MU = window.MU, MC = window.MC;

  function headView(store) {
    document.querySelector("[data-slot='view']").classList.remove("view--narrow");
    document.querySelector("[data-region='head-view']").hidden = false;
    document.querySelector("[data-region='employee-view']").hidden = true;

    const colorOf = {};
    store.data.lines.forEach(function (l) { colorOf[l.code] = l.color; });

    var KAN_CAP = 4;   // show this many per column, rest behind a toggle
    MU.render("[data-slot='kanban']", store.data.phases.map(function (phase) {
      const col = MU.fromTemplate("tpl-kanban-col");
      col.dataset.phase = phase.key;
      const orders = store.ordersByPhase(phase.key);
      col.querySelector("[data-slot='kanban-head']").style.background = phase.color;
      MU.fill(col, { name: phase.label, count: String(orders.length) });
      var body = col.querySelector("[data-slot='kanban-body']");
      function cardFor(o) {
        var card = MC.orderCard(o, colorOf[o.lineCode]);
        card.classList.add("is-clickable");
        card.addEventListener("click", function () {
          if (window.openOrderDetail) window.openOrderDetail(o.code);
        });
        return card;
      }
      if (!orders.length) {
        MU.render(body, [MU.empty("Nothing in this phase")]);
      } else {
        var shown = orders.slice(0, KAN_CAP).map(cardFor);
        MU.render(body, shown);
        if (orders.length > KAN_CAP) {
          var hidden = orders.slice(KAN_CAP);
          var expanded = false;
          var toggle = document.createElement("button");
          toggle.className = "kanban-more";
          toggle.textContent = "+ Show all (" + orders.length + ")";
          var extraWrap = document.createElement("div");
          extraWrap.className = "kanban-extra";
          extraWrap.style.display = "none";
          hidden.forEach(function (o) { extraWrap.appendChild(cardFor(o)); });
          toggle.addEventListener("click", function () {
            expanded = !expanded;
            extraWrap.style.display = expanded ? "" : "none";
            toggle.textContent = expanded ? "\u2212 Show less" : "+ Show all (" + orders.length + ")";
          });
          body.appendChild(extraWrap);
          body.appendChild(toggle);
        }
      }
      return col;
    }));

    // Alerts must show ONLY items that still need action — a pending constraint
    // awaiting the head's approval. Anything already acted upon (applied to the
    // floor, or rejected) is done and must not appear here.
    const approvals = store.constraints.filter(function (c) {
      return c.status === "pending";
    });
    var approvalCards = approvals.length
      ? approvals.map(function (c) {
          return MC.approvalCard(c, store, function () { render(store); });
        })
      : [];
    MU.render("[data-slot='approvals']",
      approvalCards.length ? approvalCards : [MU.empty("Nothing needs your approval.")]);

    // Manpower attention — recent absence / reassignment activity the head
    // should see alongside approvals.
    renderManpowerAttention(store, approvalCards.length);

    // alert count badge (only approvals awaiting action)
    var alertCountEl = document.querySelector("[data-slot='alert-count']");
    if (alertCountEl) alertCountEl.textContent = approvalCards.length ? approvalCards.length : "";

    // Optimization Suggested — three collapsible groups (floor/manpower/business)
    renderOptimizations();

    // Dispatches today
    var dispatches = (store.data && store.data.dispatches) || [];
    MU.render("[data-slot='dispatches']", dispatches.length
      ? dispatches.map(function (d) {
          const row = MU.fromTemplate("tpl-dispatch-row");
          MU.fill(row, { time: d.time, item: d.item, customer: d.customer, status: d.status });
          row.querySelector("[data-field='status']").className = "badge badge--" + MU.slug(d.status);
          return row;
        })
      : [MU.empty("No dispatches scheduled today.")]);
  }

  function renderManpowerAttention(store, hasApprovals) {
    var host = document.querySelector("[data-slot='approvals']");
    if (!host || !store.canWrite) return;
    fetch("/api/manpower/attention").then(function (r) { return r.json(); })
      .then(function (items) {
        if (!items || !items.length) return;
        // if there were no approvals, clear the "nothing needs approval" empty state
        if (!hasApprovals) host.innerHTML = "";
        var wrap = document.createElement("div");
        wrap.className = "mp-attention";
        wrap.innerHTML = '<div class="mp-attention__head">MANPOWER \u00b7 recent</div>' +
          items.map(function (it) {
            var icon = it.kind === "assignment" ? "\uD83D\uDCCB" :
              (/absent|out|unavailable/i.test(it.title) ? "\uD83D\uDEAB" : "\u21BA");
            return '<div class="mp-att-item"><span class="mp-att-ico">' + icon + "</span>" +
              '<div class="mp-att-body"><div class="mp-att-title">' + (it.title || "") + "</div>" +
              '<div class="mp-att-detail">' + (it.detail || "") + "</div>" +
              '<div class="mp-att-meta">' + (it.by || "") + " \u00b7 " + (it.ago || "") + "</div></div></div>";
          }).join("");
        host.appendChild(wrap);
      }).catch(function () {});
  }

  function renderOptimizations() {
    var host = document.querySelector("[data-slot='optimizations']");
    if (!host) return;
    var canWrite = false;
    try { canWrite = !!(window.MStore && window.MStore.canWrite); } catch (e) {}
    host.innerHTML = '<p class="muted sm" style="padding:8px 2px">Loading suggestions…</p>';
    fetch("/api/optimizations").then(function (r) { return r.json(); })
      .then(function (d) {
        // labels: floor & manpower use apply/reject; the third is advisory
        var groups = [
          { key: "floor", label: "Floor Optimization", items: d.floor || [], act: "apply" },
          { key: "manpower", label: "Manpower Optimization", items: d.manpower || [], act: "assign" },
          { key: "business", label: "Business Optimization", items: d.business || [], act: null },
        ];
        host.innerHTML = groups.map(function (g, gi) {
          var body = g.items.length
            ? g.items.map(function (it) {
                var actions = "";
                if (canWrite && g.act) {
                  var applyLabel = g.act === "assign" ? "Assign" : "Apply";
                  actions =
                    '<div class="opt-item__actions" data-id="' + (it.id || "") +
                      '" data-title="' + (it.title || "").replace(/"/g, "&quot;") + '">' +
                      '<button class="opt-btn opt-btn--apply" data-act="apply">\u2713 ' + applyLabel + "</button>" +
                      '<button class="opt-btn opt-btn--reject" data-act="reject">Reject</button>' +
                      '<input class="opt-remark" placeholder="remark (required to reject)">' +
                      '<span class="opt-msg"></span>' +
                    "</div>";
                }
                return '<div class="opt-item">' +
                  '<div class="opt-item__title">' + (it.title || "") + "</div>" +
                  (it.detail ? '<div class="opt-item__detail">' + it.detail + "</div>" : "") +
                  (it.gain ? '<div class="opt-item__gain">' + it.gain + "</div>" : "") +
                  actions +
                  "</div>";
              }).join("")
            : '<p class="muted sm" style="padding:8px 12px">No suggestions right now.</p>';
          return '<details class="opt-group"' + (gi === 0 ? " open" : "") + ">" +
            '<summary class="opt-group__sum"><span class="opt-group__chev">\u25be</span>' +
              g.label + '<span class="opt-group__count">' + g.items.length + "</span></summary>" +
            '<div class="opt-group__body">' + body + "</div></details>";
        }).join("");
        wireOptDecisions(host);
      })
      .catch(function () {
        host.innerHTML = '<p class="muted sm" style="padding:8px 2px">Could not load suggestions.</p>';
      });
  }

  function wireOptDecisions(host) {
    host.querySelectorAll(".opt-item__actions").forEach(function (box) {
      var id = box.getAttribute("data-id");
      var title = box.getAttribute("data-title");
      var remark = box.querySelector(".opt-remark");
      var msg = box.querySelector(".opt-msg");
      box.querySelectorAll(".opt-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var decision = btn.getAttribute("data-act") === "apply" ? "applied" : "rejected";
          var remarks = (remark.value || "").trim();
          if (decision === "rejected" && !remarks) {
            msg.textContent = "Add a remark to reject.";
            remark.focus(); return;
          }
          box.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
          msg.textContent = decision === "applied" ? "Preparing plan…" : "Recording…";
          var by = "", role = "";
          try { by = window.MStore.user.name; role = window.MStore.user.role; } catch (e) {}
          fetch("/api/floor-insights/decide", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: id, title: title, decision: decision,
                                   remarks: remarks, by: by, role: role }),
          }).then(function (r) { return r.json(); }).then(function (res) {
            if (res && res.ok) {
              var item = box.closest(".opt-item");
              if (item) {
                item.classList.add("opt-item--decided");
                item.innerHTML = '<div class="opt-item__title">' + title + "</div>" +
                  '<div class="opt-item__done ' + (decision === "applied" ? "is-ok" : "is-rej") + '">' +
                  (decision === "applied" ? "\u2713 Applied to the plan" : "\u2715 Rejected") +
                  (remarks ? ' \u00b7 "' + remarks + '"' : "") + "</div>";
              }
            } else {
              msg.textContent = (res && res.error) || "Could not save.";
              box.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
            }
          }).catch(function () {
            msg.textContent = "Network error.";
            box.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
          });
        });
      });
    });
  }

  function renderRework(store) {
    var host = document.querySelector("[data-slot='rework-list']");
    if (!host) return;
    fetch("/api/rework").then(function (r) { return r.json(); })
      .then(function (list) {
        list = list || [];
        var section = document.querySelector("[data-slot='rework-section']");
        if (!list.length) {
          if (section) section.hidden = true;
          host.innerHTML = "";
          return;
        }
        if (section) section.hidden = false;
        host.innerHTML = list.map(function (o) {
          return '<div class="rw-item">' +
            '<div class="rw-item__head">' +
              '<span class="rw-item__code">' + o.code + "</span>" +
              '<span class="rw-item__prod">' + (o.product || "") + " \u00b7 " + (o.line || "") + "</span>" +
              '<span class="rw-item__stage">Rework at ' + (o.reworkStage || o.currentStage || "?") + "</span>" +
            "</div>" +
            '<div class="rw-item__reason">' + (o.reason || "") + "</div>" +
            "</div>";
        }).join("");
      })
      .catch(function () { host.innerHTML = ""; });
  }

  function renderEmployeeNotifications(store) {
    var host = document.querySelector("[data-slot='my-notifications']");
    if (!host) return;
    var uid = store.user && store.user.id;
    if (!uid) { host.innerHTML = '<p class="muted sm">No notifications.</p>'; return; }
    fetch("/api/notifications?userId=" + uid).then(function (r) { return r.json(); })
      .then(function (d) {
        var list = (d && d.notifications) || [];
        if (!list.length) { host.innerHTML = '<p class="muted sm">No notifications yet.</p>'; return; }
        host.innerHTML = list.slice(0, 12).map(function (n) {
          return '<div class="mynote' + (n.read ? "" : " is-unread") + '">' +
            '<div class="mynote__title">' + (n.read ? "" : '<span class="mynote__dot"></span>') +
              (n.title || "") + "</div>" +
            '<div class="mynote__detail">' + (n.detail || "") + "</div>" +
            '<div class="mynote__ts">' + (n.createdAt ? n.createdAt.replace("T", " ").slice(0, 16) : "") + "</div>" +
            "</div>";
        }).join("");
        var unread = list.filter(function (n) { return !n.read; }).map(function (n) { return n.id; });
        if (unread.length) {
          fetch("/api/notifications/read", { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ids: unread, userId: uid }) });
        }
      })
      .catch(function () { host.innerHTML = '<p class="muted sm">Could not load notifications.</p>'; });
  }

  function employeeView(store) {
    // employee dashboard uses the full width (was constrained to view--narrow)
    document.querySelector("[data-slot='view']").classList.remove("view--narrow");
    document.querySelector("[data-region='head-view']").hidden = true;
    document.querySelector("[data-region='employee-view']").hidden = false;
    renderEmployeeNotifications(store);
    renderRework(store);

    const job = store.job;
    const orderSelect = document.querySelector("[data-input='job-order']");
    const stageSelect = document.querySelector("[data-input='job-stage']");

    fillSelect(orderSelect, store.orders.map((o) => o.code + " · " + o.product),
               store.orders.map((o) => o.code), job.order);
    fillSelect(stageSelect, store.data.stages, store.data.stages, job.stage);

    const items = store.checklistFor(job.stage);
    const summary = document.querySelector("[data-slot='job-summary']");
    if (job.order && job.stage) {
      summary.hidden = false;
      summary.textContent = job.done.length + " of " + items.length +
        " checks complete on " + job.order + " at " + job.stage + ".";
    } else {
      summary.hidden = true;
    }

    document.querySelector("[data-action='set-job']").onclick = function () {
      store.setJob(orderSelect.value, stageSelect.value);
      render(store);
    };

    // constraint form
    const typeSelect = document.querySelector("[data-input='con-type']");
    const conOrder = document.querySelector("[data-input='con-order']");
    const conStage = document.querySelector("[data-input='con-stage']");
    const noteField = document.querySelector("[data-input='con-note']");
    fillSelect(typeSelect, store.data.constraintTypes, store.data.constraintTypes, "");
    fillSelect(conOrder, store.orders.map((o) => o.code), store.orders.map((o) => o.code), job.order, "Order…");
    fillSelect(conStage, store.data.stages, store.data.stages, job.stage, "Stage…");

    document.querySelector("[data-action='raise-constraint']").onclick = function () {
      const note = noteField.value.trim();
      if (!note) { noteField.focus(); return; }
      store.raiseConstraint({
        order: conOrder.value || job.order, stage: conStage.value || job.stage,
        type: typeSelect.value, note: note
      });
      noteField.value = "";
      render(store);
    };

    const mine = store.constraints.filter((c) => c.raisedBy === store.user.name);
    MU.render("[data-slot='my-constraints']",
      mine.length ? mine.map(function (c) {
        const state = store.data.constraintStates[c.status];
        const row = MU.fromTemplate("tpl-my-constraint");
        MU.fill(row, {
          state: state.mine,
          meta: c.code + " · " + c.order + " · " + c.stage + " · " + c.ts,
          body: c.type + " — " + c.note
        });
        row.querySelector("[data-field='state']").className = "tag tag--" + state.tone;
        return row;
      }) : [MU.empty("You have not raised any constraints.")]);
  }

  function fillSelect(select, labels, values, selected, placeholder) {
    const options = [];
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = placeholder || "—";
    options.push(blank);
    labels.forEach(function (label, i) {
      const option = document.createElement("option");
      option.value = values[i];
      option.textContent = label;
      if (values[i] === selected) option.selected = true;
      options.push(option);
    });
    select.replaceChildren.apply(select, options);
  }

  function render(store) {
    const head = document.querySelector("[data-slot='greeting']");
    head.textContent = MU.greeting() + ", " + store.user.name;
    document.querySelector("[data-slot='greeting-sub']").textContent =
      MU.longDate() + " · " + store.user.role +
      (store.canWrite ? " · read + write" : " · shop floor");

    if (store.canWrite) headView(store);
    else employeeView(store);
  }

  window.MPage = { render: render };
})(window, document);
