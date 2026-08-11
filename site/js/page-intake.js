/* Order Intake — manual entry (all details incl. starting stage) and
   Knowledge-Centre document sync (parse -> review -> import). Both hit the
   real backend so new orders reach the scheduler. */
(function (window, document) {
  "use strict";
  var MU = window.MU;

  var STAGES = ["Order Entry", "Kitting", "Sensor Module", "Helium Leak",
    "Electronics", "Assembly", "Calibration", "Burn-in", "Hydro",
    "Certification", "Final QC", "Documentation", "Packing", "Dispatch"];
  var LINE_PRODUCT = { PT: "PT-3051", TT: "TT-644", DP: "DP-2051", LT: "LT-5400" };

  function actor() {
    var u = (window.MStore && window.MStore.user) || {};
    return { by: u.name || "Department Head", role: u.role || "Department Head" };
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function q(sel) { return document.querySelector(sel); }

  function render(store) {
    // datalists for customer/product convenience
    var data = (store && store.data) || {};
    fillList("intake-customers", data.customers || []);
    fillList("intake-products", data.products ||
      ["PT-3051", "TT-644", "DP-2051", "LT-5400"]);

    // starting-stage dropdown
    var stageSel = q("[data-input='intake-stage']");
    if (stageSel && !stageSel.children.length) {
      stageSel.innerHTML = STAGES.map(function (s) {
        return '<option value="' + s + '">' + s + "</option>";
      }).join("");
    }

    wireManual();
    wireKc();
    wireKcLive();
    paintQueue();
  }

  // ---- manual entry ----------------------------------------------------
  function wireManual() {
    var form = q("[data-action='create-order']");
    if (!form || form._wired) return;
    form._wired = true;
    var msg = q("[data-slot='intake-msg']");

    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      var line = q("[data-input='intake-line']").value;
      var qty = q("[data-input='intake-qty']").value;
      var due = q("[data-input='intake-due']").value;
      if (!qty || !due) { q("[data-input='intake-qty']").focus(); return; }
      var a = actor();
      var payload = {
        lineCode: line,
        product: q("[data-input='intake-product']").value.trim() || LINE_PRODUCT[line],
        customer: q("[data-input='intake-customer']").value.trim(),
        qty: Number(qty),
        due: fmtDate(due),
        stage: q("[data-input='intake-stage']").value,
        rush: q("[data-input='intake-rush']").checked,
        notes: q("[data-input='intake-notes']").value.trim(),
        by: a.by, role: a.role
      };
      msg.textContent = "Adding…"; msg.className = "intake-msg";
      try {
        var res = await fetch("/api/intake", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        var out = await res.json();
        if (!res.ok) { msg.textContent = out.error || "Could not add order"; msg.className = "intake-msg is-err"; return; }
        msg.textContent = "\u2713 " + out.order.code + " created (" + payload.product +
          " \u00d7" + payload.qty + " on " + line + ", at " + payload.stage + ").";
        msg.className = "intake-msg is-ok";
        form.reset();
        if (window.MStore && window.MStore.refresh) await window.MStore.refresh();
        paintQueue();
      } catch (e) { msg.textContent = "Could not add order"; msg.className = "intake-msg is-err"; }
    });
  }

  // ---- Knowledge-Centre document sync ----------------------------------
  function wireKc() {
    var browse = q("[data-action='kc-browse']");
    var fileInput = q("[data-input='kc-file']");
    var drop = q("[data-slot='kc-drop']");
    var textArea = q("[data-input='kc-text']");
    var parseBtn = q("[data-action='kc-parse']");
    var helpBtn = q("[data-action='kc-help']");
    var kcMsg = q("[data-slot='kc-msg']");
    if (!parseBtn || parseBtn._wired) return;
    parseBtn._wired = true;

    if (browse) browse.addEventListener("click", function () { fileInput.click(); });
    if (fileInput) fileInput.addEventListener("change", function () {
      if (fileInput.files && fileInput.files[0]) readFile(fileInput.files[0], textArea, kcMsg);
    });
    if (drop) {
      ["dragover", "dragenter"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("is-over"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("is-over"); });
      });
      drop.addEventListener("drop", function (e) {
        var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) readFile(f, textArea, kcMsg);
      });
    }
    if (helpBtn) helpBtn.addEventListener("click", function () {
      var h = q("[data-slot='kc-help']");
      h.hidden = !h.hidden;
      if (!h.innerHTML) h.innerHTML = helpHtml();
    });

    parseBtn.addEventListener("click", async function () {
      var text = (textArea.value || "").trim();
      if (!text) { kcMsg.textContent = "Upload or paste a document first."; kcMsg.className = "intake-msg is-err"; return; }
      kcMsg.textContent = "Extracting…"; kcMsg.className = "intake-msg";
      try {
        var res = await fetch("/api/intake/kc-parse", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text })
        });
        var out = await res.json();
        if (out.error) { kcMsg.textContent = out.error; kcMsg.className = "intake-msg is-err"; renderPreview([]); return; }
        kcMsg.textContent = "Read " + out.count + " order" + (out.count > 1 ? "s" : "") +
          " (" + (out.format === "json" ? "JSON" : "text") + " format). Review and import.";
        kcMsg.className = "intake-msg is-ok";
        renderPreview(out.orders || []);
      } catch (e) { kcMsg.textContent = "Could not parse the document."; kcMsg.className = "intake-msg is-err"; }
    });
  }

  function renderPreview(items) {
    var host = q("[data-slot='kc-preview']");
    if (!host) return;
    if (!items.length) { host.innerHTML = ""; return; }
    var rows = items.map(function (it, i) {
      var o = it.order;
      var warn = (it.warnings && it.warnings.length)
        ? '<div class="kc-row__warn">\u26a0 ' + it.warnings.map(esc).join("; ") + "</div>" : "";
      return '<div class="kc-row" data-i="' + i + '">' +
        '<label class="chk"><input type="checkbox" checked data-k="pick"></label>' +
        '<div class="kc-row__grid">' +
          field("Customer", o.customer || "\u2014") +
          field("Product", o.product || "(line default)") +
          field("Line", o.line) +
          field("Qty", o.qty) +
          field("Due", o.due || "\u2014") +
          field("Stage", o.stage) +
          field("Rush", o.rush ? "yes" : "no") +
          (o.notes ? field("Notes", o.notes) : "") +
        "</div>" + warn +
        "</div>";
    }).join("");
    host.innerHTML =
      '<div class="kc-preview__head">Extracted orders</div>' + rows +
      '<div class="kc-preview__foot">' +
        '<button class="btn btn--primary" data-action="kc-import">Import selected</button>' +
        '<span class="intake-msg" data-slot="kc-import-msg"></span></div>';
    host._items = items;

    host.querySelector("[data-action='kc-import']").addEventListener("click", function () {
      importSelected(host);
    });
  }

  async function importSelected(host) {
    var picks = [];
    host.querySelectorAll(".kc-row").forEach(function (row) {
      if (row.querySelector("[data-k='pick']").checked) {
        picks.push(host._items[parseInt(row.dataset.i, 10)].order);
      }
    });
    var imsg = host.querySelector("[data-slot='kc-import-msg']");
    if (!picks.length) { imsg.textContent = "Tick at least one order."; imsg.className = "intake-msg is-err"; return; }
    var a = actor();
    imsg.textContent = "Importing…"; imsg.className = "intake-msg";
    try {
      var res = await fetch("/api/intake/kc-import", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orders: picks, by: a.by, role: a.role })
      });
      var out = await res.json();
      if (!res.ok) { imsg.textContent = out.error || "Import failed"; imsg.className = "intake-msg is-err"; return; }
      imsg.textContent = "\u2713 Imported " + out.count + " order" + (out.count > 1 ? "s" : "") +
        ": " + out.created.map(function (o) { return o.code; }).join(", ");
      imsg.className = "intake-msg is-ok";
      q("[data-input='kc-text']").value = "";
      if (window.MStore && window.MStore.refresh) await window.MStore.refresh();
      paintQueue();
    } catch (e) { imsg.textContent = "Import failed"; imsg.className = "intake-msg is-err"; }
  }

  function field(label, val) {
    return '<div class="kc-f"><span>' + label + "</span><b>" + esc(val) + "</b></div>";
  }

  function helpHtml() {
    return '<p class="sm">The Knowledge Centre should emit one of these. <b>JSON</b> is preferred for reliable extraction:</p>' +
      '<pre class="kc-code">{\n  "orders": [\n    {\n      "customer": "Reliance Refineries",\n      "product": "PT-3051",\n      "line": "PT",\n      "qty": 40,\n      "due": "22 Aug",\n      "stage": "Order Entry",\n      "rush": false,\n      "notes": "NACE certification required"\n    }\n  ]\n}</pre>' +
      '<p class="sm">Or <b>labelled text</b> (one order per blank-line-separated block):</p>' +
      '<pre class="kc-code">Customer: Reliance Refineries\nProduct: PT-3051\nLine: PT\nQuantity: 40\nDue: 22 Aug\nStage: Order Entry\nRush: no\nNotes: NACE certification required</pre>' +
      '<p class="sm muted">Recognised fields: customer, product, family, line (PT/TT/DP/LT or Line 1\u20134), quantity, due, stage, rush, notes. Line can be inferred from the product; missing fields are flagged in the preview for you to fix before import.</p>';
  }

  // ---- intake queue ----------------------------------------------------
  async function paintQueue() {
    var host = q("[data-slot='intake-queue']");
    if (!host) return;
    var items = [];
    try {
      var b = await fetch("/api/bootstrap").then(function (r) { return r.json(); });
      items = (b.intakeQueue || []);
    } catch (e) {}
    var count = q("[data-slot='queue-count']");
    if (count) count.textContent = items.length + " in intake";
    if (!items.length) { host.innerHTML = '<p class="muted sm" style="padding:14px 18px">Nothing waiting in intake.</p>'; return; }
    host.innerHTML = '<div class="intake-q">' + items.map(function (o) {
      return '<div class="intake-q__row">' +
        '<b>' + esc(o.code) + "</b>" +
        "<span>" + esc(o.product) + " \u00d7" + (o.qty || "") + "</span>" +
        "<span>" + esc(o.line || o.lineCode || "") + "</span>" +
        '<span class="badge">' + esc(o.stage || "Order Entry") + "</span>" +
        "</div>";
    }).join("") + "</div>";
  }

  function fillList(id, values) {
    var dl = document.getElementById(id);
    if (!dl) return;
    dl.innerHTML = values.map(function (v) { return '<option value="' + esc(v) + '">'; }).join("");
  }
  function fmtDate(iso) {
    // yyyy-mm-dd -> "22 Aug" to match the app's display format
    try {
      var d = new Date(iso + "T00:00:00");
      return d.getDate() + " " + ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getMonth()];
    } catch (e) { return iso; }
  }
  function readFile(file, textArea, msg) {
    var r = new FileReader();
    r.onload = function () { textArea.value = r.result; if (msg) { msg.textContent = "Loaded " + file.name + " — click Extract."; msg.className = "intake-msg is-ok"; } };
    r.onerror = function () { if (msg) { msg.textContent = "Could not read file."; msg.className = "intake-msg is-err"; } };
    r.readAsText(file);
  }

  // ---- Knowledge-Centre LIVE auto-detect -------------------------------
  var _kcAutoTimer = null;
  function wireKcLive() {
    var btn = q("[data-action='kc-detect']");
    if (!btn || btn._wired) return;
    btn._wired = true;
    var statusEl = q("[data-slot='kc-live-status']");
    var auto = q("[data-input='kc-auto']");

    function actor() {
      var u = (window.MStore && window.MStore.user) || {};
      return { by: u.name || "Department Head", role: u.role || "Department Head" };
    }

    async function detect() {
      statusEl.textContent = "Checking Knowledge Centre…";
      try {
        var d = await fetch("/api/intake/kc-detect").then(function (r) { return r.json(); });
        if (!d.online) {
          statusEl.innerHTML = '<span class="kc-off">Knowledge Centre offline</span> — start it, or use paste-sync below.';
          renderQueue([]);
          return;
        }
        statusEl.innerHTML = 'Connected · <b>' + d.seen + '</b> order document(s) in the Knowledge Centre · <b>' +
          d.new.length + '</b> new';
        renderQueue(d.new || []);
      } catch (e) {
        statusEl.innerHTML = '<span class="kc-off">Could not reach the scheduler.</span>';
      }
    }

    function renderQueue(orders) {
      var host = q("[data-slot='kc-live-queue']");
      if (!orders.length) { host.innerHTML = '<p class="muted sm" style="padding:10px 2px">No new orders waiting.</p>'; return; }
      host.innerHTML =
        '<div class="kc-live__rows">' +
        orders.map(function (o, i) {
          var warn = (o.warnings && o.warnings.length)
            ? '<span class="kc-warn" title="' + o.warnings.join("; ") + '">\u26a0 ' + o.warnings.length + '</span>' : "";
          return '<label class="kc-live__row">' +
            '<input type="checkbox" data-kc-pick="' + i + '"' + (o.lineCode ? " checked" : "") + '>' +
            '<span class="kc-live__code">' + (o.code || o.docId) + "</span>" +
            '<span class="kc-live__meta">' + (o.product || "?") + " \u00b7 " +
              (o.line || '<span class="kc-warn">needs line</span>') + " \u00b7 " + (o.qty || "?") + " units \u00b7 due " +
              (o.due || "?") + (o.rush ? ' \u00b7 <b class="kc-rush">RUSH</b>' : "") + "</span>" +
            (o.customer ? '<span class="kc-live__cust">' + o.customer + "</span>" : "") +
            warn + "</label>";
        }).join("") +
        "</div>" +
        '<div class="kc-live__actions">' +
        '<button class="btn btn--primary btn--sm" data-kc-import>Import selected</button>' +
        '<span class="intake-msg" data-slot="kc-live-msg"></span></div>';

      host.querySelector("[data-kc-import]").addEventListener("click", async function () {
        var picks = Array.prototype.slice.call(host.querySelectorAll("[data-kc-pick]:checked"))
          .map(function (cb) { return orders[parseInt(cb.getAttribute("data-kc-pick"), 10)]; });
        var msg = host.querySelector("[data-slot='kc-live-msg']");
        if (!picks.length) { msg.textContent = "Select at least one order."; msg.className = "intake-msg is-err"; return; }
        // block import of orders still missing a line
        var noLine = picks.filter(function (p) { return !p.lineCode; });
        if (noLine.length) {
          msg.textContent = noLine.length + " order(s) still need a line — assign it in the KC doc first.";
          msg.className = "intake-msg is-err"; return;
        }
        msg.textContent = "Importing…"; msg.className = "intake-msg";
        var a = actor();
        try {
          var r = await fetch("/api/intake/kc-sync-import", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ orders: picks, by: a.by, role: a.role })
          }).then(function (x) { return x.json(); });
          if (!r.ok) { msg.textContent = r.error || "Import failed."; msg.className = "intake-msg is-err"; return; }
          msg.textContent = "Imported " + r.count + " order(s)." +
            (r.skipped && r.skipped.length ? " Skipped " + r.skipped.length + " (already present)." : "");
          msg.className = "intake-msg is-ok";
          if (window.MStore && window.MStore.refresh) window.MStore.refresh();
          setTimeout(detect, 700);   // refresh the queue
        } catch (e) { msg.textContent = "Import failed."; msg.className = "intake-msg is-err"; }
      });
    }

    btn.addEventListener("click", detect);
    if (auto) auto.addEventListener("change", function () {
      if (auto.checked) { detect(); _kcAutoTimer = setInterval(detect, 30000); }
      else if (_kcAutoTimer) { clearInterval(_kcAutoTimer); _kcAutoTimer = null; }
    });
    detect();   // initial check on page load
  }

  window.MPage = { render: render };
})(window, document);
