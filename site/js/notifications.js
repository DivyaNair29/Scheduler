/* Notification poller — shows assignment pop-ups to the current employee.
   Polls /api/notifications for the logged-in user and surfaces unread ones. */
(function (window, document) {
  "use strict";

  function currentUserId() {
    var s = window.MStore;
    return s && s.user ? s.user.id : null;
  }

  function ensureHost() {
    var host = document.querySelector(".notif-host");
    if (!host) {
      host = document.createElement("div");
      host.className = "notif-host";
      document.body.appendChild(host);
    }
    return host;
  }

  function showToast(n, onOpen) {
    var host = ensureHost();
    var t = document.createElement("div");
    t.className = "notif-toast";
    t.innerHTML =
      '<div class="notif-toast__icon">\uD83D\uDD14</div>' +
      '<div class="notif-toast__body"><div class="notif-toast__title">' + n.title + "</div>" +
      '<div class="notif-toast__detail">' + (n.detail || "") + "</div></div>" +
      '<button class="notif-toast__x" aria-label="Dismiss">\u00d7</button>';
    t.querySelector(".notif-toast__x").addEventListener("click", function () {
      t.remove(); onOpen([n.id]);
    });
    // clicking the body takes an employee to their confirmations
    t.querySelector(".notif-toast__body").addEventListener("click", function () {
      onOpen([n.id]);
      window.location.href = "quality.html";
    });
    host.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.remove(); }, 12000);
  }

  var seen = {};

  async function poll() {
    var uid = currentUserId();
    if (uid == null) return;
    try {
      var d = await fetch("/api/notifications?userId=" + uid + "&unread=1")
        .then(function (r) { return r.json(); });
      (d.notifications || []).forEach(function (n) {
        if (seen[n.id]) return;
        seen[n.id] = true;
        showToast(n, markRead);
      });
      // badge on the sidebar (Stage Confirmation) if present
      var badge = document.querySelector("[data-nav='quality'] .nav-item__badge");
      if (badge && d.unread) badge.textContent = d.unread;
    } catch (e) { /* ignore */ }
  }

  async function markRead(ids) {
    var uid = currentUserId();
    try {
      await fetch("/api/notifications/read", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId: uid, ids: ids })
      });
    } catch (e) { /* ignore */ }
  }

  function start() {
    poll();
    setInterval(poll, 15000);   // gentle poll every 15s
  }

  if (document.readyState !== "loading") setTimeout(start, 800);
  else document.addEventListener("DOMContentLoaded", function () { setTimeout(start, 800); });
})(window, document);
