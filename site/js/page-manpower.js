/* Manpower — who's on shift, current assignment, load, skills, capacity. */
(function (window, document) {
  "use strict";

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  async function render() {
    var mount = document.querySelector("[data-slot='manpower-mount']");
    if (!mount) return;
    mount.innerHTML = '<p class="muted">Loading…</p>';
    var d;
    try { d = await fetch("/api/manpower").then(function (r) { return r.json(); }); }
    catch (e) { mount.innerHTML = '<p class="muted">Could not load manpower.</p>'; return; }
    mount.innerHTML = "";

    var store = window.MStore;
    var canWrite = !!(store && store.canWrite);

    // (The standalone operator-first assign card was removed — per-shift
    // "+ Assign work" covers this. Kept disabled to avoid building unused DOM.)
    if (false && canWrite && d.employees && d.employees.length && d.assignableWork && d.assignableWork.length) {
      var assignCard = el("div", "card mp-assign");
      assignCard.appendChild(el("h2", "card__label", "ASSIGN WORK TO AN OPERATOR"));
      assignCard.appendChild(el("p", "muted sm",
        "Pick an operator, then tick one or more tasks they're trained for and confirm. "
        + "They're notified for each task and work through them in order. Untrained "
        + "tasks are hidden — train the operator first to unlock them."));
      var empOpts = d.employees.map(function (e2) {
        return '<option value="' + e2.id + '">' + e2.name +
          (e2.skill ? " (" + e2.skill + ")" : "") + "</option>";
      }).join("");
      var form = el("div", "mp-assign2");
      form.innerHTML =
        '<label class="mp-assign2__op">Operator<select data-a="emp">' + empOpts + "</select></label>" +
        '<div class="mp-assign2__tasks" data-a="tasks"></div>' +
        '<div class="mp-assign2__foot">' +
          '<button class="btn btn--primary" data-a="confirm">Confirm &amp; notify</button>' +
          '<span class="mp-assign__msg" data-a="msg"></span>' +
        "</div>";
      assignCard.appendChild(form);
      window._mpAssignCard = assignCard;

      var empSel = form.querySelector("[data-a='emp']");
      var taskWrap = form.querySelector("[data-a='tasks']");
      var msg = form.querySelector("[data-a='msg']");

      function empById(id) {
        return d.employees.filter(function (e2) { return e2.id == id; })[0];
      }
      function refreshTasks() {
        var emp = empById(empSel.value);
        var can = (emp && emp.canDo) || [];
        var eligible = d.assignableWork.filter(function (w) { return can.indexOf(w.stage) > -1; });
        if (!eligible.length) {
          taskWrap.innerHTML = '<p class="mp-err">' + (emp ? emp.name : "This operator") +
            " isn't trained for any pending task. Train them (\u201c+ train\u201d on their card) to assign work.</p>";
          return;
        }
        taskWrap.innerHTML = eligible.map(function (w, i) {
          var id = d.assignableWork.indexOf(w);
          return '<label class="mp-task-check"><input type="checkbox" value="' + id + '">' +
            "<span><b>" + w.order + "</b> \u00b7 " + w.stage +
            ' <i>(' + w.product + " \u00b7 " + w.line + ")</i></span></label>";
        }).join("");
      }
      empSel.addEventListener("change", function () { msg.innerHTML = ""; refreshTasks(); });
      refreshTasks();

      form.querySelector("[data-a='confirm']").addEventListener("click", async function () {
        var emp = empById(empSel.value);
        if (!emp) { msg.innerHTML = '<span class="mp-err">Pick an operator.</span>'; return; }
        var picks = Array.prototype.slice.call(taskWrap.querySelectorAll("input:checked"))
          .map(function (cb) { return d.assignableWork[parseInt(cb.value, 10)]; });
        if (!picks.length) { msg.innerHTML = '<span class="mp-err">Tick at least one task.</span>'; return; }
        var by = (store && store.user && store.user.name) || "Department Head";
        var okCount = 0, errs = [];
        for (var i = 0; i < picks.length; i++) {
          var w = picks[i];
          try {
            var res = await fetch("/api/assign", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ order: w.order, stage: w.stage,
                employeeId: parseInt(emp.id, 10), by: by })
            }).then(function (r) { return r.json(); });
            if (res.ok) okCount++; else errs.push(w.order + " · " + w.stage + ": " + (res.error || "failed"));
          } catch (e) { errs.push(w.order + " · " + w.stage + ": failed"); }
        }
        var out = "";
        if (okCount) out += '<span class="mp-ok">\u2713 Assigned ' + okCount + " task" +
          (okCount > 1 ? "s" : "") + " to " + emp.name + " — notified.</span>";
        if (errs.length) out += '<span class="mp-err"> ' + errs.join("; ") + "</span>";
        msg.innerHTML = out;
        if (okCount) setTimeout(render, 900);
      });
    }

    // summary
    var sum = el("div", "mp-stats");
    sum.innerHTML =
      statCard(d.totals.operators, "operators") +
      statCard(d.totals.onShift, "on assignment") +
      statCard(d.totals.idle, "idle / available");
    mount.appendChild(sum);

    // shifts with assignments
    var shiftWrap = el("div", "card");
    shiftWrap.appendChild(el("h2", "card__label", "SHIFTS &amp; CURRENT ASSIGNMENTS"));
    ["A", "B", "C"].forEach(function (s) {
      var people = d.shifts[s] || [];
      var row = el("div", "mp-shift");
      var head = el("div", "mp-shift__head",
        "<b>Shift " + s + "</b> <span>" + (d.shiftTimes[s] || "") + "</span>");
      // + Assign work / + Add employee (heads only)
      if (canWrite) {
        var assignBtn = el("button", "mp-addbtn mp-addbtn--assign", "+ Assign work");
        head.appendChild(assignBtn);
        assignBtn.addEventListener("click", function () {
          showShiftAssign(row, s, people);
        });
        var addBtn = el("button", "mp-addbtn", "+ Add employee");
        head.appendChild(addBtn);
        addBtn.addEventListener("click", function () {
          showAddForm(row, s);
        });
      }
      row.appendChild(head);
      var grid = el("div", "mp-people");
      people.forEach(function (p) {
        var card = el("div", "mp-person" + (p.idle ? " is-idle" : "") + (p.absent ? " is-absent" : ""));
        var extraStages = p.extraStages || [];
        var canDoHtml = (p.canDo && p.canDo.length)
          ? p.canDo.map(function (st) {
              var isExtra = extraStages.indexOf(st) > -1;
              return '<span class="mp-task' + (isExtra ? " mp-task--extra" : "") + '"' +
                (isExtra && canWrite ? ' data-untrain="' + st + '" title="Extra trained task — click to remove"' : "") +
                ">" + st + (isExtra ? " \u00d7" : "") + "</span>";
            }).join(" ")
          : '<span class="muted">\u2014</span>';
        // ASSIGNED WORK — shown separately from "can do". When the person is
        // absent, each task gets an inline "Reassign" button.
        var working = p.working || [];
        var workHtml = working.length
          ? working.map(function (w) {
              return '<span class="mp-work" data-unassign="' + w.order + "|" + w.stage + '">' +
                "<b>" + w.order + "</b> \u00b7 " + w.stage +
                (canWrite ? ' <i class="mp-work__x" title="Remove">\u00d7</i>' : "") + "</span>" +
                (p.absent && canWrite ? ' <button class="mp-reassign-btn" data-reassign="' +
                  w.order + "|" + w.stage + '">\u21ba Reassign</button>' : "");
            }).join(" ")
          : '<span class="mp-work-none">no work assigned</span>';
        var absentTag = p.absent
          ? '<span class="mp-person__out" title="' + (p.absentNote || "Unavailable") + '">OUT</span>' : "";
        card.innerHTML =
          '<span class="mp-person__init">' + p.initials + "</span>" +
          '<div class="mp-person__mid"><div class="mp-person__name">' + p.name + " " + absentTag +
            (working.length ? ' <span class="mp-person__busy">' + working.length + " task" + (working.length > 1 ? "s" : "") + "</span>" : "") + "</div>" +
          '<div class="mp-person__skill">' + (p.skill || "") + "</div>" +
          '<div class="mp-work-row"><span class="mp-work-lbl">working on:</span> ' + workHtml + "</div>" +
          '<div class="mp-person__cando" title="Trained tasks"><span class="mp-cando-lbl">can do:</span> ' +
            canDoHtml +
            (canWrite ? ' <button class="mp-train-btn" data-train="' + p.id + '">+ train</button>' : "") +
            (canWrite ? ' <button class="mp-out-btn" data-absent="' + p.id + '|' + (p.absent ? "0" : "1") + '">' +
              (p.absent ? "Mark present" : "Mark out") + "</button>" : "") +
          "</div></div>";
        if (canWrite) {
          var trainBtn = card.querySelector("[data-train]");
          if (trainBtn) trainBtn.addEventListener("click", function () {
            showTrainForm(card, p, d.allStages || []);
          });
          card.querySelectorAll("[data-untrain]").forEach(function (chip) {
            chip.addEventListener("click", function () {
              untrainTask(p.id, chip.getAttribute("data-untrain"));
            });
          });
          card.querySelectorAll("[data-unassign]").forEach(function (chip) {
            chip.addEventListener("click", function () {
              var parts = chip.getAttribute("data-unassign").split("|");
              unassignWork(p.userId || p.id, parts[0], parts[1]);
            });
          });
          var outBtn = card.querySelector("[data-absent]");
          if (outBtn) outBtn.addEventListener("click", function () {
            var parts = outBtn.getAttribute("data-absent").split("|");
            markAbsent(parts[0], parts[1] === "1");
          });
          card.querySelectorAll("[data-reassign]").forEach(function (btn) {
            btn.addEventListener("click", function () {
              var parts = btn.getAttribute("data-reassign").split("|");
              reassignTask(p, parts[0], parts[1], btn);
            });
          });
        }
        grid.appendChild(card);
      });
      row.appendChild(grid);
      shiftWrap.appendChild(row);
    });
    mount.appendChild(shiftWrap);

    // per-shift inline assign: pick an operator on THIS shift + tasks they can do
    function showShiftAssign(shiftRow, shift, people) {
      if (shiftRow.querySelector(".mp-shiftassign")) { shiftRow.querySelector(".mp-shiftassign").remove(); return; }
      var eligiblePeople = people.filter(function (p) { return (p.canDo || []).length && !p.absent; });
      var work = d.assignableWork || [];
      if (!eligiblePeople.length || !work.length) {
        var none = el("div", "mp-shiftassign", '<span class="muted sm">No assignable work or trained operators on this shift.</span>');
        shiftRow.insertBefore(none, shiftRow.querySelector(".mp-people"));
        return;
      }
      var box = el("div", "mp-shiftassign");
      var empOpts = eligiblePeople.map(function (p) {
        return '<option value="' + p.id + '">' + p.name + " (" + (p.skill || "") + ")</option>";
      }).join("");
      box.innerHTML =
        '<div class="mp-shiftassign__title">Assign work \u2014 Shift ' + shift + "</div>" +
        '<label class="mp-assign2__op">Operator<select data-sa="emp">' + empOpts + "</select></label>" +
        '<div class="mp-assign2__tasks" data-sa="tasks"></div>' +
        '<div class="mp-assign2__foot"><button class="btn btn--primary btn--sm" data-sa="go">Assign &amp; notify</button>' +
        '<button class="btn btn--sm" data-sa="cancel">Cancel</button><span class="mp-assign__msg" data-sa="msg"></span></div>';
      shiftRow.insertBefore(box, shiftRow.querySelector(".mp-people"));
      var empSel = box.querySelector("[data-sa='emp']");
      var taskWrap = box.querySelector("[data-sa='tasks']");
      function refresh() {
        var emp = eligiblePeople.filter(function (p) { return p.id == empSel.value; })[0];
        var can = (emp && emp.canDo) || [];
        var elig = work.filter(function (w) { return can.indexOf(w.stage) > -1; });
        taskWrap.innerHTML = elig.length ? elig.map(function (w) {
          return '<label class="mp-task-check"><input type="checkbox" value="' + work.indexOf(w) + '">' +
            "<span><b>" + w.order + "</b> \u00b7 " + w.stage + "</span></label>";
        }).join("") : '<p class="mp-err">' + (emp ? emp.name : "This operator") + " isn't trained for any pending task.</p>";
      }
      empSel.addEventListener("change", refresh); refresh();
      box.querySelector("[data-sa='cancel']").addEventListener("click", function () { box.remove(); });
      box.querySelector("[data-sa='go']").addEventListener("click", async function () {
        var emp = eligiblePeople.filter(function (p) { return p.id == empSel.value; })[0];
        var picks = Array.prototype.slice.call(taskWrap.querySelectorAll("input:checked"))
          .map(function (cb) { return work[parseInt(cb.value, 10)]; });
        var msg = box.querySelector("[data-sa='msg']");
        if (!emp || !picks.length) { msg.innerHTML = '<span class="mp-err">Pick an operator and at least one task.</span>'; return; }
        var by = (store && store.user && store.user.name) || "Department Head";
        var ok = 0;
        for (var i = 0; i < picks.length; i++) {
          try {
            var r = await fetch("/api/assign", { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ order: picks[i].order, stage: picks[i].stage, employeeId: parseInt(emp.userId || emp.id, 10), by: by }) }).then(function (x) { return x.json(); });
            if (r.ok) ok++;
          } catch (e) {}
        }
        if (ok) { render(); } else { msg.innerHTML = '<span class="mp-err">Could not assign.</span>'; }
      });
    }

    async function unassignWork(empId, order, stage) {
      var by = (store && store.user && store.user.name) || "Department Head";
      try {
        await fetch("/api/unassign", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order: order, stage: stage, employeeId: empId, by: by }) });
        render();
      } catch (e) {}
    }

    // #2a: mark an operator out / back on the floor
    async function markAbsent(operatorId, absent) {
      var a = mpActor();
      var note = absent ? (prompt("Reason (optional):", "Unavailable today") || "Unavailable") : "";
      try {
        await fetch("/api/manpower/absence", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ operatorId: operatorId, absent: absent, note: note, by: a.by, role: a.role }) });
        render();
      } catch (e) { alert("Could not update availability."); }
    }

    // #2b: reassign one task of an absent person to a trained, available operator
    async function reassignTask(person, order, stage, btn) {
      var a = mpActor();
      // fetch candidates trained for this stage and not absent
      var cands = [];
      try {
        var r = await fetch("/api/manpower/reassign-candidates/" +
          encodeURIComponent(order) + "/" + encodeURIComponent(stage)).then(function (x) { return x.json(); });
        cands = (r && r.candidates) || [];
      } catch (e) {}
      cands = cands.filter(function (cc) { return cc.operatorId !== person.id; });
      if (!cands.length) {
        alert("No trained, available operator to take " + order + " \u00b7 " + stage +
              ". Train someone for " + stage + " first.");
        return;
      }
      // simple inline picker
      var existing = btn.parentNode.querySelector(".mp-reassign-pick");
      if (existing) { existing.remove(); return; }
      var pick = el("span", "mp-reassign-pick");
      pick.innerHTML = '<select data-rp="who">' +
        cands.map(function (cc) { return '<option value="' + cc.id + '">' + cc.name + " (" + (cc.skill || "") + ")</option>"; }).join("") +
        '</select><button class="btn btn--xs btn--primary" data-rp="go">Give</button>' +
        '<button class="btn btn--xs" data-rp="x">\u00d7</button>';
      btn.parentNode.insertBefore(pick, btn.nextSibling);
      pick.querySelector("[data-rp='x']").addEventListener("click", function () { pick.remove(); });
      pick.querySelector("[data-rp='go']").addEventListener("click", async function () {
        var toId = parseInt(pick.querySelector("[data-rp='who']").value, 10);
        try {
          // assign to the new operator, then remove from the absent one
          var res = await fetch("/api/assign", { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order: order, stage: stage, employeeId: toId, by: a.by }) }).then(function (x) { return x.json(); });
          if (!res.ok) { alert(res.error || "Could not reassign."); return; }
          await fetch("/api/unassign", { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order: order, stage: stage, employeeId: person.userId || person.id, by: a.by }) });
          render();
        } catch (e) { alert("Could not reassign."); }
      });
    }

    // inline add-employee form (one person can be on two shifts)
    function showAddForm(shiftRow, shift) {
      if (shiftRow.querySelector(".mp-addform")) return;
      var existingNames = [];
      ["A", "B", "C"].forEach(function (s) {
        (d.shifts[s] || []).forEach(function (p) {
          if (existingNames.indexOf(p.name) < 0) existingNames.push(p.name);
        });
      });
      var datalist = existingNames.map(function (n) { return '<option value="' + n + '">'; }).join("");
      var skills = ["Assembly", "Calibration tech", "Burn-in", "QC inspector", "Packer"];
      var skillOpts = skills.map(function (s) { return '<option value="' + s + '">' + s + "</option>"; }).join("");
      var f = el("div", "mp-addform");
      f.innerHTML =
        '<input list="mp-names" data-a="name" placeholder="Employee name (or pick existing for a 2nd shift)" />' +
        '<datalist id="mp-names">' + datalist + "</datalist>" +
        '<select data-a="skill">' + skillOpts + "</select>" +
        '<button class="btn btn--primary" data-a="save">Add to Shift ' + shift + "</button>" +
        '<button class="btn" data-a="cancel">Cancel</button>' +
        '<span class="mp-addmsg"></span>';
      shiftRow.insertBefore(f, shiftRow.querySelector(".mp-people"));
      f.querySelector("[data-a='cancel']").addEventListener("click", function () { f.remove(); });
      f.querySelector("[data-a='save']").addEventListener("click", async function () {
        var name = f.querySelector("[data-a='name']").value.trim();
        var skill = f.querySelector("[data-a='skill']").value;
        if (!name) { f.querySelector(".mp-addmsg").textContent = "Enter a name"; return; }
        try {
          var res = await fetch("/api/manpower/add", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name, shift: shift, skill: skill })
          }).then(function (r) { return r.json(); });
          if (res.ok) { render(); }  // re-render with the new person
          else f.querySelector(".mp-addmsg").textContent = res.error || "Could not add";
        } catch (e) { f.querySelector(".mp-addmsg").textContent = "Could not add"; }
      });
    }

    // skills / certification matrix
    var skillCard = el("div", "card");
    skillCard.appendChild(el("h2", "card__label", "SKILLS COVERAGE · WHO CAN RUN EACH STAGE"));
    var table = el("div", "mp-matrix");
    d.skillMatrix.forEach(function (m) {
      var r = el("div", "mp-matrix__row" + (m.thin ? " is-thin" : ""));
      r.innerHTML =
        '<div class="mp-matrix__stage">' + m.stage +
          (m.thin ? ' <span class="mp-thin">thin</span>' : '') + "</div>" +
        '<div class="mp-matrix__ops">' +
          (m.operators.length ? m.operators.join(", ") : "\u2014 no one certified") +
          "</div>" +
        '<div class="mp-matrix__count">' + m.count + "</div>";
      table.appendChild(r);
    });
    skillCard.appendChild(table);
    mount.appendChild(skillCard);

    // capacity: active orders per line
    var capCard = el("div", "card");
    capCard.appendChild(el("h2", "card__label", "LINE CAPACITY · ACTIVE ORDER LOAD"));
    var caps = el("div", "mp-caps");
    var maxOrders = Math.max.apply(null, d.capacity.map(function (c) { return c.activeOrders; }).concat([1]));
    d.capacity.forEach(function (c) {
      var pct = Math.round(c.activeOrders / maxOrders * 100);
      var bar = el("div", "mp-cap");
      bar.innerHTML =
        '<div class="mp-cap__label">' + c.line + " <span>· " + c.code + "</span></div>" +
        '<div class="mp-cap__track"><i style="width:' + pct + '%"></i></div>' +
        '<div class="mp-cap__val">' + c.activeOrders + " orders</div>";
      caps.appendChild(bar);
    });
    capCard.appendChild(caps);
    mount.appendChild(capCard);

    // (The standalone assign-work card that used to sit here has been removed —
    // work is assigned per shift via the "+ Assign work" button in each shift
    // section, which is the natural place for it.)
    window._mpAssignCard = null;

    // NOTE: the manpower optimisation suggestions have moved to the Dashboard
    // (Optimization Suggested → Manpower Optimization). The Manpower tab no
    // longer shows its own suggestion card.
  }

  function mpActor() {
    var u = (window.MStore && window.MStore.user) || {};
    return { by: u.name || "Department Head", role: u.role || "Department Head" };
  }

  function showTrainForm(card, person, allStages) {
    var cando = card.querySelector(".mp-person__cando");
    if (card.querySelector(".mp-trainform")) { card.querySelector(".mp-trainform").remove(); return; }
    var already = person.canDo || [];
    var options = allStages.filter(function (s) { return already.indexOf(s) < 0; });
    if (!options.length) return;
    var f = el("div", "mp-trainform");
    f.innerHTML =
      '<select data-t="stage">' +
        options.map(function (s) { return '<option value="' + s + '">' + s + "</option>"; }).join("") +
      "</select>" +
      '<button class="btn btn--primary btn--xs" data-t="save">Mark trained</button>' +
      '<button class="btn btn--xs" data-t="cancel">Cancel</button>' +
      '<span class="mp-trainmsg"></span>';
    cando.appendChild(f);
    f.querySelector("[data-t='cancel']").addEventListener("click", function () { f.remove(); });
    f.querySelector("[data-t='save']").addEventListener("click", async function () {
      var stage = f.querySelector("[data-t='stage']").value;
      var a = mpActor();
      try {
        var res = await fetch("/api/manpower/train", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ operatorId: person.id, stage: stage, by: a.by, role: a.role })
        });
        var out = await res.json();
        if (!res.ok) { f.querySelector(".mp-trainmsg").textContent = out.error || "Could not save"; return; }
        render();  // reflect the new trained task everywhere
      } catch (e) { f.querySelector(".mp-trainmsg").textContent = "Could not save"; }
    });
  }

  async function untrainTask(operatorId, stage) {
    var a = mpActor();
    try {
      await fetch("/api/manpower/untrain", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operatorId: operatorId, stage: stage, by: a.by, role: a.role })
      });
      render();
    } catch (e) {}
  }

  function statCard(n, label) {
    return '<div class="mp-stat"><div class="mp-stat__n">' + n +
      '</div><div class="mp-stat__l">' + label + "</div></div>";
  }

  // Render AFTER main.js has loaded the store (so canWrite/user are known).
  // main.js calls MPage.render(store) once the store is ready; we also cover
  // the case where the store is already up by the time this script parses.
  window.MPage = { render: function () { render(); } };
  if (window.MStore && window.MStore.user) render();
})(window, document);
