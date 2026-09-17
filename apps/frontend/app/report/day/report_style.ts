export const REPORT_CSS = `
        .rdoc { color-scheme: light; font: 14px/1.45 "Segoe UI", system-ui, sans-serif;
                color:#1c2430; margin:32px auto; max-width:780px; background:#fff; padding:0 16px; }
        .rdoc h1 { font-size:20px; margin:0 0 2px; }
        .rdoc .sub { color:#6b7684; font-size:12.5px; margin-bottom:18px; }
        .rdoc .grid { display:flex; gap:12px; margin-bottom:22px; flex-wrap:wrap; }
        .rdoc .kpi { flex:1 1 140px; border:1px solid #e3e8ef; border-radius:10px; padding:12px 14px; }
        .rdoc .kpi b { display:block; font-size:22px; font-variant-numeric:tabular-nums; }
        .rdoc .kpi span { color:#6b7684; font-size:12px; }
        .rdoc h2 { font-size:14px; margin:20px 0 8px; color:#39424e; }
        .rdoc table { width:100%; border-collapse:collapse; font-size:12.5px; }
        .rdoc th { text-align:left; color:#6b7684; font-weight:600; border-bottom:1px solid #dfe5ec; padding:5px 8px; }
        .rdoc td { border-bottom:1px solid #eef1f5; padding:5px 8px; vertical-align:top; }
        .rdoc td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
        .rdoc .ok { color:#0e7a3d; } .rdoc .no { color:#b3261e; }
        .rdoc .note { color:#6b7684; font-size:12px; margin-top:20px; }
        .rdoc .cour { display:inline-block; background:#f1f4f9; border-radius:12px; padding:2px 10px;
                      margin:0 6px 6px 0; font-size:12.5px; }
        .rdoc .rbtn { display:inline-block; margin:0 0 14px; padding:7px 14px; border-radius:8px;
                      border:1px solid #d5dae3; background:#fff; font:600 13px "Segoe UI",sans-serif;
                      cursor:pointer; color:#1c2430; }
        .rdoc .rdoc-err { color:#b3261e; margin-top:40px; }
        @media print { .rdoc { margin:0; } .rdoc .kpi { break-inside:avoid; } .rdoc tr { break-inside:avoid; } .rdoc .rbtn { display:none; } }
      `;
