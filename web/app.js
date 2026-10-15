/* Crash Triage — static front end. Reads data.json built by pipeline/build_site.py. */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const FACT = [["safety","Safety"],["runway","Cash runway"],["value","Value vs cash"],["insider","Insider buying"],["programs","Other programs"]];
const ICON = {g:"✓", r:"✕", n:"–", p:""};
const VERDICT = {
  overdone:["p-good","▲","Potentially Oversold","var(--g-line)"],
  close:["p-warn","●","Too Close To Call","var(--w-line)"],
  justified:["p-crit","■","Justified","var(--r-line)"],
  pending:["p-pend","","Not scored yet","var(--axis)"]};
const pill = v => { const [c,i,t] = VERDICT[v] || VERDICT.pending; return `<span class="pill ${c}">${i?`<i>${i}</i>`:""}${t}</span>`; };
const dur = c => `${c.days} day${c.days === 1 ? "" : "s"}`;
const pct = v => v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(0)}%`;
const OUTCOME = {Winner:"p-good", Recovery:"p-warn", Failure:"p-crit"};
const BUCKETS = {
  runway:["Under 6 mo","6–12 mo","12–24 mo","24+ mo","N/A"], value:["Below Cash","Above Cash","N/A"],
  insider:["Insider Buy","Insider Sell","N/A"], programs:["1 trial","2–4 trials","5+ trials","N/A"]};
let D = null, SHOW = 60, MODE = "screen";

const tip = $("#tip");
function showTip(e, html){ tip.innerHTML = html; tip.style.opacity = 1;
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = e.clientY - tip.offsetHeight - 12;
  tip.style.left = x + "px"; tip.style.top = (y < 8 ? e.clientY + 18 : y) + "px"; }
function hideTip(){ tip.style.opacity = 0; }

function cell(f){ const [k,v] = f || ["n","N/A"]; return `<td class="f f-${k}"><span class="ic">${ICON[k]||""}</span>${esc(v)}</td>`; }

/* ------------------------------------------------------------------ the two tables
   screen   = crashes less than a year past the trough: the live screener (return to date)
   backtest = a full year since the trough: the source of truth for the backtest (1-year return + outcome tag) */
const rowsFor = mode => D.crashes.filter(c => mode === "backtest" ? c.played : !c.played);
const bucketOf = (c, k) => c.factors?.[k]?.[3] || "N/A";
function viewTable(mode){
  MODE = mode; SHOW = 60;
  const rows = rowsFor(mode), bt = mode === "backtest";
  const years = [...new Set(rows.map(c => c.date.slice(0,4)))].sort().reverse();
  const trig = [...new Set(rows.map(c => c.trigger).filter(Boolean))].sort();
  const opts = k => { const seen = new Set(rows.map(c => bucketOf(c, k)));
    const order = BUCKETS[k] || [...seen].sort((a,b) => (a === "N/A") - (b === "N/A") || a.localeCompare(b));
    return order.filter(o => seen.has(o)); };
  const sel = (id, all, list) => `<select class="hsel" id="${id}"><option value="">${all}</option>${list.map(([v,l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>`;
  $("#app").innerHTML = `
  <div class="toprow"><div><h1>${bt ? "Backtest" : "Screener"}</h1><div class="muted">${bt
     ? `${rows.length} crashes with a full year since the trough · return if bought at the trough and held one year · Winner ≥ +30%, Recovery 0 to +30%, Failure below 0%`
     : `${rows.length} crashes in the last year · return if bought at the trough, to the latest close`}</div></div>
   <div class="filters">
    <select id="fy"><option value="">All years</option>${years.map(y=>`<option>${y}</option>`).join("")}</select>
    <select id="ft"><option value="">All triggers</option>${trig.map(t=>`<option>${esc(t)}</option>`).join("")}</select>
    <input id="fq" placeholder="Search company, ticker or drug"></div></div>
  <div class="tablewrap"><table class="crash"><thead>
   <tr><th>Company</th><th>Crash</th><th class="r">Fall</th><th>What happened</th>${FACT.map(f=>`<th class="fh">${f[1]}</th>`).join("")}
    <th>Verdict</th><th class="r">${bt ? "1-year return<br>from trough" : "Return since<br>trough"}</th>${bt ? "<th>Outcome</th>" : ""}</tr>
   <tr class="frow"><th></th><th></th><th></th><th></th>${FACT.map(([k]) => `<th>${sel("f_"+k, "All", opts(k).map(o => [o,o]))}</th>`).join("")}
    <th>${sel("f_verdict", "All", Object.entries(VERDICT).filter(([k]) => rows.some(c => (c.verdict||"pending") === k)).map(([k,v]) => [k, v[2]]))}</th><th></th>
    ${bt ? `<th>${sel("f_outcome", "All", ["Winner","Recovery","Failure"].map(o => [o,o]))}</th>` : ""}</tr>
  </thead><tbody id="rows"></tbody></table></div>
  <div class="legend"><span class="lg f-g"><span class="ic">✓</span>Better than typical</span><span class="lg f-n"><span class="ic">–</span>Neutral</span><span class="lg f-r"><span class="ic">✕</span>Worse than typical</span>
   <span class="muted" id="cnt"></span></div><div class="more" id="more"></div>`;
  document.querySelectorAll("#fy,#ft,#fq,.hsel").forEach(el => el.addEventListener("input", () => { SHOW = 60; fill(); }));
  document.querySelectorAll(".hsel").forEach(el => el.addEventListener("click", e => e.stopPropagation()));
  fill();
}
function fill(){
  const bt = MODE === "backtest", val = id => $("#"+id)?.value || "";
  const y = val("fy"), t = val("ft"), q = val("fq").trim().toLowerCase(), v = val("f_verdict"), o = val("f_outcome");
  const fv = Object.fromEntries(FACT.map(([k]) => [k, val("f_"+k)]));
  const list = rowsFor(MODE).filter(c => (!y || c.date.startsWith(y)) && (!t || c.trigger === t) && (!v || (c.verdict||"pending") === v)
    && (!o || c.outcome === o) && FACT.every(([k]) => !fv[k] || bucketOf(c, k) === fv[k])
    && (!q || `${c.name} ${c.ticker} ${c.summary||""}`.toLowerCase().includes(q)));
  const ret = c => c.ret_trough == null ? `<td class="r muted">N/A</td>` :
    `<td class="r" style="white-space:nowrap"><span class="num" style="color:${c.ret_trough >= 0 ? "var(--good)" : "var(--bad)"}">${pct(c.ret_trough)}</span>
     <div class="muted" style="font-size:11.5px">${c.stopped ? `stopped ${esc(c.stopped)}` : bt ? "after 1 year" : `to ${esc(c.ret_trough_to)}`}</div></td>`;
  $("#rows").innerHTML = list.slice(0, SHOW).map(c => `<tr data-id="${c.id}"><td><b>${esc(c.name)}</b><div class="muted" style="font-size:11.5px">${esc(c.ticker)}</div></td>
    <td style="white-space:nowrap">${esc(c.month)}</td><td class="r" style="white-space:nowrap"><span class="num">${pct(c.move)}</span><div class="muted" style="font-size:11.5px">${dur(c)}</div></td><td class="what">${esc(c.summary || "Reading the company's announcement…")}</td>
    ${FACT.map(f => cell(c.factors?.[f[0]])).join("")}<td>${pill(c.verdict)}</td>${ret(c)}
    ${bt ? `<td>${c.outcome ? `<span class="pill ${OUTCOME[c.outcome]}">${c.outcome}</span>` : ""}</td>` : ""}</tr>`).join("") ||
    `<tr><td colspan="13" class="empty">No crashes match these filters.</td></tr>`;
  $("#cnt").textContent = `Showing ${Math.min(SHOW, list.length)} of ${list.length}.`;
  $("#more").innerHTML = list.length > SHOW ? `<button id="mb">Show more</button>` : "";
  if (list.length > SHOW) $("#mb").onclick = () => { SHOW += 60; fill(); };
  document.querySelectorAll("#rows tr[data-id]").forEach(tr => tr.onclick = () => location.hash = "#/c/" + tr.dataset.id);
}

/* ------------------------------------------------------------------ one crash */
function viewCrash(id){
  const c = D.crashes.find(x => x.id === id);
  if (!c){ $("#app").innerHTML = `<p class="empty">Crash not found. <a href="#/">Back to all crashes</a></p>`; return; }
  const cards = FACT.map(([k, label]) => { const f = c.factors?.[k] || ["n","N/A",""];
    return `<div class="fc f-${f[0]}"><div class="fl">${label}</div><div class="fv"><span class="ic">${ICON[f[0]]||""}</span>${esc(f[1])}</div><div class="fn">${esc(f[2]||"")}</div></div>`; }).join("");
  const quotes = (c.quotes||[]).map(q => `<div class="quote">“${esc(q)}”</div>`).join("");
  const src = (c.sources||[]).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join("");
  const after = c.ret_trough == null ? "" : `<div class="card"><h4>What happened next</h4><div class="tiles">
     <div><b>${pct(c.ret_trough)}</b><span>${c.played ? "1-year return if bought at the trough" : `return if bought at the trough, to ${esc(c.ret_trough_to)}`} (trough ${esc(c.trough_date)}${c.stopped ? `, stopped trading ${esc(c.stopped)}` : ""})</span></div>
     ${c.outcome ? `<div><b><span class="pill ${OUTCOME[c.outcome]}">${c.outcome}</span></b><span>outcome</span></div>` : ""}
     ${c.ret365 == null ? "" : `<div><b>${pct(c.ret365)}</b><span>stock move from day 30 to day 365</span></div>`}
     ${c.xbi365 == null ? "" : `<div><b>${pct(c.xbi365)}</b><span>biotech index, day 30 to day 365</span></div>`}</div></div>`;
  $("#app").innerHTML = `
  <div class="vhead"><div><div class="muted">News day ${esc(c.date)} (${pct(c.news_day)} that day) · <a href="${c.played ? "#/backtest" : "#/"}">‹ ${c.played ? "Backtest" : "Screener"}</a></div>
    <h2>${esc(c.name)} <span class="muted" style="font-size:18px">${esc(c.ticker)}</span><span class="mv">${pct(c.move)}</span><span class="muted" style="font-size:16px;margin-left:8px">in ${dur(c)}</span></h2>
    <div class="what1">${esc(c.summary || "Reading the company's announcement…")}</div></div>
   <div class="vbox">${pill(c.verdict)}${c.score!=null?`<div class="conf">Score ${c.score} / 100</div>`:""}</div></div>
  <div class="fcs">${cards}</div>
  ${quotes ? `<div class="card"><h4>From the company's announcement</h4>${quotes}</div>` : ""}
  ${after}
  ${src ? `<div class="card srcs"><h4>Sources</h4>${src}</div>` : ""}`;
}

/* ------------------------------------------------------------------ outcomes: all crashes, 365 days later, by verdict */
function viewOutcomes(){
  const pts = D.crashes.filter(c => c.played && c.ret365 != null && !c.in_sample);
  const groups = ["overdone","close","justified","pending"].filter(g => pts.some(c => (c.verdict||"pending") === g));
  $("#app").innerHTML = `<div class="toprow"><div><h1>Outcomes</h1><div class="muted">Backtest crashes: the stock move from day 30 (when all the factors are known) to day 365, grouped by the verdict the tool would have given at the time, using only crashes whose outcome was already known. Hover a dot to see the company; click to open it.</div></div></div>
   <div class="card" id="oc">${pts.length ? "" : `<p class="empty">Outcomes appear once crashes have a full year of prices.</p>`}</div>`;
  if (!pts.length) return;
  const W = Math.max(720, $("#oc").clientWidth - 32), rowH = 92, top = 10, left = 170, right = 24, H = top + rowH * groups.length + 48;
  const lo = -100, hi = Math.max(150, Math.min(400, Math.ceil(Math.max(...pts.map(p => p.ret365)) / 50) * 50));
  const x = v => left + (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (W - left - right);
  let s = `<svg width="${W}" height="${H}" role="img" aria-label="365-day outcomes by verdict">`;
  for (let t = lo; t <= hi; t += 50) s += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H-40}" stroke="${t===0?"var(--axis)":"var(--line)"}"/><text x="${x(t)}" y="${H-24}" text-anchor="middle">${t===0?"0%":(t>0?"+":"")+t+"%"}</text>`;
  s += `<text x="${(left+W-right)/2}" y="${H-4}" text-anchor="middle">Stock move from day 30 to day 365 after the crash</text>`;
  groups.forEach((g, i) => {
    const cy = top + rowH * i + rowH / 2, mine = pts.filter(c => (c.verdict||"pending") === g), med = median(mine.map(c => c.ret365));
    s += `<text class="lab" x="0" y="${cy-4}" font-weight="600">${VERDICT[g][2]}</text><text x="0" y="${cy+13}">${mine.length} crashes · median ${pct(med)}</text>`;
    mine.forEach((c, j) => { const jy = cy + (hash(c.id) - .5) * rowH * .62;
      s += `<circle class="pt" data-id="${c.id}" cx="${x(c.ret365)}" cy="${jy}" r="5.5" fill="${VERDICT[g][3]}" fill-opacity=".85" stroke="var(--bg)" stroke-width="1.5" style="cursor:pointer"/>`; });
    s += `<line x1="${x(med)}" x2="${x(med)}" y1="${cy-rowH*.42}" y2="${cy+rowH*.42}" stroke="var(--ink)" stroke-width="2.5"/>`;
  });
  $("#oc").innerHTML = s + "</svg>";
  document.querySelectorAll(".pt").forEach(el => { const c = D.crashes.find(x => x.id === el.dataset.id);
    el.onmousemove = e => showTip(e, `<b>${esc(c.name)} · ${esc(c.month)}</b>${pct(c.move)} fall in ${dur(c)} → ${pct(c.ret365)} by day 365<br><span class="a">Click to open ›</span>`);
    el.onmouseleave = hideTip; el.onclick = () => { hideTip(); location.hash = "#/c/" + c.id; }; });
}

/* ------------------------------------------------------------------ track record */
function viewTrack(){
  const T = D.track || {};
  const series = [["overdone","Potentially Oversold"],["close","Too Close To Call"],["justified","Justified"]].filter(([k]) => T[k]?.path?.length)
    .map(([k,l]) => ({k, l: `${l} · ${T[k].n}`, col: VERDICT[k][3], path: T[k].path}));
  if (T.xbi?.path?.length) series.push({k:"xbi", l:"Biotech index (XBI)", col:"var(--idx)", path:T.xbi.path, dash:true});
  $("#app").innerHTML = `<div class="toprow"><div><h1>Track record</h1><div class="muted">Backtest crashes: median stock move after the crash, by the verdict given at the time, vs. the biotech index over the same dates. Measured from day 30.</div></div></div>
   <div class="card" id="tr">${series.length > 1 ? "" : `<p class="empty">The track record appears once crashes are scored and have a full year of prices.</p>`}</div>`;
  if (series.length < 2) return;
  const W = Math.max(720, $("#tr").clientWidth - 32), H = 420, left = 50, right = 230, top = 14, bot = 44;
  const all = series.flatMap(s => s.path), lo = Math.floor(Math.min(-10, ...all) / 10) * 10, hi = Math.ceil(Math.max(10, ...all) / 10) * 10;
  const n = series[0].path.length, x = i => left + i / (n - 1) * (W - left - right), y = v => top + (hi - v) / (hi - lo) * (H - top - bot);
  let s = `<div class="chartleg">${series.map(r => `<span><i class="ln" style="background:${r.col}"></i>${esc(r.l)}</span>`).join("")}</div><svg width="${W}" height="${H}">`;
  for (let t = lo; t <= hi; t += 10) s += `<line x1="${left}" x2="${W-right}" y1="${y(t)}" y2="${y(t)}" stroke="${t===0?"var(--axis)":"var(--line)"}"/><text x="${left-8}" y="${y(t)+4}" text-anchor="end">${t===0?"0%":(t>0?"+":"")+t+"%"}</text>`;
  [0,90,180,270,365].forEach(d => s += `<text x="${x(Math.round(d/365*(n-1)))}" y="${H-24}" text-anchor="middle">day ${d}</text>`);
  const ends = series.map((r,i) => ({i, y: y(r.path[n-1])})).sort((a,b) => a.y - b.y); let last = -1e9;
  ends.forEach(e => { e.ly = Math.max(e.y, last + 18); last = e.ly; });
  series.forEach((r, i) => { s += `<path d="M${r.path.map((v,j)=>`${x(j)},${y(v)}`).join(" L")}" fill="none" stroke="${r.col}" stroke-width="2.5" ${r.dash?'stroke-dasharray="6 5"':""}/>`;
    const e = ends.find(z => z.i === i); s += `<circle cx="${x(n-1)}" cy="${y(r.path[n-1])}" r="4.5" fill="${r.col}"/><text class="lab" x="${x(n-1)+12}" y="${e.ly+4}"><tspan font-weight="700">${pct(r.path[n-1])}</tspan> ${esc(r.l)}</text>`; });
  s += `<line id="xh" y1="${top}" y2="${H-bot}" stroke="var(--axis)" opacity="0"/><rect x="${left}" y="${top}" width="${W-left-right}" height="${H-top-bot}" fill="transparent" id="hit"/></svg>`;
  $("#tr").innerHTML = s;
  const hit = $("#hit"), xh = $("#xh");
  hit.onmousemove = e => { const b = hit.getBoundingClientRect(), j = Math.round((e.clientX - b.left) / b.width * (n - 1));
    xh.setAttribute("x1", x(j)); xh.setAttribute("x2", x(j)); xh.setAttribute("opacity", 1);
    showTip(e, `<b>Day ${Math.round(j/(n-1)*365)}</b>` + series.map(r => `${esc(r.l.split(" · ")[0])}: ${pct(r.path[j])}`).join("<br>")); };
  hit.onmouseleave = () => { xh.setAttribute("opacity", 0); hideTip(); };
}

function median(a){ if (!a.length) return 0; const s = [...a].sort((x,y)=>x-y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m-1]+s[m]) / 2; }
function hash(s){ let h = 2166136261; for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619); return ((h >>> 0) % 1000) / 1000; }

function route(){
  hideTip();
  const h = location.hash || "#/", nav = h.startsWith("#/outcomes") ? "outcomes" : h.startsWith("#/track") ? "track"
    : h.startsWith("#/backtest") ? "backtest" : "crashes";
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("on", a.dataset.nav === nav));
  if (h.startsWith("#/c/")) viewCrash(decodeURIComponent(h.slice(4)));
  else if (nav === "outcomes") viewOutcomes(); else if (nav === "track") viewTrack();
  else viewTable(nav === "backtest" ? "backtest" : "screen");
  scrollTo(0, 0);
}
fetch("data.json", {cache: "no-store"}).then(r => r.json()).then(d => {
  D = d; D.crashes.sort((a,b) => b.date.localeCompare(a.date));
  $("#foot").textContent = `Updated ${d.updated}. Data: SEC EDGAR, ClinicalTrials.gov, Twelve Data, Tiingo. Not investment advice.`;
  addEventListener("hashchange", route); route();
}).catch(() => $("#app").innerHTML = `<p class="empty">Could not load data.</p>`);
