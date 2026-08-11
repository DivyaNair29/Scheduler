/* ============================================================================
   data.js — MOCK DATA ONLY.

   Every value a Flask backend will eventually supply lives here and nowhere
   else. Delete this file (and its <script> tag) once the server populates the
   same data-* hooks, and the pages keep working.

   Exposed as window.MERIDIAN_DATA so plain scripts and modules can both read it.
   ==========================================================================*/
window.MERIDIAN_DATA = {

  session: { userId: 2 },

  users: [
    { id: 1, name: "J. Reyes",  role: "Employee",        short: "Employee" },
    { id: 2, name: "M. Okafor", role: "Department Head", short: "Dept Head" },
    { id: 3, name: "S. Kane",   role: "Admin",           short: "Admin" }
  ],

  brand: { name: "AND SCHEDULER", tagline: "Meridian Instruments", logo: "img/and-logo.png" },

  /* Sidebar destinations. `write: true` = Dept Head / Admin only. */
  nav: [
    { group: null, items: [
      { id: "dashboard", label: "Dashboard",          href: "index.html" },
      { id: "board",     label: "Schedule Board",     href: "board.html",    write: true, badgeKey: "mfgOrders" },
      { id: "orders",    label: "Orders",             href: "orders.html",   badgeKey: "orders" },
      { id: "quality",   label: "Quality & Dispatch", href: "quality.html",  employeeLabel: "Stage Confirmations", badgeKey: "approvals" }
    ]}
  ],

  phases: [
    { key: "intake",   label: "Front end & planning", color: "var(--phase-intake)" },
    { key: "mfg",      label: "Manufacturing",        color: "var(--phase-mfg)" },
    { key: "quality",  label: "Quality",              color: "var(--phase-quality)" },
    { key: "dispatch", label: "Dispatch",             color: "var(--phase-dispatch)" },
    { key: "closed",   label: "Closed / shipped",     color: "var(--phase-closed)" }
  ],

  lines: [
    { code: "PT", name: "Line 1", family: "Pressure",       color: "var(--line-pt)" },
    { code: "TT", name: "Line 2", family: "Temperature",    color: "var(--line-tt)" },
    { code: "DP", name: "Line 3", family: "Diff. Pressure", color: "var(--line-dp)" },
    { code: "LT", name: "Line 4", family: "Level",          color: "var(--line-lt)" }
  ],

  /* Downstream stages shown on the board after the four lines. */
  downstream: [
    { name: "Final QC",            match: ["Final QC"] },
    { name: "Documentation",       match: ["Docs", "Documentation"] },
    { name: "Packing",             match: ["Packing"] },
    { name: "Shipping & Dispatch", match: ["Shipping", "Dispatch", "Closure"] }
  ],

  workCentres: [
    { name: "Burn-in Chamber B1",   sub: "Burn-in work-centre",     state: "available" },
    { name: "Burn-in Chamber B2",   sub: "Burn-in work-centre",     state: "down",
      label: "DOWN — B2 FAULT", note: "Unavailable till 24 Jul", startMin: 1060, durationMin: 380 },
    { name: "Calibration Bench C1", sub: "Calibration work-centre", state: "available" },
    { name: "Calibration Bench C2", sub: "Calibration work-centre", state: "maintenance",
      label: "MAINTENANCE", note: "Planned outage 02:00–06:00", startMin: 1200, durationMin: 240 },
    { name: "Calibration Bench C3", sub: "Calibration work-centre", state: "available" }
  ],

  stages: ["Kitting", "Assembly", "Calibration", "Burn-in", "Final QC", "Packing", "Dispatch"],
  constraintTypes: ["Material shortage", "Machine issue", "Manpower gap", "Quality hold", "Tooling"],
  products: ["PT-3051", "TT-4400", "DPT-7100", "LT-6200"],
  customers: ["Northwind Energy", "Kova Automotive", "Medisys Devices", "Brightline Utilities"],

  orders: [
    { code: "SO-1042", product: "DPT-7100", family: "Diff. pressure", lineCode: "DP", line: "Line 3", qty: 40,  phase: "mfg",     stage: "Calibration", status: "RUNNING",  due: "29 Jul", source: "erp",  startMin: 240,  durationMin: 300 },
    { code: "SO-1044", product: "PT-3051",  family: "Pressure",       lineCode: "PT", line: "Line 1", qty: 25,  phase: "mfg",     stage: "Burn-in",     status: "RUNNING",  due: "30 Jul", source: "erp",  startMin: 420,  durationMin: 360, rush: true },
    { code: "SO-1049", product: "PT-3051",  family: "Pressure",       lineCode: "PT", line: "Line 1", qty: 100, phase: "intake",  stage: "Kitting",     status: "ON TRACK", due: "1 Aug",  source: "erp",  startMin: 60,   durationMin: 180 },
    { code: "SO-1054", product: "TT-4400",  family: "Temperature",    lineCode: "TT", line: "Line 2", qty: 60,  phase: "mfg",     stage: "Assembly",    status: "RUNNING",  due: "2 Aug",  source: "dept", startMin: 300,  durationMin: 260 },
    { code: "SO-1058", product: "LT-6200",  family: "Level",          lineCode: "LT", line: "Line 4", qty: 30,  phase: "mfg",     stage: "Burn-in",     status: "AT RISK",  due: "31 Jul", source: "ai",   startMin: 640,  durationMin: 380 },
    { code: "SO-1031", product: "TT-4400",  family: "Temperature",    lineCode: "TT", line: "Line 2", qty: 80,  phase: "quality", stage: "Packing",     status: "ON TRACK", due: "27 Jul", source: "erp",  startMin: 900,  durationMin: 200 },
    { code: "SO-1028", product: "PT-3051",  family: "Pressure",       lineCode: "PT", line: "Line 1", qty: 50,  phase: "quality", stage: "Dispatch",    status: "ON TRACK", due: "26 Jul", source: "erp",  startMin: 1100, durationMin: 160 },
    { code: "SO-1061", product: "DPT-7100", family: "Diff. pressure", lineCode: "DP", line: "Line 3", qty: 20,  phase: "intake",  stage: "Engineering", status: "ON TRACK", due: "8 Aug",  source: "erp",  startMin: 0,    durationMin: 120 },
    { code: "SO-1015", product: "LT-6200",  family: "Level",          lineCode: "LT", line: "Line 4", qty: 45,  phase: "closed",  stage: "Closure",     status: "SHIPPED",  due: "18 Jul", source: "erp",  startMin: 0,    durationMin: 0 }
  ],

  /* Orders that arrived but are not yet released to a line. */
  intakeQueue: [
    { code: "SO-1063", customer: "Northwind Energy", product: "PT-3051",  qty: 75, received: "27 Jul 08:40", stage: "Credit check", status: "ON TRACK" },
    { code: "SO-1064", customer: "Medisys Devices",  product: "TT-4400",  qty: 30, received: "27 Jul 10:15", stage: "Engineering",  status: "ON TRACK" },
    { code: "SO-1065", customer: "Kova Automotive",  product: "DPT-7100", qty: 12, received: "27 Jul 11:02", stage: "Quote",        status: "AT RISK" }
  ],

  constraints: [
    { code: "C-201", raisedBy: "J. Reyes", role: "Employee", order: "SO-1044", stage: "Calibration",
      type: "Material shortage",
      note: "Only one reference standard available at the benches — the second bench is idle until the spare is released from stores.",
      status: "pending", revision: 1, feedback: "", ts: "26 Jul 09:12" },
    { code: "C-202", raisedBy: "J. Reyes", role: "Employee", order: "SO-1058", stage: "Burn-in",
      type: "Machine issue",
      note: "Chamber B2 thermal controller trips intermittently before the soak completes.",
      status: "applied", revision: 2,
      feedback: "Keep SO-1044 on Line 3 — do not push its due date.", ts: "26 Jul 07:48" }
  ],

  confirmations: [
    { order: "SO-1042", stage: "Calibration", item: "3-point calibration recorded", operator: "G. Petrov", ts: "25 Jul 22:10" }
  ],

  dispatches: [
    { time: "14:00", item: "SO-1028 · PT-3051 ×50", customer: "Northwind Energy", status: "ON TRACK" },
    { time: "17:30", item: "SO-1031 · TT-4400 ×80", customer: "Brightline Utilities", status: "ON TRACK" },
    { time: "19:00", item: "SO-1015 · LT-6200 ×45", customer: "Kova Automotive", status: "SHIPPED" }
  ],

  checklists: {
    "Kitting":     ["BOM kit verified against pick list", "Serial tags issued", "Shortages flagged to planner"],
    "Assembly":    ["Sub-assembly torque check logged", "Housing seal fitted", "Wiring continuity pass"],
    "Calibration": ["Bench reference standard verified", "3-point calibration recorded", "Cal certificate drafted"],
    "Burn-in":     ["Chamber loaded & profile set", "Soak hours logged", "Post-soak drift within limit"],
    "Final QC":    ["Visual & dimensional inspection", "Functional test pass", "Open NCRs closed"],
    "Packing":     ["Anti-static bagging complete", "Carton labels applied", "Document pack enclosed"],
    "Dispatch":    ["Ship-ready confirmed", "Carrier booked", "POD reference captured"]
  },

  boardInsights: [
    { kind: "USAGE", ref: "Line 1 · Line 2",
      title: "Line 1 under-loaded while Line 2 is saturated",
      detail: "Line 1 (Pressure) is running ≈62% this shift; Line 2 has three orders stacked at Assembly. Shift SO-1054 electronics onto Line 1 to balance the load.",
      gain: "balances load · −40 min queue" },
    { kind: "MOVE", ref: "SO-1058",
      title: "Move SO-1058 to Line 3 burn-in",
      detail: "Burn-in Chamber B2 is down until 24 Jul. A 02:00 slot is open on Line 3 — rerouting SO-1058 there holds its 31 Jul promise instead of slipping to 9 Aug.",
      gain: "holds 31 Jul due date" },
    { kind: "SPLIT", ref: "SO-1049",
      title: "Split SO-1049 (100 pcs) to protect the due date",
      detail: "SO-1049 (PT-3051 ×100, due 1 Aug) can’t clear one run in time. Split 60/40 across Line 1 and Line 3 kitting to ship the first 60 pcs on schedule.",
      gain: "60 pcs ship 1 Aug" }
  ],

  chat: [
    { from: "ai", author: "Scheduler AI", ts: "09:12",
      text: "C-201 raised on SO-1044 at Calibration. Second bench is idle — approving the constraint will generate a reroute proposal." },
    { from: "them", author: "J. Reyes", ts: "09:14",
      text: "Stores says the spare standard lands after lunch." },
    { from: "ai", author: "Scheduler AI", ts: "09:15",
      text: "Noted. Holding the proposal until you decide — the floor plan is unchanged." }
  ],

  /* Status → tone + label, used by the approval chain on several pages. */
  constraintStates: {
    pending:  { tone: "warn", head: "AWAITING YOUR APPROVAL",                 mine: "Awaiting production head" },
    approved: { tone: "info", head: "CONSTRAINT APPROVED · SCHEDULE PENDING", mine: "Approved · schedule under review" },
    applied:  { tone: "ok",   head: "APPLIED TO LIVE FLOOR",                  mine: "Applied to live floor" },
    rejected: { tone: "bad",  head: "REJECTED",                               mine: "Rejected" }
  }
};
