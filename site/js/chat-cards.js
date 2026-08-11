/* Visual answer cards for the assistant chat.
   The ENGINE fills the data (facts); the LLM/rules write the prose above.
   Two card kinds today: 'order' (routing stepper) and 'replan' (before/after). */
(function (window, document) {
  "use strict";

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function orderCard(d) {
    var card = el("div", "cc-card");
    // header chip
    card.appendChild(el("div", "cc-chip",
      '\u25C6 Order lookup \u00b7 ' + d.code));
    // title line
    var risk = d.status && /risk/i.test(d.status);
    card.appendChild(el("div", "cc-title",
      d.code + ' <span class="cc-mut">is at</span> ' +
      (d.current_stage || "\u2014") + "."));
    // stepper
    var box = el("div", "cc-stepper");
    var head = el("div", "cc-stepper__head");
    head.innerHTML = '<span class="cc-code">' + d.code + "</span>" +
      '<span class="cc-dot2">\u00b7</span><span>' + (d.product || "") + "</span>" +
      '<span class="cc-dot2">\u00b7</span><span>' + (d.family || d.line || "") + "</span>" +
      (risk ? '<span class="cc-risk">\u25C6 At risk</span>' : "");
    box.appendChild(head);
    var track = el("div", "cc-track");
    (d.steps || []).forEach(function (s) {
      var node = el("div", "cc-step is-" + s.state);
      var mark = s.state === "done" ? "\u2713" : (s.state === "current" ? "!" : "");
      node.innerHTML = '<div class="cc-step__dot">' + mark + "</div>" +
        '<div class="cc-step__name">' + s.stage + "</div>";
      track.appendChild(node);
    });
    box.appendChild(track);
    card.appendChild(box);
    // held / at-risk note
    if (d.held) {
      var h = d.held;
      var note = el("div", "cc-note" + (h.halted ? " is-halt" : " is-risk"));
      note.innerHTML =
        (h.halted ? "Halted" : "Held") + " at " + h.stage +
        (h.resource ? " (" + h.resource + ")" : "") + ". " +
        (h.projected ? 'Delivery ' + (h.atRisk || h.halted ? "at risk \u2014 " : "") +
          '<b>' + h.projected + "</b>" + (h.due ? ' <span class="cc-mut">(was ' + h.due + ")</span>" : "") : "");
      card.appendChild(note);
    }
    // open detail link
    var link = el("a", "cc-link", "Open order detail \u2192");
    link.href = "#";
    link.addEventListener("click", function (e) {
      e.preventDefault();
      if (window.openOrderDetail) window.openOrderDetail(d.code);
    });
    card.appendChild(link);
    return card;
  }

  function replanCard(d) {
    var card = el("div", "cc-card");
    card.appendChild(el("div", "cc-chip",
      '\u25C6 ' + (d.echo || "Re-plan") +
      (d.proposed ? ' <span class="cc-proposed">proposed</span>' : "")));
    if (d.summary) card.appendChild(el("div", "cc-summary", d.summary));
    // badges
    if (d.badges && d.badges.length) {
      var bwrap = el("div", "cc-badges");
      d.badges.forEach(function (b) {
        bwrap.appendChild(el("span", "cc-badge cc-badge--" + (b.tone || "neutral"), b.label));
      });
      card.appendChild(bwrap);
    }
    // before/after table
    var changes = d.changes || [];
    if (changes.length) {
      var t = el("table", "cc-table");
      t.innerHTML = "<thead><tr><th>Order</th><th>Before</th><th></th><th>After</th></tr></thead>";
      var tb = document.createElement("tbody");
      changes.slice(0, 8).forEach(function (c) {
        var unchanged = (c.to_value === c.from_value) ||
          /unchanged/i.test(c.to_value || "");
        var tr = document.createElement("tr");
        tr.innerHTML =
          "<td class='cc-order'>" + (c.order || c.what || "") + "</td>" +
          "<td class='cc-before'>" + (c.from_value || "\u2014") + "</td>" +
          "<td class='cc-arrow'>" + (unchanged ? "" : "\u2192") + "</td>" +
          "<td class='cc-after " + (unchanged ? "is-same" : "is-changed") + "'>" +
            (c.to_value || "\u2014") + "</td>";
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      card.appendChild(t);
    }
    if (d.proposed) {
      card.appendChild(el("div", "cc-preview-note",
        "\u26A0 Preview only \u2014 the live board updates when you approve."));
    }
    return card;
  }

  function citationsBlock(cites) {
    if (!cites || !cites.length) return null;
    var wrap = el("div", "cc-cites");
    wrap.appendChild(el("div", "cc-cites__head", "Sources"));
    var list = el("ol", "cc-cites__list");
    cites.forEach(function (c, i) {
      var label = typeof c === "string" ? c : (c.label || c.source || "");
      var detail = (c && c.detail)
        ? '<span class="cc-cites__det">' + c.detail + "</span>" : "";
      list.appendChild(el("li", "cc-cite",
        '<span class="cc-cite__n">' + (i + 1) + "</span>" +
        '<span class="cc-cite__body"><span class="cc-cite__label">' + label + "</span>" +
        detail + "</span>"));
    });
    wrap.appendChild(list);
    return wrap;
  }

  function chartCard(chart) {
    if (!chart || !chart.series || !chart.series.length) return null;
    var max = Math.max.apply(null, chart.series.map(function (s) { return s.value || 0; })) || 1;
    var wrap = el("div", "cc-card cc-chart");
    if (chart.title) wrap.appendChild(el("div", "cc-chart__title", chart.title));
    var rows = el("div", "cc-chart__rows");
    chart.series.forEach(function (s) {
      var pct = Math.round((s.value || 0) / max * 100);
      var row = el("div", "cc-chart__row");
      row.innerHTML =
        '<span class="cc-chart__lbl">' + s.label + "</span>" +
        '<span class="cc-chart__track"><span class="cc-chart__fill" style="width:' + pct + '%"></span></span>' +
        '<span class="cc-chart__val">' + (s.value != null ? s.value : "") + (s.unit || "") + "</span>";
      rows.appendChild(row);
    });
    wrap.appendChild(rows);
    return wrap;
  }

  window.MChatCards = {
    render: function (kind, data) {
      try {
        if (kind === "order" && data && data.steps) return orderCard(data);
        if (kind === "replan" && data) return replanCard(data);
        if (data && data.chart) return chartCard(data.chart);
      } catch (e) { return null; }
      return null;
    },
    citations: function (cites) {
      try { return citationsBlock(cites); } catch (e) { return null; }
    }
  };
})(window, document);
