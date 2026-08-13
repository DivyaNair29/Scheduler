/* ============================================================================
   main.js — shared chrome, loaded by every page.

   Boots the store, paints the sidebar / topbar / chat panel from templates,
   applies role gating, then calls the page script registered as
   window.MPage.render (if any).
   ==========================================================================*/
(function (window, document) {
  "use strict";

  const MU = window.MU;
  const store = window.MStore;

  /** Pages only Dept Head / Admin may open. */
  const WRITE_ONLY = ["board"];

  // SVG icon set for the sidebar nav (stroke inherits currentColor).
  var _svg = function (paths) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" ' +
      'width="18" height="18">' + paths + "</svg>";
  };
  const NAV_ICONS = {
    grid: _svg('<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'),
    board: _svg('<line x1="6" y1="20" x2="6" y2="12"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="18" y1="20" x2="18" y2="9"/>'),
    list: _svg('<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>'),
    people: _svg('<circle cx="9" cy="8" r="3"/><path d="M15 11a3 3 0 1 0-1.8-5.4"/><path d="M3 20c0-3 2.7-5 6-5s6 2 6 5"/><path d="M17 15c2.2.4 4 2 4 5"/>'),
    inbox: _svg('<path d="M4 13l2.5-8h11L20 13"/><path d="M4 13v5h16v-5"/><path d="M4 13h4l1.5 2.5h5L16 13h4"/>'),
    check: _svg('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>'),
    log: _svg('<rect x="4" y="3" width="16" height="18" rx="2"/><line x1="8" y1="8" x2="16" y2="8"/><line x1="8" y1="12" x2="16" y2="12"/><line x1="8" y1="16" x2="13" y2="16"/>'),
    report: _svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
    insights: _svg('<path d="M4 18l5-5 3 3 7-8"/><path d="M17 8h4v4"/>'),
    dot: _svg('<circle cx="12" cy="12" r="3"/>'),
  };

  // ------------------------------------------------------------- sidebar
  function renderSidebar(page) {
    const brand = store.data.brand;
    const logo = document.querySelector("[data-slot='brand-logo']");
    if (logo) logo.setAttribute("src", brand.logo);
    const name = document.querySelector("[data-slot='brand-name']");
    if (name) name.textContent = "Meridian Instruments Pvt Ltd.";
    const tagline = document.querySelector("[data-slot='brand-tagline']");
    if (tagline) tagline.textContent = brand.tagline;

    const nodes = [];
    store.data.nav.forEach(function (group, groupIndex) {
      const items = group.items.filter((i) => !i.write || store.canWrite);
      if (!items.length) return;
      if (groupIndex > 0) {
        const divider = document.createElement("div");
        divider.className = "nav-divider";
        nodes.push(divider);
      }
      if (group.group) {
        const head = document.createElement("div");
        head.className = "nav-group";
        head.textContent = group.group;
        nodes.push(head);
      }
      items.forEach(function (item) {
        const link = MU.fromTemplate("tpl-nav-item");
        link.href = (!store.canWrite && item.employeeHref) ? item.employeeHref : item.href;
        link.dataset.nav = item.id;
        if (item.id === page) link.classList.add("is-active");
        const iconNode = link.querySelector("[data-field='icon']");
        if (iconNode) {
          iconNode.innerHTML = NAV_ICONS[item.icon] || NAV_ICONS.dot;
        }
        const label = !store.canWrite && item.employeeLabel ? item.employeeLabel : item.label;
        link.querySelector("[data-field='label']").textContent = label;
        const badgeNode = link.querySelector("[data-field='badge']");
        const badge = item.badgeKey ? store.badge(item.badgeKey) : null;
        if (badge) badgeNode.textContent = String(badge);
        else badgeNode.remove();
        nodes.push(link);
      });
    });
    MU.render("[data-slot='nav']", nodes);

    const avatar = document.querySelector("[data-slot='user-initials']");
    if (avatar) avatar.textContent = MU.initials(store.user.name);
    const userName = document.querySelector("[data-slot='user-name']");
    if (userName) userName.textContent = store.user.name;

    // Sidebar notifications indicator — polls the current user's unread count
    // and links to the dashboard where the full list lives.
    (function () {
      var brandFoot = document.querySelector(".sidebar__foot") ||
                      document.querySelector("[data-slot='sync']");
      var host = document.querySelector("[data-slot='sidebar-notif']");
      if (!host) {
        host = document.createElement("a");
        host.className = "sidebar-notif";
        host.setAttribute("data-slot", "sidebar-notif");
        host.href = "index.html";
        var nav = document.querySelector(".sidebar__nav") || document.querySelector(".sidebar");
        if (nav) nav.appendChild(host);
      }
      function refresh() {
        var uid = store.user && store.user.id;
        if (!uid) return;
        fetch("/api/notifications?userId=" + uid + "&unread=1")
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var n = (d && d.unread) || 0;
            host.innerHTML = '<span class="sidebar-notif__ico">\uD83D\uDD14</span>' +
              '<span class="sidebar-notif__lbl">Notifications</span>' +
              (n ? '<span class="sidebar-notif__count">' + n + "</span>" : "");
            host.classList.toggle("has-unread", n > 0);
          }).catch(function () {});
      }
      refresh();
      if (!window._notifSidebarTimer) window._notifSidebarTimer = setInterval(refresh, 15000);
    })();

    MU.render("[data-slot='roles']", ((store.data && store.data.users) || []).map(function (user) {
      const chip = MU.fromTemplate("tpl-role-chip");
      chip.textContent = user.short;
      chip.dataset.userId = String(user.id);
      if (user.id === store.user.id) chip.classList.add("is-on");
      chip.addEventListener("click", function () {
        store.setUser(user.id);
        const blocked = WRITE_ONLY.indexOf(page) > -1 && !store.canWrite;
        window.location.href = blocked ? "index.html" : window.location.pathname;
      });
      return chip;
    }));

    // ERP sync footer
    var syncHost = document.querySelector("[data-slot='sync']");
    if (syncHost && store.data.sync) {
      var s = store.data.sync;
      syncHost.innerHTML =
        '<span class="syncbar__dot"></span>' +
        '<span class="syncbar__txt">' + s.source + " \u00b7 " + s.state +
        ' \u00b7 <b>' + s.at + "</b></span>" +
        '<button class="syncbar__btn" type="button" data-action="sync-now">Sync now</button>';
      var btn = syncHost.querySelector("[data-action='sync-now']");
      if (btn) btn.addEventListener("click", async function () {
        btn.textContent = "Syncing…"; btn.disabled = true;
        if (store.refresh) await store.refresh();
        btn.textContent = "Sync now"; btn.disabled = false;
        if (window.MPage && window.MPage.render) window.MPage.render(store);
      });
    }

    // Collapse toggle
    var collapseBtn = document.querySelector("[data-action='collapse-sidebar']");
    if (collapseBtn) {
      var applyCollapsed = function (on) {
        document.body.classList.toggle("is-sidebar-collapsed", on);
        var lbl = collapseBtn.querySelector("span");
        collapseBtn.firstChild.textContent = on ? "\u00bb " : "\u00ab ";
        if (lbl) lbl.textContent = on ? "" : "Collapse sidebar";
      };
      applyCollapsed(store.state.sidebarCollapsed || false);
      collapseBtn.addEventListener("click", function () {
        var on = !document.body.classList.contains("is-sidebar-collapsed");
        store.state.sidebarCollapsed = on;
        if (store.persist) store.persist();
        applyCollapsed(on);
      });
    }
  }

  // -------------------------------------------------------------- topbar
  function renderTopbar() {
    const time = document.querySelector("[data-slot='clock']");
    if (time) time.textContent = MU.clock();
    const reset = document.querySelector("[data-action='reset-demo']");
    if (reset) reset.addEventListener("click", function () { store.reset(); });
  }

  // ---------------------------------------------------------- chat panel
  function renderChat() {
    const panel = document.querySelector("[data-slot='chat']");
    if (!panel) return;

    // Header controls: view previous chats + clear current chat. Injected once
    // so we don't have to touch every page's HTML.
    var head = panel.querySelector(".chat__head");
    if (head && !head.querySelector(".chat__tools")) {
      var tools = document.createElement("div");
      tools.className = "chat__tools";
      tools.innerHTML =
        '<button class="chat__tool" type="button" data-chat="history" title="View previous chats">\u21bb Previous</button>' +
        '<button class="chat__tool" type="button" data-chat="clear" title="Clear this chat">Clear</button>';
      // place tools before the collapse toggle
      var tog = head.querySelector(".chat__toggle");
      if (tog) head.insertBefore(tools, tog); else head.appendChild(tools);

      tools.querySelector("[data-chat='clear']").addEventListener("click", function () {
        store.clearChat();
        closeHistory();
        paint();
      });
      tools.querySelector("[data-chat='history']").addEventListener("click", function () {
        toggleHistory();
      });
    }
    function updateTools() {
      var hb = panel.querySelector("[data-chat='history']");
      if (hb) hb.style.display = store.hasChatHistory() ? "" : "none";
      var cb = panel.querySelector("[data-chat='clear']");
      if (cb) cb.style.display = store.chat.length ? "" : "none";
    }

    // ---- previous-chats drawer ----
    function closeHistory() {
      var d = panel.querySelector(".chat__history");
      if (d) d.remove();
      var hb = panel.querySelector("[data-chat='history']");
      if (hb) hb.classList.remove("is-on");
    }
    function toggleHistory() {
      if (panel.querySelector(".chat__history")) { closeHistory(); return; }
      var hist = store.chatHistory;
      if (!hist.length) return;
      var userName = {};
      (store.data.users || []).forEach(function (u) { userName[String(u.id)] = u.name; });
      var drawer = document.createElement("div");
      drawer.className = "chat__history";
      drawer.innerHTML =
        '<div class="chat__history-head">Previous chats' +
        '<span class="chat__history-tools">' +
          '<button class="chat__tool chat__tool--danger" data-h="delall">Delete all</button>' +
          '<button class="chat__tool" data-h="close">Close \u00d7</button></span></div>' +
        hist.slice().reverse().map(function (sess, ri) {
          var idx = hist.length - 1 - ri;
          var who = userName[String(sess.user)] || ("Session " + (idx + 1));
          var lines = sess.messages.map(function (m) {
            var cls = m.from === "me" ? "hm--me" : "hm--ai";
            return '<div class="hm ' + cls + '"><b>' + (m.author || "") + "</b> " +
              "<span>" + (m.ts || "") + "</span><div>" +
              String(m.text || "").replace(/[&<>]/g, function (c) {
                return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]; }) + "</div></div>";
          }).join("");
          return '<details class="chat__history-sess"' + (ri === 0 ? " open" : "") + ">" +
            "<summary>" + who + " \u00b7 " + sess.messages.length + " messages" +
            (sess.endedAt ? " \u00b7 " + sess.endedAt : "") +
            ' <button class="chat__hist-del" data-h="del" data-i="' + idx + '" title="Delete this chat">\uD83D\uDDD1</button>' +
            "</summary>" +
            '<div class="chat__history-body">' + lines + "</div></details>";
        }).join("");
      panel.querySelector(".chat__body").appendChild(drawer);
      panel.querySelector("[data-chat='history']").classList.add("is-on");
      drawer.querySelector("[data-h='close']").addEventListener("click", closeHistory);
      drawer.querySelector("[data-h='delall']").addEventListener("click", function () {
        if (confirm("Delete all previous chats? This can't be undone.")) {
          store.deleteAllHistory();
          closeHistory();
          updateTools();
        }
      });
      drawer.querySelectorAll("[data-h='del']").forEach(function (b) {
        b.addEventListener("click", function (ev) {
          ev.preventDefault(); ev.stopPropagation();
          store.deleteHistorySession(parseInt(b.getAttribute("data-i"), 10));
          closeHistory(); toggleHistory(); updateTools();
        });
      });
    }

    if (store.state.chatCollapsed) panel.classList.add("is-collapsed");

    const toggle = panel.querySelector("[data-action='toggle-chat']");
    if (toggle) {
      toggle.addEventListener("click", function () {
        const collapsed = panel.classList.toggle("is-collapsed");
        store.setChatCollapsed(collapsed);
        toggle.textContent = collapsed ? "‹" : "›";
        updateFab();
      });
      toggle.textContent = store.state.chatCollapsed ? "‹" : "›";
    }

    // Floating AI button — opens the chat, prominent when it's collapsed.
    var fab = document.querySelector(".ai-fab");
    if (!fab) {
      fab = document.createElement("button");
      fab.className = "ai-fab";
      fab.type = "button";
      fab.setAttribute("aria-label", "Open AI assistant");
      fab.innerHTML = '<span class="ai-fab__txt">AI</span>';
      document.body.appendChild(fab);
      fab.addEventListener("click", function () {
        panel.classList.remove("is-collapsed");
        store.setChatCollapsed(false);
        if (toggle) toggle.textContent = "›";
        var input = panel.querySelector("[data-action='send-chat'] input");
        if (input) input.focus();
        updateFab();
      });
    }
    function updateFab() {
      // show the FAB when the chat is collapsed (or hidden on narrow screens)
      var collapsed = panel.classList.contains("is-collapsed");
      fab.classList.toggle("is-visible", collapsed || window.innerWidth <= 1280);
    }
    updateFab();
    window.addEventListener("resize", updateFab);

    // AND logo at the bottom of the chat panel
    if (!panel.querySelector(".chat__brand")) {
      var brand = document.createElement("div");
      brand.className = "chat__brand";
      brand.innerHTML = '<img class="chat__brand-logo" src="img/and-logo-dark.png?v=8" alt="AND">';
      panel.appendChild(brand);
    }

    function paint() {
      var messages = store.chat;
      var msgHost = panel.querySelector("[data-slot='chat-messages']");
      if (msgHost && !messages.length) {
        // fresh session: a single, non-persisted welcome (not a stored message)
        msgHost.innerHTML =
          '<div class="chat__welcome">' +
          '<div class="chat__welcome-mark">AI</div>' +
          "<p><b>AND Scheduling Assistant</b></p>" +
          "<p class=\"sm\">Ask about an order, what's blocking the floor, or how to " +
          "improve throughput. " + (store.canWrite
            ? "To change the plan, describe a disruption \u2014 I'll show a preview you approve before anything moves."
            : "Heads can apply plan changes from here.") + "</p>" +
          "</div>";
        if (typeof updateTools === "function") updateTools();
        return;
      }
      MU.render(msgHost,
        messages.map(function (message) {
          const node = MU.fromTemplate("tpl-chat-message");
          node.classList.add("msg--" + (message.from === "me" ? "me" : message.from === "ai" ? "ai" : "them"));
          MU.fill(node, { meta: message.author + " · " + message.ts, text: message.text });
          // visual answer card (order stepper, re-plan table) if data present
          if (message.from === "ai" && message.kind && message.data &&
              window.MChatCards) {
            var card = window.MChatCards.render(message.kind, message.data);
            if (card) {
              var txt = node.querySelector("[data-field='text']");
              if (txt) txt.parentNode.insertBefore(card, txt.nextSibling);
              else node.appendChild(card);
            }
          }
          // Sources are intentionally hidden for now. The assistant still
          // carries citations in message.data.citations; flip SHOW_SOURCES to
          // true once real data makes the sources meaningful to show.
          var SHOW_SOURCES = false;
          if (SHOW_SOURCES &&
              message.from === "ai" && message.data && message.data.citations &&
              message.data.citations.length && window.MChatCards &&
              window.MChatCards.citations) {
            var cite = window.MChatCards.citations(message.data.citations);
            if (cite) node.appendChild(cite);
          }
          return node;
        }));
      const body = panel.querySelector("[data-slot='chat-messages']");
      if (body) {
        var scroller = body.parentElement;
        // Anchor the view to the user's most recent question near the top, so the
        // assistant's reply flows beneath it and is read from the start — instead
        // of jamming to the container bottom (which lands you at the END of a long
        // reply, forcing a scroll up). If the whole exchange fits, just show the
        // bottom as usual.
        var kids = body.children;
        var lastMe = null;
        for (var i = kids.length - 1; i >= 0; i--) {
          if (kids[i].classList && kids[i].classList.contains("msg--me")) { lastMe = kids[i]; break; }
        }
        var fits = scroller.scrollHeight - scroller.clientHeight < 40;
        if (lastMe && !fits) {
          // position the user's question ~12px below the scroller's top edge,
          // measured via bounding rects so it's independent of offsetParent
          var delta = lastMe.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
          scroller.scrollTop = Math.max(0, scroller.scrollTop + delta - 12);
        } else {
          scroller.scrollTop = scroller.scrollHeight;
        }
      }
      if (typeof updateTools === "function") updateTools();
    }

    // suggested-prompt chips — fill the input and submit on click
    function renderPrompts(panel) {
      var host = panel.querySelector("[data-slot='chat-prompts']");
      if (!host) return;
      var chips = store.canWrite
        ? ["What's blocking the floor?",
           "How can I improve throughput?",
           "Which orders are at risk?",
           "Show line utilisation"]
        : ["What's blocking the floor?",
           "How can I improve throughput?",
           "Which orders are at risk?"];
      host.innerHTML = "";
      chips.forEach(function (text) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "chip";
        b.textContent = text;
        b.addEventListener("click", function () {
          var input = panel.querySelector("[data-action='send-chat'] input");
          if (input) {
            input.value = text;
            input.focus();
            // submit through the same handler
            var form = panel.querySelector("[data-action='send-chat']");
            form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
          }
        });
        host.appendChild(b);
      });
    }
    renderPrompts(panel);

    function clearTyping() {
      var arr = store.state.chat;
      for (var i = arr.length - 1; i >= 0; i--) {
        if (arr[i].kind === "typing") { arr.splice(i, 1); break; }
      }
    }

    async function proposeAndShow(text) {
      const p = await store.proposeConstraint(text);
      clearTyping();
      if (!p.ok) {
        const a = await store.askAssistant(text);
        store.pushChat("ai", "Scheduler AI",
          (a.answer || p.echo ||
           "I couldn't turn that into a plan change. Try naming a resource or order, e.g. \u201cBurn-in B2 is down till the 24th\u201d or \u201cprotect SO-1044\u201d."),
          { kind: a.kind, data: a.data });
      } else {
        store.pushChat("ai", "Scheduler AI",
          "Proposed re-plan for: " + p.echo + ". " + (p.summary || "") +
          " Review the changes below \u2014 nothing is applied until you approve.",
          { kind: "replan", data: {
              echo: p.echo, summary: p.summary, changes: p.changes,
              badges: p.badges || null, proposed: true,
              citations: null } });
        store.state.pendingProposal = { code: p.code, summary: p.summary, changes: p.changes, revision: 1 };
        store.persist();
        renderProposalActions(panel);
      }
    }

    // Route a free-text message: disruption -> propose; else -> answer.
    async function handleUserText(text) {
      store.pushChat("ai", "Scheduler AI", "\u2026", { kind: "typing" });
      paint();
      const looksLikeConstraint = /\b(down|offline|off-line|broken|break|fault|faulty|out sick|absent|short[- ]?staff|delay|delayed|late|rush|expedite|squeeze|bump|escalat|priorit|hold|halt|stop|overtime|split|reroute|re-route|move|shift|push|protect|maintenance|servicing|service|repair|pm|preventive|preventative|unavailable|not available|on leave|off today|sick)\b/i.test(text);
      try {
        // For heads, don't rely on a keyword regex to decide intent — try to
        // parse the message as a constraint first. If the parser can't build one
        // (it returns ok:false), fall back to answering. This means phrasings
        // the regex would miss ("Priya Sharma is not available today") still
        // reach the re-plan preview. Questions ("where is SO-1044?") simply
        // don't parse and fall through to the answer.
        var looksLikeQuestion = /^(where|what|which|how|who|when|why|is |are |does |do |can |show |list |tell )/i.test(text) || text.indexOf("?") >= 0;
        if (store.canWrite && !looksLikeQuestion) {
          const p = await store.proposeConstraint(text);
          if (p.ok) {
            clearTyping();
            var reassignNote = "";
            if (p.reassign && p.reassign.person) {
              reassignNote = " \u2014 " + p.reassign.person + " is now unavailable" +
                (p.reassign.tasks && p.reassign.tasks.length
                  ? "; their pending work (" + p.reassign.tasks.join(", ") +
                    ") can be reassigned on the Manpower tab."
                  : ". Reassign any of their work on the Manpower tab.");
            }
            store.pushChat("ai", "Scheduler AI",
              "Proposed re-plan for: " + p.echo + ". " + (p.summary || "") +
              reassignNote +
              " Review the changes below \u2014 nothing is applied until you approve.",
              { kind: "replan", data: {
                  echo: p.echo, summary: p.summary, changes: p.changes,
                  badges: p.badges || null, proposed: true,
                  citations: null } });
            store.state.pendingProposal = { code: p.code, summary: p.summary, changes: p.changes, revision: 1 };
            store.persist();
            renderProposalActions(panel);
          } else {
            const a = await store.askAssistant(text);
            clearTyping();
            store.pushChat("ai", "Scheduler AI", a.answer, { kind: a.kind, data: a.data });
          }
        } else {
          const a = await store.askAssistant(text);
          clearTyping();
          if (!store.canWrite && looksLikeConstraint) {
            store.pushChat("ai", "Scheduler AI",
              "Only a Department Head or Admin can change the plan. Here's what I can tell you: " +
              (a.answer || ""), { kind: a.kind, data: a.data });
          } else {
            store.pushChat("ai", "Scheduler AI", a.answer, { kind: a.kind, data: a.data });
          }
        }
      } catch (err) {
        clearTyping();
        store.pushChat("ai", "Scheduler AI",
          "I couldn't reach the scheduler just now. (" + err.message + ")");
      }
      paint();
    }

    // The constraint form builds a valid sentence, then routes it through the
    // SAME robust handler as typed chat (propose -> preview; fall back to an
    // answer if it doesn't parse). Any real error is surfaced, not swallowed.
    async function forceConstraint(text) {
      store.sendChat(text);
      paint();
      await handleUserText(text);
    }

    const form = panel.querySelector("[data-action='send-chat']");
    if (form) {
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        const input = form.querySelector("input");
        const text = input.value.trim();
        if (!text) return;
        store.sendChat(text);
        input.value = "";
        paint();
        await handleUserText(text);
      });
    }

    // ---- constraint form (structured entry) — heads only ----
    if (store.canWrite) {
      buildConstraintForm(panel, forceConstraint);
      // if store data hadn't loaded yet (chat painted before bootstrap), the
      // form's dropdowns would be empty — rebuild once data is in.
      if (!(store.data && store.data.orders && store.data.orders.length)) {
        var _tries = 0;
        var _iv = setInterval(function () {
          _tries++;
          if (store.data && store.data.orders && store.data.orders.length) {
            var old = panel.querySelector(".cform");
            if (old) old.remove();
            buildConstraintForm(panel, forceConstraint);
            clearInterval(_iv);
          } else if (_tries > 20) {
            clearInterval(_iv);   // give up after ~5s; form still works, just no presets
          }
        }, 250);
      }
    }

    // Build a compact structured form so a head can add any constraint without
    // free-typing. It assembles a sentence the parser reliably understands and
    // routes it through the same propose -> preview -> approve flow.
    function buildConstraintForm(panel, submit) {
      var actions = panel.querySelector("[data-slot='chat-actions']");
      var composer = panel.querySelector(".chat__composer");
      if (!actions || !composer || panel.querySelector(".cform")) return;

      // options sourced from the live store
      var resources = ((store.data && store.data.workCentres) || [])
        .map(function (w) { return w.name; });
      var orders = ((store.data && store.data.orders) || [])
        .map(function (o) { return o.code; });
      var people = ((store.data && store.data.users) || [])
        .filter(function (u) { return u.role === "Employee"; })
        .map(function (u) { return u.name; });

      var TYPES = [
        { v: "machine", label: "Machine down / maintenance" },
        { v: "manpower", label: "Manpower gap (absence)" },
        { v: "material", label: "Material / parts delay" },
        { v: "priority", label: "Priority change (protect / bump)" },
        { v: "quality", label: "Quality hold" },
        { v: "rush", label: "Rush order" },
        { v: "capacity", label: "Add capacity (overtime)" }
      ];

      var wrap = document.createElement("div");
      wrap.className = "cform is-collapsed";
      wrap.innerHTML =
        '<button type="button" class="cform__toggle" data-c="toggle">' +
          '<span class="cform__plus">+</span> Add constraint</button>' +
        '<div class="cform__body">' +
          '<label class="cform__f"><span>Type</span>' +
            '<select data-c="type">' + TYPES.map(function (t) {
              return '<option value="' + t.v + '">' + t.label + "</option>"; }).join("") +
            "</select></label>" +
          '<div class="cform__target" data-c="target"></div>' +
          '<div class="cform__when" data-c="when">' +
            '<label class="cform__f"><span>When</span>' +
              '<select data-c="when-mode">' +
                '<option value="">— open-ended —</option>' +
                '<option value="today">Today</option>' +
                '<option value="tomorrow">Tomorrow</option>' +
                '<option value="shift">A shift…</option>' +
                '<option value="date">Until a date…</option>' +
              "</select></label>" +
            '<label class="cform__f cform__hide" data-c="shift-wrap"><span>Shift</span>' +
              '<select data-c="shift"><option>A</option><option>B</option><option>C</option></select></label>' +
            '<label class="cform__f cform__hide" data-c="date-wrap"><span>Date</span>' +
              '<input type="date" data-c="date"></label>' +
          "</div>" +
          '<div class="cform__foot">' +
            '<button type="button" class="btn btn--primary btn--sm" data-c="submit">Preview change</button>' +
            '<button type="button" class="btn btn--sm" data-c="cancel">Cancel</button>' +
            '<span class="cform__msg" data-c="msg"></span>' +
          "</div>" +
        "</div>";
      composer.parentNode.insertBefore(wrap, composer);

      var typeSel = wrap.querySelector("[data-c='type']");
      var targetHost = wrap.querySelector("[data-c='target']");
      var whenBlock = wrap.querySelector("[data-c='when']");
      var whenMode = wrap.querySelector("[data-c='when-mode']");

      function opts(list, ph) {
        return '<option value="">' + (ph || "— select —") + "</option>" +
          list.map(function (x) { return '<option>' + x + "</option>"; }).join("");
      }
      // build the target field(s) per constraint type
      function renderTarget() {
        var v = typeSel.value;
        whenBlock.style.display = "";
        if (v === "machine") {
          targetHost.innerHTML =
            '<label class="cform__f"><span>Machine</span><select data-c="res">' +
            opts(resources) + "</select></label>" +
            '<label class="cform__f"><span>Reason</span><select data-c="reason">' +
            '<option value="down">Breakdown / offline</option>' +
            '<option value="maintenance">Maintenance</option></select></label>';
        } else if (v === "manpower") {
          targetHost.innerHTML =
            '<label class="cform__f"><span>Who</span><select data-c="person">' +
            '<option value="">— a group —</option>' + opts(people, "").replace('<option value=""></option>','') +
            "</select></label>" +
            '<label class="cform__f"><span>Or group</span><select data-c="group">' +
            '<option value="">—</option><option>Assembly</option><option>Calibration</option>' +
            '<option>Burn-in</option><option>QC</option><option>Packing</option></select></label>' +
            '<label class="cform__f"><span>How many</span><input type="number" min="1" max="6" value="1" data-c="count"></label>';
        } else if (v === "material") {
          targetHost.innerHTML =
            '<label class="cform__f"><span>Order (optional)</span><select data-c="order">' +
            opts(orders, "— any —") + "</select></label>" +
            '<label class="cform__f"><span>Item</span><input type="text" data-c="item" placeholder="e.g. diaphragms"></label>';
        } else if (v === "priority") {
          targetHost.innerHTML =
            '<label class="cform__f"><span>Order</span><select data-c="order">' + opts(orders) + "</select></label>" +
            '<label class="cform__f"><span>Action</span><select data-c="paction">' +
            '<option value="protect">Protect (keep on time)</option>' +
            '<option value="bump">Bump to top priority</option></select></label>';
          whenBlock.style.display = "none";
        } else if (v === "quality") {
          targetHost.innerHTML =
            '<label class="cform__f"><span>Order</span><select data-c="order">' + opts(orders) + "</select></label>";
          whenBlock.style.display = "none";
        } else if (v === "rush") {
          targetHost.innerHTML =
            '<label class="cform__f"><span>Quantity</span><input type="number" min="1" value="10" data-c="qty"></label>' +
            '<label class="cform__f"><span>Product/line (optional)</span><input type="text" data-c="prod" placeholder="e.g. PT-3051"></label>';
        } else if (v === "capacity") {
          targetHost.innerHTML =
            '<label class="cform__f"><span>Where</span><select data-c="group">' +
            '<option>Calibration</option><option>Burn-in</option><option>Assembly</option><option>Packing</option></select></label>' +
            '<label class="cform__f"><span>How</span><select data-c="how">' +
            '<option value="overtime">Overtime</option><option value="bench">Add a bench</option>' +
            '<option value="operators">More operators</option></select></label>';
          whenBlock.style.display = "none";
        }
      }
      typeSel.addEventListener("change", renderTarget);
      renderTarget();

      whenMode.addEventListener("change", function () {
        wrap.querySelector("[data-c='shift-wrap']").classList.toggle("cform__hide", whenMode.value !== "shift");
        wrap.querySelector("[data-c='date-wrap']").classList.toggle("cform__hide", whenMode.value !== "date");
      });

      // build a parser-friendly sentence from the selections
      function compose() {
        var v = typeSel.value, q = function (s) { var e = wrap.querySelector("[data-c='" + s + "']"); return e ? e.value.trim() : ""; };
        var when = "";
        if (whenBlock.style.display !== "none") {
          var wm = whenMode.value;
          if (wm === "today") when = " today";
          else if (wm === "tomorrow") when = " tomorrow";
          else if (wm === "shift") when = " in shift " + q("shift");
          else if (wm === "date" && q("date")) {
            var d = new Date(q("date"));
            when = " until the " + d.getDate() + " " +
              ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getMonth()];
          }
        }
        if (v === "machine") {
          if (!q("res")) return null;
          return q("res") + (q("reason") === "maintenance" ? " maintenance" : " is down") + when;
        }
        if (v === "manpower") {
          if (q("person")) return q("person") + " is not available" + when;
          var grp = q("group") || "Assembly"; var n = q("count") || "1";
          return n + " " + grp.toLowerCase() + " operators are off" + when;
        }
        if (v === "material") {
          var it = q("item") || "parts";
          return (q("order") ? q("order") + " " : "") + "waiting on " + it + when;
        }
        if (v === "priority") {
          if (!q("order")) return null;
          return (q("paction") === "bump" ? "bump " + q("order") + " to top priority"
            : "protect " + q("order"));
        }
        if (v === "quality") { return q("order") ? "put " + q("order") + " on quality hold" : null; }
        if (v === "rush") {
          return "rush order " + (q("qty") || "10") + " units" + (q("prod") ? " " + q("prod") : "");
        }
        if (v === "capacity") {
          var g = q("group") || "Calibration"; var how = q("how");
          if (how === "bench") return "add a bench to " + g.toLowerCase();
          if (how === "operators") return "add operators to " + g.toLowerCase();
          return "overtime on " + g.toLowerCase();
        }
        return null;
      }

      wrap.querySelector("[data-c='toggle']").addEventListener("click", function () {
        wrap.classList.toggle("is-collapsed");
      });
      wrap.querySelector("[data-c='cancel']").addEventListener("click", function () {
        wrap.classList.add("is-collapsed");
      });
      wrap.querySelector("[data-c='submit']").addEventListener("click", async function () {
        var text = compose();
        var msg = wrap.querySelector("[data-c='msg']");
        if (!text) { msg.textContent = "Fill in the required field."; return; }
        msg.textContent = "";
        wrap.classList.add("is-collapsed");
        await submit(text);   // forceConstraint echoes the user message itself
      });
    }

    // Approve / reject buttons for a live proposal
    function renderProposalActions(panel) {
      var actionsHost = panel.querySelector("[data-slot='chat-actions']");
      if (!actionsHost) return;
      // use a dedicated child so we never clobber the "Add constraint" form,
      // which lives in the same chat-actions host.
      var host = actionsHost.querySelector("[data-slot='prop-actions']");
      if (!host) {
        host = document.createElement("div");
        host.setAttribute("data-slot", "prop-actions");
        actionsHost.insertBefore(host, actionsHost.firstChild);
      }
      var prop = store.state.pendingProposal;
      if (!prop) { host.innerHTML = ""; return; }
      host.innerHTML =
        '<div class="prop-box">' +
          '<div class="prop-box__title">This is a preview \u2014 the live board updates only when you approve.</div>' +
          '<div class="prop-box__row">' +
            '<button class="btn btn--primary" data-act="approve">\u2713 Approve &amp; apply</button>' +
            '<button class="btn" data-act="discard">Discard</button>' +
          '</div>' +
          '<div class="prop-box__dir">' +
            '<label>Want different changes? Tell the scheduler what to do instead:</label>' +
            '<div class="prop-box__inrow">' +
              '<input type="text" data-a="dir" placeholder="e.g. protect SO-1044, push low-priority orders instead" />' +
              '<button class="btn" data-act="revise">Re-plan</button>' +
            '</div>' +
          '</div>' +
        '</div>';

      host.querySelector("[data-act='approve']").addEventListener("click", async function () {
        try {
          var r = await store.applyConstraint(prop.code);
          store.pushChat("ai", "Scheduler AI",
            "Applied to the live floor \u2014 " + (r.orders_updated || 0) + " orders updated. The board now reflects these changes.");
          store.state.pendingProposal = null; store.persist();
          host.innerHTML = "";
          await store.refresh();
          paint();
          if (window.MPage && window.MPage.render) window.MPage.render(store);
        } catch (e) {
          store.pushChat("ai", "Scheduler AI", "Apply failed: " + e.message); paint();
        }
      });

      host.querySelector("[data-act='discard']").addEventListener("click", function () {
        store.pushChat("ai", "Scheduler AI", "Discarded \u2014 nothing changed on the live board.");
        store.state.pendingProposal = null; store.persist();
        host.innerHTML = "";
        paint();
      });

      host.querySelector("[data-act='revise']").addEventListener("click", async function () {
        var fb = host.querySelector("[data-a='dir']").value.trim();
        if (!fb) return;
        try {
          prop.revision = (prop.revision || 1) + 1;
          var r = await store.reviseConstraint(prop.code, fb, prop.revision);
          store.pushChat("ai", "Scheduler AI",
            "Revised plan (" + (r.directive || fb) + "). " + (r.summary || "") +
            " Still a preview \u2014 approve to apply.",
            { kind: "replan", data: {
                echo: "Revision " + prop.revision, summary: r.summary,
                changes: r.changes, badges: r.badges || null, proposed: true } });
          store.state.pendingProposal = { code: prop.code, summary: r.summary,
            changes: r.changes, revision: prop.revision };
          store.persist();
          paint();
          renderProposalActions(panel);
        } catch (e) {
          store.pushChat("ai", "Scheduler AI", "Revise failed: " + e.message); paint();
        }
      });
    }
    paint();
  }

  // ----------------------------------------------------------------- boot
  async function boot() {
    await store.loadAll();          // fetch live data (falls back to data.js)
    const page = document.body.dataset.page;

    if (WRITE_ONLY.indexOf(page) > -1 && !store.canWrite) {
      window.location.replace("index.html");
      return;
    }

    renderSidebar(page);
    renderTopbar();
    renderChat();

    if (window.MPage && typeof window.MPage.render === "function") {
      window.MPage.render(store);
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})(window, document);
