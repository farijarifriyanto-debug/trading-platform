from fastapi.responses import HTMLResponse


def dashboard_response() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Platform</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color-scheme:dark;background:#0b0d10;color:#eef2f6}
*{box-sizing:border-box}body{margin:0;background:#0b0d10}main{max-width:1180px;margin:auto;padding:32px 20px 64px}
header{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:24px}h1{margin:0;font-size:30px}.muted{color:#9ca8b5}
.badge{border:1px solid #31404d;border-radius:999px;padding:7px 11px;font-size:12px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.card{background:#12161b;border:1px solid #252d35;border-radius:14px;padding:16px}.metric{font-size:26px;font-weight:700;margin-top:8px}.wide{grid-column:span 2}
h2{font-size:16px;margin:0 0 12px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px 7px;border-bottom:1px solid #252d35}
th{color:#9ca8b5;font-weight:500}pre{white-space:pre-wrap;word-break:break-word;background:#0b0d10;padding:12px;border-radius:9px;max-height:260px;overflow:auto}
section{margin-top:12px}.ok{color:#8ee6a5}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}.wide{grid-column:span 2}}
@media(max-width:520px){.grid{grid-template-columns:1fr}.wide{grid-column:span 1}header{align-items:start;flex-direction:column}}
</style></head>
<body><main>
<header><div><div class="muted">Research, simulation & paper execution control plane</div><h1>Trading Platform</h1></div><div class="badge"><span class="ok">●</span> PAPER ONLY · LIVE OFF</div></header>
<div class="grid">
<div class="card"><div class="muted">Datasets</div><div id="datasetCount" class="metric">—</div></div>
<div class="card"><div class="muted">Paper cash</div><div id="paperCash" class="metric">—</div></div>
<div class="card"><div class="muted">Open positions</div><div id="positions" class="metric">—</div></div>
<div class="card"><div class="muted">Experiments</div><div id="experiments" class="metric">—</div></div>
<section class="card wide"><h2>Experiment comparison</h2><div id="comparison">Loading…</div></section>
<section class="card wide"><h2>Simulation workers</h2><pre id="workers">Loading…</pre></section>
<section class="card wide"><h2>Durable jobs</h2><div id="jobs">Loading…</div></section>
<section class="card wide"><h2>Readiness</h2><pre id="readiness">Loading…</pre></section>
<section class="card wide"><h2>Historical datasets</h2><div id="datasets">Loading…</div></section>
<section class="card wide"><h2>Simulation boundaries</h2><div id="engines">Loading…</div></section>
<section class="card wide"><h2>Paper portfolio</h2><pre id="portfolio">Loading…</pre></section>
<section class="card wide"><h2>Recent audit</h2><pre id="audit">Loading…</pre></section>
</div></main>
<script>
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function j(url){const r=await fetch(url);if(!r.ok)throw new Error(url+" "+r.status);return r.json()}
function table(rows,cols){if(!rows.length)return '<span class="muted">No records yet</span>';return '<table><thead><tr>'+cols.map(c=>'<th>'+esc(c[0])+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+cols.map(c=>'<td>'+esc(row[c[1]]??"")+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
async function refresh(){try{
 const [m,d,e,p,a,c,w,q,r]=await Promise.all([j('/metrics'),j('/datasets'),j('/simulations/engines'),j('/paper/portfolio'),j('/paper/audit?limit=10'),j('/experiments/compare'),j('/workers/health'),j('/jobs?limit=10'),j('/ready')]);
 datasetCount.textContent=m.dataset_count;paperCash.textContent=Number(m.paper_cash).toLocaleString(undefined,{maximumFractionDigits:2});
 positions.textContent=m.open_positions;experiments.textContent=m.completed_experiments+"/"+m.experiment_count;
 comparison.innerHTML=table(c,[['Engine','engine'],['Strategy','strategy'],['Start','start_cash'],['End','end_equity'],['Return','return_fraction'],['Trades','trades']]);
 workers.textContent=JSON.stringify(w,null,2);
 jobs.innerHTML=table(q,[['Job','job_id'],['Kind','kind'],['Status','status'],['Attempts','attempts']]);
 readiness.textContent=JSON.stringify(r,null,2);
 datasets.innerHTML=table(d,[['ID','dataset_id'],['Exchange','exchange'],['Symbol','symbol'],['TF','timeframe'],['Rows','count']]);
 engines.innerHTML=table(e,[['Engine','engine'],['Mode','mode'],['Execution','execution_enabled']]);
 portfolio.textContent=JSON.stringify(p,null,2);audit.textContent=JSON.stringify(a,null,2);
}catch(err){document.querySelectorAll('pre').forEach(x=>x.textContent=String(err))}}
refresh();setInterval(refresh,10000);
</script></body></html>"""
    )
