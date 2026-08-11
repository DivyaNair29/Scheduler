/* ============================================================================
   store.js — the single source of truth the pages read from.

   Today it wraps window.MERIDIAN_DATA and keeps user edits in localStorage.
   Point loadAll() at Flask endpoints later and no page script changes:

       await fetch("/api/orders").then(r => r.json())

   Attaches to window.MStore.
   ==========================================================================*/
(function (window) {
  "use strict";

  const KEY = "meridian.site.state.v1";

  function readState() {
    try { return JSON.parse(window.localStorage.getItem(KEY)) || {}; }
    catch (e) { return {}; }
  }

  const Store = {
    data: null,
    state: null,

    /* Try the Flask backend first; fall back to the bundled mock data so the
       site still opens as a standalone static page. Called by main.js boot(). */
    loadAll: async function () {
      try {
        const res = await fetch("/api/bootstrap", { headers: { "Accept": "application/json" } });
        if (res.ok) {
          window.MERIDIAN_DATA = await res.json();
        }
      } catch (e) {
        /* offline / no server — keep window.MERIDIAN_DATA from data.js */
      }
      return this.init();
    },

    init: function () {
      this.data = window.MERIDIAN_DATA;
      const saved = readState();
      this.state = {
        userId: saved.userId || this.data.session.userId,
        constraintPatches: saved.constraintPatches || {},
        newConstraints: saved.newConstraints || [],
        confirmations: saved.confirmations || [],
        job: saved.job || { order: "", stage: "", done: [] },
        chat: saved.chat || [],
        chatHistory: saved.chatHistory || [],   // archived past sessions
        chatSession: saved.chatSession || null,  // which login owns state.chat
        chatCollapsed: saved.chatCollapsed || false,
        intake: saved.intake || []
      };
      // A NEW login starts with a clear chat: if the active user differs from
      // the one who owns the current chat buffer, archive it to history and
      // begin fresh. Past chats remain viewable via "previous chats".
      var sessionKey = String(this.state.userId);
      if (this.state.chatSession && this.state.chatSession !== sessionKey
          && this.state.chat.length) {
        this.state.chatHistory.push({
          user: this.state.chatSession,
          endedAt: (window.MU && window.MU.clock ? window.MU.clock() : ""),
          messages: this.state.chat.slice()
        });
        // cap history so localStorage doesn't grow unbounded
        if (this.state.chatHistory.length > 20)
          this.state.chatHistory = this.state.chatHistory.slice(-20);
        this.state.chat = [];
      }
      this.state.chatSession = sessionKey;
      this.persist();
      return this;
    },

    persist: function () {
      window.localStorage.setItem(KEY, JSON.stringify(this.state));
    },

    reset: function () {
      window.localStorage.removeItem(KEY);
      window.location.reload();
    },

    // ------------------------------------------------------------ identity
    get user() {
      var data = this.data || window.MERIDIAN_DATA || {};
      var users = data.users || [];
      if (!users.length) return { id: 0, name: "", role: "Employee" };
      var uid = (this.state && this.state.userId);
      return users.find(function (u) { return u.id === uid; }) || users[1] || users[0];
    },
    get canWrite() {
      var r = this.user && this.user.role;
      return r === "Department Head" || r === "Admin";
    },
    setUser: function (id) {
      var newKey = String(id);
      if (this.state.chatSession && this.state.chatSession !== newKey
          && this.state.chat.length) {
        this.state.chatHistory.push({
          user: this.state.chatSession,
          endedAt: (window.MU && window.MU.clock ? window.MU.clock() : ""),
          messages: this.state.chat.slice()
        });
        this.state.chat = [];
      }
      this.state.userId = id;
      this.state.chatSession = newKey;
      this.persist();
    },

    // -------------------------------------------------------------- orders
    get orders() { return this.data.orders; },
    ordersByPhase: function (phase) {
      var DISPATCH_STAGES = ["Packing", "Dispatch", "Shipping", "Shipping & Dispatch"];
      var list = this.orders.filter(function (o) {
        // split the old "quality" phase: dispatch-stage orders -> "dispatch"
        if (o.phase === "quality") {
          var isDispatch = DISPATCH_STAGES.indexOf(o.stage) > -1;
          return phase === "dispatch" ? isDispatch : (phase === "quality" && !isDispatch);
        }
        return o.phase === phase;
      });
      // prioritised orders (locked/protected, then rush) float to the top of
      // their column so they're immediately visible on the dashboard.
      return list.sort(function (a, b) {
        var pa = (a.locked ? 2 : 0) + (a.rush ? 1 : 0);
        var pb = (b.locked ? 2 : 0) + (b.rush ? 1 : 0);
        return pb - pa;
      });
    },
    ordersByLine: function (line) {
      return this.orders.filter((o) => o.line === line).sort((a, b) => a.startMin - b.startMin);
    },
    ordersByStages: function (stages) { return this.orders.filter((o) => stages.indexOf(o.stage) > -1); },

    get intakeQueue() { return this.state.intake.concat(this.data.intakeQueue); },
    addIntake: function (entry) { this.state.intake.unshift(entry); this.persist(); },

    // --------------------------------------------------------- constraints
    get constraints() {
      const patches = this.state.constraintPatches;
      const seeded = this.data.constraints.map((c) => Object.assign({}, c, patches[c.code] || {}));
      return this.state.newConstraints.concat(seeded);
    },
    constraint: function (code) { return this.constraints.find((c) => c.code === code); },
    pendingCount: function () {
      return this.constraints.filter((c) => c.status === "pending" || c.status === "approved").length;
    },

    raiseConstraint: function (fields) {
      const numbers = this.constraints
        .map((c) => Number(String(c.code).split("-")[1])).filter(isFinite);
      const entry = {
        code: "C-" + ((numbers.length ? Math.max.apply(null, numbers) : 200) + 1),
        raisedBy: this.user.name, role: this.user.role,
        order: fields.order || "—", stage: fields.stage || "—",
        type: fields.type, note: fields.note,
        status: "pending", revision: 1, feedback: "", ts: window.MU.stamp()
      };
      this.state.newConstraints.unshift(entry);
      this.persist();
      return entry;
    },

    patchConstraint: function (code, patch) {
      const own = this.state.newConstraints.find((c) => c.code === code);
      if (own) Object.assign(own, patch);
      else this.state.constraintPatches[code] =
        Object.assign({}, this.state.constraintPatches[code] || {}, patch);
      this.persist();
    },

    approveConstraint: function (code) { this.patchConstraint(code, { status: "approved" }); },
    rejectConstraint:  function (code) { this.patchConstraint(code, { status: "rejected" }); },
    approveSchedule:   function (code) { this.patchConstraint(code, { status: "applied" }); },
    reviseSchedule: function (code, feedback) {
      const current = this.constraint(code);
      this.patchConstraint(code, {
        revision: (current.revision || 1) + 1, feedback: feedback, status: "approved"
      });
    },

    /* The before → after rows an approval generates. Revision 1 slips the
       promise; later revisions honour the production head's note. */
    scheduleChanges: function (constraint) {
      const rows = [
        { what: constraint.order + " · " + constraint.stage,
          from: "Line 3 · 06:15", to: "Line 1 · 08:40", note: "reroute around the constraint" },
        { what: "SO-1042 · Calibration",
          from: "Bench C2 · 09:00", to: "Bench C3 · 09:00", note: "freed capacity absorbed" }
      ];
      rows.push((constraint.revision || 1) > 1
        ? { what: constraint.order + " · promised date", from: "31 Jul", to: "31 Jul",
            note: "held per your revision — slip absorbed by splitting the batch 60/40" }
        : { what: constraint.order + " · promised date", from: "31 Jul", to: "1 Aug",
            note: "+1 day slip, sales notified" });
      return rows;
    },

    // -------------------------------------------------------- confirmations
    get confirmations() { return this.state.confirmations.concat(this.data.confirmations); },
    get job() { return this.state.job; },
    setJob: function (order, stage) {
      this.state.job = { order: order, stage: stage, done: [] };
      this.persist();
    },
    checklistFor: function (stage) { return this.data.checklists[stage] || []; },
    toggleCheck: function (item) {
      const job = this.state.job;
      const index = job.done.indexOf(item);
      if (index > -1) {
        job.done.splice(index, 1);
        this.state.confirmations = this.state.confirmations.filter(
          (c) => !(c.order === job.order && c.stage === job.stage && c.item === item));
      } else {
        job.done.push(item);
        this.state.confirmations.unshift({
          order: job.order, stage: job.stage, item: item,
          operator: this.user.name, ts: window.MU.stamp()
        });
      }
      this.persist();
    },

    // ----------------------------------------------------------------- chat
    get chat() {
      // The live buffer belongs to whoever owns the current session. If the
      // active user doesn't match (e.g. an account switch that hasn't been
      // reconciled yet), show nothing rather than leak another user's chat.
      if (this.state.chatSession && this.state.chatSession !== String(this.state.userId)) {
        return [];
      }
      return this.state.chat;
    },
    // Only the CURRENT user's own past sessions — never another account's.
    // Each archived session is tagged with the user id that owns it; the
    // employee must never see the head's chats and vice-versa.
    get chatHistory() {
      var me = String(this.state.userId);
      return (this.state.chatHistory || []).filter(function (s) {
        return String(s.user) === me;
      });
    },
    hasChatHistory: function () { return this.chatHistory.length > 0; },
    deleteAllHistory: function () {
      // remove only the CURRENT user's archived sessions
      var me = String(this.state.userId);
      this.state.chatHistory = (this.state.chatHistory || [])
        .filter(function (s) { return String(s.user) !== me; });
      this.persist();
    },
    deleteHistorySession: function (filteredIndex) {
      // filteredIndex indexes THIS user's visible history; map to the full array
      var me = String(this.state.userId);
      var seen = -1;
      for (var i = 0; i < (this.state.chatHistory || []).length; i++) {
        if (String(this.state.chatHistory[i].user) === me) {
          seen++;
          if (seen === filteredIndex) {
            this.state.chatHistory.splice(i, 1);
            this.persist();
            return;
          }
        }
      }
    },
    clearChat: function () {
      if (this.state.chat.length) {
        this.state.chatHistory.push({
          user: this.state.chatSession,
          endedAt: (window.MU && window.MU.clock ? window.MU.clock() : ""),
          messages: this.state.chat.slice()
        });
      }
      this.state.chat = [];
      this.persist();
    },
    sendChat: function (text) {
      this.state.chat.push({ from: "me", author: this.user.name, ts: window.MU.clock(), text: text });
      this.persist();
    },
    pushChat: function (from, author, text, extra) {
      var msg = { from: from, author: author, ts: window.MU.clock(), text: text };
      if (extra) { msg.kind = extra.kind; msg.data = extra.data; }
      this.state.chat.push(msg);
      this.persist();
    },

    /* Ask the assistant a question (order status, constraints, suggestions).
       Returns the answer text. */
    askAssistant: async function (question) {
      const res = await fetch("/api/scheduler/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question })
      });
      if (!res.ok) throw new Error("ask failed");
      return res.json();               // { answer, kind, data }
    },

    /* Propose a constraint in natural language. Returns the parsed constraint
       echo + the before->after changes for approval. */
    proposeConstraint: async function (text) {
      const res = await fetch("/api/scheduler/propose", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      });
      // Parse the body even on a non-2xx so the caller can fall back to an
      // answer gracefully instead of throwing an opaque "propose failed".
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      if (!res.ok) {
        return { ok: false, echo: (data && (data.error || data.echo)) ||
          ("Scheduler returned " + res.status), _status: res.status };
      }
      return data || { ok: false, echo: "Empty response from scheduler." };
    },

    /* Approve a proposed constraint -> applies to the live floor. */
    applyConstraint: async function (code) {
      const res = await fetch("/api/scheduler/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code })
      });
      if (!res.ok) throw new Error("apply failed");
      return res.json();               // { ok, applied, orders_updated }
    },

    /* Reject with feedback -> revised schedule honouring the instruction. */
    reviseConstraint: async function (code, feedback, revision) {
      const res = await fetch("/api/scheduler/revise", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code, feedback: feedback, revision: revision || 2 })
      });
      if (!res.ok) throw new Error("revise failed");
      return res.json();               // { ok, directive, summary, changes }
    },

    /* Re-pull live data after an action so the whole UI reflects it. */
    refresh: async function () {
      try {
        const res = await fetch("/api/bootstrap", { headers: { "Accept": "application/json" } });
        if (res.ok) { this.data = await res.json(); }
      } catch (e) { /* keep current data */ }
      return this;
    },
    setChatCollapsed: function (collapsed) {
      this.state.chatCollapsed = collapsed;
      this.persist();
    },

    // -------------------------------------------------------------- badges
    badge: function (key) {
      if (key === "orders") return this.orders.length;
      if (key === "mfgOrders") return this.ordersByPhase("mfg").length;
      if (key === "intake") return this.intakeQueue.length;
      if (key === "approvals") return this.canWrite ? (this.pendingCount() || null) : null;
      if (key === "log") return (this.data.logCount || null);
      return null;
    }
  };

  window.MStore = Store;
})(window);
