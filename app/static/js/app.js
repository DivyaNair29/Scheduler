/* Progressive enhancement only — every page works without JavaScript.
   Server-rendered forms are the source of truth; this file just smooths edges. */
(function () {
  "use strict";

  // Auto-submit stage assignment selects already handled inline; here we guard
  // against double submits on the approval chain.
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      form.querySelectorAll("button[type=submit]").forEach(function (btn) {
        window.setTimeout(function () { btn.disabled = true; }, 0);
      });
    });
  });

  // Reject & revise needs a reason — block the round trip when it is empty.
  document.querySelectorAll(".sched-form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var pressed = document.activeElement;
      if (pressed && pressed.value === "reject") {
        var field = form.querySelector("input[name=feedback]");
        if (field && !field.value.trim()) {
          event.preventDefault();
          field.focus();
          field.placeholder = "Tell the planner what to change first";
        }
      }
    });
  });

  // Dismiss flash messages after a beat.
  window.setTimeout(function () {
    document.querySelectorAll(".flash").forEach(function (el) {
      el.style.transition = "opacity .4s ease";
      el.style.opacity = "0";
      window.setTimeout(function () { el.remove(); }, 400);
    });
  }, 5000);
})();
