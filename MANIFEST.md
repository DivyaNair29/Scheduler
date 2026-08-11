# Changed files — Reports: month-by-month growth table + Download PDF

The Reports tab has the month-by-month growth table and a Download PDF button
that exports that table (plus the stat cards) as a PDF.

## What's there
- Four stat cards: Orders this month, On-time delivery, Constraints raised,
  Avg cycle time (with vs-previous deltas).
- MONTH-BY-MONTH · GROWTH table: Month / Orders / Shipped / Constraints /
  On-time / Avg cycle / Growth, latest row highlighted. (6 months of data from
  the existing /api/reports.)
- **Download PDF** button -> opens a clean print-formatted view (header, the four
  stat cards, and the full month-by-month table) and triggers the browser's
  print-to-PDF, so the user saves it as a PDF. No external library needed.
- The PDF document is titled "Meridian_Monthly_Report_<Month_Year>" so the saved
  file gets a clean, dated default filename.

## Note
This feature was already built in the current code; the version prompting this
request predates it. This zip carries the current reports files so dropping them
in makes the table + PDF appear.

## Files
- site/js/page-reports.js  (table render + PDF export, dated filename)
- site/reports.html         (mount + script)
- site/css/pages.css         (report table styles)

## After dropping in
Re-seed not required. Hard-refresh the Reports tab -> the month-by-month growth
table shows; click Download PDF and save via your browser's print-to-PDF dialog.

## On PDF generation (honest)
This uses the browser's built-in print-to-PDF (Save as PDF), which reliably
produces a proper PDF everywhere with no server dependency. If you later need a
server-generated PDF (identical output, no print dialog, e.g. for automated /
emailed reports), that's a backend addition with a library like ReportLab —
worth it only for automation, not for a user clicking Download.
