/* ============================================================================
   components.js — builders for markup shared by more than one page.
   Every builder clones a <template> from the page; none of them invent copy.
   Attaches to window.MC.
   ==========================================================================*/
(function (window, document) {
  "use strict";

  const MU = window.MU;

  function orderCard(order, lineColor) {
    const node = MU.fromTemplate("tpl-order-card");
    node.dataset.order = order.code;
    MU.fill(node, {
      code: order.code, status: order.status, product: order.product,
      line: order.line, qty: "qty " + order.qty, due: "due " + order.due
    });
    node.querySelector("[data-field='status']").className =
      "badge badge--" + MU.slug(order.status);
    node.querySelector("[data-slot='line-swatch']").style.background = lineColor;
    // priority / rush badge — so a prioritised order is visible on the dashboard
    if (order.locked || order.rush) {
      var pill = document.createElement("span");
      pill.className = "ocard__prio" + (order.locked ? " is-locked" : " is-rush");
      pill.textContent = order.locked ? "\uD83D\uDD12 Priority" : "\u26A1 Rush";
      if (order.locked && order.lockReason) pill.title = order.lockReason;
      var codeEl = node.querySelector("[data-field='code']");
      if (codeEl && codeEl.parentNode) codeEl.parentNode.appendChild(pill);
      else node.appendChild(pill);
      node.classList.add("is-priority");
    }
    return node;
  }

  function miniCard(item, tone) {
    const node = MU.fromTemplate("tpl-mini-card");
    MU.fill(node, {
      kind: item.kind || "", ref: item.ref || "",
      title: item.title, detail: item.detail, gain: item.gain
    });
    const tag = node.querySelector("[data-field='kind']");
    if (item.kind) tag.className = "tag tag--" + (tone || "info");
    else tag.remove();
    if (!item.ref) node.querySelector("[data-field='ref']").remove();
    if (tone === "info") node.querySelector("[data-field='gain']").classList.add("gain--info");
    return node;
  }

  /* The constraint → schedule approval chain. Used by Dashboard and
     Quality & Dispatch, so the rule lives in exactly one place. */
  function approvalCard(constraint, store, onChange) {
    const state = store.data.constraintStates[constraint.status];
    const node = MU.fromTemplate("tpl-approval");
    node.dataset.constraint = constraint.code;
    node.classList.add("card--" + state.tone);

    MU.fill(node, {
      state: state.head,
      meta: constraint.code + " · raised by " + constraint.raisedBy + " · " + constraint.ts,
      revision: "Revision " + constraint.revision,
      type: constraint.type, order: constraint.order, stage: constraint.stage,
      note: constraint.note
    });
    node.querySelector("[data-field='state']").className = "tag tag--" + state.tone;

    // --- constraint decision
    const decide = node.querySelector("[data-slot='decide']");
    if (constraint.status === "pending" && store.canWrite) {
      decide.querySelector("[data-action='approve-constraint']")
        .addEventListener("click", function () { store.approveConstraint(constraint.code); onChange(); });
      decide.querySelector("[data-action='reject-constraint']")
        .addEventListener("click", function () { store.rejectConstraint(constraint.code); onChange(); });
    } else {
      decide.remove();
    }

    // --- schedule revision
    const panel = node.querySelector("[data-slot='schedule']");
    if (constraint.status === "approved" || constraint.status === "applied") {
      const feedback = node.querySelector("[data-field='feedback']");
      if (constraint.feedback) feedback.textContent = "Revised per your note: “" + constraint.feedback + "”";
      else feedback.remove();

      MU.render(panel.querySelector("[data-slot='changes']"),
        store.scheduleChanges(constraint).map(function (change) {
          const row = MU.fromTemplate("tpl-change-row");
          return MU.fill(row, change);
        }));

      const actions = panel.querySelector("[data-slot='schedule-actions']");
      const done = panel.querySelector("[data-slot='schedule-done']");
      if (constraint.status === "approved" && store.canWrite) {
        done.remove();
        const input = actions.querySelector("input");
        actions.querySelector("[data-action='approve-schedule']")
          .addEventListener("click", function () { store.approveSchedule(constraint.code); onChange(); });
        actions.querySelector("[data-action='reject-schedule']")
          .addEventListener("click", function () {
            const text = input.value.trim();
            if (!text) {
              input.focus();
              input.placeholder = "Tell the planner what to change first";
              return;
            }
            store.reviseSchedule(constraint.code, text);
            onChange();
          });
      } else if (constraint.status === "applied") {
        actions.remove();
      } else {
        actions.remove();
        done.remove();
      }
    } else {
      panel.remove();
    }

    return node;
  }

  function ganttBlock(order, color, quality) {
    const node = MU.fromTemplate("tpl-gantt-block");
    node.style.left = (order.startMin / 1440 * 100).toFixed(2) + "%";
    node.style.width = "calc(" + (order.durationMin / 1440 * 100).toFixed(2) + "% - 3px)";
    if (color) node.style.background = color;
    if (quality) node.classList.add("gantt-block--quality");
    if (order.status === "AT RISK") node.classList.add("gantt-block--risk");
    node.title = order.code + " · " + order.stage + " · due " + order.due;
    return MU.fill(node, { code: order.code, stage: order.stage });
  }

  window.MC = { orderCard, miniCard, approvalCard, ganttBlock };
})(window, document);
