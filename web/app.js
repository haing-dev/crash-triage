/* Biotech Triage — static front end for two triages (crash, slide). Reads data.json built by pipeline/build_site.py.
   Routes: #/<kind>/<view>  kind = crash | slide; view = screener (default) | backtest | outcomes | track | c/<id> */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const KIND = {
  crash: {label: "Crash Triage", word: "crash", plural: "crashes",
    facts: [["safety","Safety"],["runway","Cash runway"],["value","Value vs cash"],["insider","Insider buying"],["programs","Other programs"]],
    blurb: "a US biotech stock fell 50% or more within 5 trading days, starting with a news day of −25% or worse. Entry = the trough"},
  slide: {label: "Slide Triage", word: "slide", plural: "slides",
    facts: [["runway","Cash runway"],["value","Value vs cash"],["insider","Insider buying"],["programs","Other programs"]],
    blurb: "a US biotech stock slid 30% below its 6-month high with no single day of −25% or worse (stocks that crashed in the same period are left out). Entry = the first close 30% below the high"}};
const ICON = {g:"✓", r:"✕", n:"–"};
const VERDICT = {
  overdone:["p-good","▲","Potentially Oversold","var(--g-line)"],
  close:["p-warn","●","Too Close To Call","var(--w-line)"],
  justified:["p-crit","■","Justified","var(--r-line)"],
  pending:["p-pend","","Not scored yet","var(--axis)"]};
const OUTCOME = {Winner:"p-good", Recovery:"p-warn", Failure:"p-crit"};
const BUCKETS = {
  safety:["Reported Death","Negative Side Effects","Well Tolerated","N/A"],
  runway:["Under 6 mo","6–12 mo","12–24 mo","24+ mo","No cash burn","N/A"], value:["Below Cash","Above Cash","N/A"],
  insider:["Insider Buy","Insider Sell","N/A"], programs:["1 trial","2–4 trials","5+ trials","N/A"],
  mcap:["Under $50M","$50–300M","$300M–2B","Over $2B","N/A"]};
const pill = v => { const [c,i,t] = VERDICT[v] || VERDICT.pending; return `<span class="pill ${c}">${i?`<i>${i}</i>`:""}${t}</span>`; };
const dur = c => `${c.days} day${c.days === 1 ? "" : "s"}`;
const pct = v => v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(0)}%`;
const money = v => v == null ? "N/A" : v >= 1e9 ? `$${(v/1e9).toFixed(1)}B` : v >= 1e7 ? `$${(v/1e6).toFixed(0)}M` : `$${(v/1e6).toFixed(1)}M`;
const mcapBucket = v => v == null ? "N/A" : v < 5e7 ? "Under $50M" : v < 3e8 ? "$50–300M" : v < 2e9 ? "$300M–2B" : "Over $2B";
let D = null, K = "crash", SHOW = 60, MODE = "screener", SORT = false;
const items = () => D.kinds[K].items;
const base = () => `#/${K}`;

const tip = $("#tip");
function showTip(e, html){ tip.innerHTML = html; tip.style.opacity = 1;
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = e.clientY - tip.offsetHeight - 12;
  tip.style.left = x + "px"; tip.style.top = (y < 8 ? e.clientY + 18 : y) + "px"; }
function hideTip(){ tip.style.opacity = 0; }

function cell(f){ const [k,v] = f || ["n","N/A"];
  return v === "N/A" ? `<td class="f f-na">N/A</td>` : `<td class="f f-${k}"><span class="ic">${ICON[k]||""}</span>${esc(v)}</td>`; }

/* ------------------------------------------------------------------ the two tables
   screener = episodes less than a year past entry (return to date); backtest = a full year since entry */
const rowsFor = mode => items().filter(c => mode === "backtest" ? c.played : !c.played);
const bucketOf = (c, k) => k === "mcap" ? mcapBucket(c.mcap) : c.factors?.[k]?.[3] || "N/A";
function viewTable(mode){
  MODE = mode; SHOW = 60; SORT = false;
  const kd = KIND[K], rows = rowsFor(mode), bt = mode === "backtest", crash = K === "crash";
  const years = [...new Set(rows.map(c => c.date.slice(0,4)))].sort().reverse();
  const trig = [...new Set(rows.map(c => c.trigger).filter(Boolean))].sort();
  const opts = k => { const seen = new Set(rows.map(c => bucketOf(c, k))); return (BUCKETS[k] || [...seen].sort()).filter(o => seen.has(o)); };
  const sel = (id, list) => `<select class="hsel" id="${id}"><option value="">All</option>${list.map(([v,l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>`;
  const cols = kd.facts.map(([k, label]) => k === "insider"
    ? `<th class="fh sortable" id="sort_ins" title="Sort: biggest buy first, biggest sell last">${label} <span id="sort_ic">⇅</span></th>` : `<th class="fh">${label}</th>`).join("");
  $("#app").innerHTML = `
  <div class="toprow"><div><h1>${bt ? "Backtest" : "Screener"}</h1><div class="muted">${rows.length} times ${kd.blurb}. ${bt
     ? "A full year since entry: return if bought at entry and held one year · Winner ≥ +30%, Recovery 0 to +30%, Failure below 0%"
     : "Less than a year since entry: return if bought at entry, to the latest close"}</div></div>
   <div class="filters">
    <select id="fy"><option value="">All years</option>${years.map(y=>`<option>${y}</option>`).join("")}</select>
    <input id="fq" placeholder="Search company or ticker"></div></div>
  <div class="tablewrap"><table class="crash"><thead>
   <tr><th>Company</th><th>${crash ? "Crash" : "Entry"}</th><th class="r">${crash ? "Fall" : "Fall from high"}</th>${crash ? "<th>What happened</th>" : ""}<th class="r">Market cap<br>at entry</th>${cols}
    <th>Verdict</th><th class="r">${bt ? "1-year return<br>from entry" : "Return since<br>entry"}</th>${bt ? "<th>Outcome</th>" : ""}</tr>
   <tr class="frow"><th></th><th></th><th></th>${crash ? `<th>${sel("ft", trig.map(t => [t, t]))}</th>` : ""}<th>${sel("f_mcap", opts("mcap").map(o => [o,o]))}</th>
    ${kd.facts.map(([k]) => `<th>${sel("f_"+k, opts(k).map(o => [o,o]))}</th>`).join("")}
    <th>${sel("f_verdict", Object.entries(VERDICT).filter(([k]) => rows.some(c => (c.verdict||"pending") === k)).map(([k,v]) => [k, v[2]]))}</th><th></th>
    ${bt ? `<th>${sel("f_outcome", ["Winner","Recovery","Failure"].map(o => [o,o]))}</th>` : ""}</tr>
  </thead><tbody id="rows"></tbody></table></div>
  <div class="legend"><span class="lg f-g"><span class="ic">✓</span>Better than typical</span><span class="lg f-n"><span class="ic">–</span>Neutral</span><span class="lg f-r"><span class="ic">✕</span>Worse than typical</span>
   <span class="muted" id="cnt"></span></div><div class="more" id="more"></div>`;
  document.querySelectorAll("#fy,#fq,.hsel").forEach(el => el.addEventListener("input", () => { SHOW = 60; fill(); }));
  document.querySelectorAll(".hsel").forEach(el => el.addEventListener("click", e => e.stopPropagation()));
  $("#sort_ins").onclick = () => { SORT = !SORT; $("#sort_ic").textContent = SORT ? "↓" : "⇅"; SHOW = 60; fill(); };
  fill();
}
function fill(){
  const kd = KIND[K], bt = MODE === "backtest", crash = K === "crash", val = id => $("#"+id)?.value || "";
  const y = val("fy"), t = val("ft"), q = val("fq").trim().toLowerCase(), v = val("f_verdict"), o = val("f_outcome");
  const keys = ["mcap", ...kd.facts.map(f => f[0])], fv = Object.fromEntries(keys.map(k => [k, val("f_"+k)]));
  const list = rowsFor(MODE).filter(c => (!y || c.date.startsWith(y)) && (!t || c.trigger === t) && (!v || (c.verdict||"pending") === v)
    && (!o || c.outcome === o) && keys.every(k => !fv[k] || bucketOf(c, k) === fv[k])
    && (!q || `${c.name} ${c.ticker} ${c.summary||""}`.toLowerCase().includes(q)));
  if (SORT) list.sort((a, b) => (b.insider_net ?? 0) - (a.insider_net ?? 0));   // biggest buy first, N/A middle, biggest sell last
  const ret = c => c.ret == null ? `<td class="r muted">N/A</td>` :
    `<td class="r" style="white-space:nowrap"><span class="num" style="color:${c.ret >= 0 ? "var(--good)" : "var(--bad)"}">${pct(c.ret)}</span>
     <div class="muted" style="font-size:11.5px">${c.stopped ? `stopped ${esc(c.stopped)}` : bt ? "after 1 year" : `to ${esc(c.ret_to)}`}</div></td>`;
  $("#rows").innerHTML = list.slice(0, SHOW).map(c => `<tr data-id="${c.id}"><td><b>${esc(c.name)}</b><div class="muted" style="font-size:11.5px">${esc(c.ticker)}</div></td>
    <td style="white-space:nowrap">${esc(c.month)}</td><td class="r" style="white-space:nowrap"><span class="num">${pct(c.move)}</span><div class="muted" style="font-size:11.5px">${dur(c)}</div></td>
    ${crash ? `<td class="what">${esc(c.summary || "Reading the company's announcement…")}</td>` : ""}
    <td class="r ${c.mcap == null ? "muted" : ""}" style="white-space:nowrap">${money(c.mcap)}</td>
    ${kd.facts.map(f => cell(c.factors?.[f[0]])).join("")}<td>${pill(c.verdict)}${c.why?.length ? `<div class="why">${esc(c.why.join(" · "))}</div>` : ""}</td>${ret(c)}
    ${bt ? `<td>${c.outcome ? `<span class="pill ${OUTCOME[c.outcome]}">${c.outcome}</span>` : ""}</td>` : ""}</tr>`).join("") ||
    `<tr><td colspan="14" class="empty">Nothing matches these filters.</td></tr>`;
  $("#cnt").textContent = `Showing ${Math.min(SHOW, list.length)} of ${list.length}.`;
  $("#more").innerHTML = list.length > SHOW ? `<button id="mb">Show more</button>` : "";
  if (list.length > SHOW) $("#mb").onclick = () => { SHOW += 60; fill(); };
  document.querySelectorAll("#rows tr[data-id]").forEach(tr => tr.onclick = () => location.hash = `${base()}/c/${tr.dataset.id}`);
}

/* ------------------------------------------------------------------ one episode */
function viewOne(id){
  const kd = KIND[K], c = items().find(x => x.id === id);
  if (!c){ $("#app").innerHTML = `<p class="empty">Not found. <a href="${base()}">Back</a></p>`; return; }
  const back = c.played ? `${base()}/backtest` : base();
  const cards = kd.facts.map(([k, label]) => { const f = c.factors?.[k] || ["n","N/A",""];
    if (f[1] === "N/A") return `<div class="fc f-na"><div class="fl">${label}</div><div class="fv">N/A</div><div class="fn">${esc(f[2]||"")}</div></div>`;
    return `<div class="fc f-${f[0]}"><div class="fl">${label}</div><div class="fv"><span class="ic">${ICON[f[0]]||""}</span>${esc(f[1])}</div><div class="fn">${esc(f[2]||"")}</div></div>`; }).join("");
  const quotes = (c.quotes||[]).map(q => `<div class="quote">“${esc(q)}”</div>`).join("");
  const src = (c.sources||[]).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join("");
  const after = c.ret == null ? "" : `<div class="card"><h4>What happened next</h4><div class="tiles">
     <div><b>${pct(c.ret)}</b><span>${c.played ? "1-year return if bought at entry" : `return if bought at entry, to ${esc(c.ret_to)}`} (entry ${esc(c.entry_date)}${c.stopped ? `, stopped trading ${esc(c.stopped)}` : ""})</span></div>
     ${c.outcome ? `<div><b><span class="pill ${OUTCOME[c.outcome]}">${c.outcome}</span></b><span>outcome</span></div>` : ""}
     ${c.xbi365 == null ? "" : `<div><b>${pct(c.xbi365)}</b><span>biotech index over the same year</span></div>`}</div></div>`;
  const head = K === "crash" ? `News day ${esc(c.date)} (${pct(c.news_day)} that day)`
    : `Entry ${esc(c.date)} · 6-month high on ${esc(c.peak_date)} · worst single day ${pct(c.news_day)}`;
  $("#app").innerHTML = `
  <div class="vhead"><div><div class="muted">${head} · <a href="${back}">‹ ${c.played ? "Backtest" : "Screener"}</a></div>
    <h2>${esc(c.name)} <span class="muted" style="font-size:18px">${esc(c.ticker)}</span><span class="mv">${pct(c.move)}</span><span class="muted" style="font-size:16px;margin-left:8px">in ${dur(c)}</span></h2>
    <div class="what1">${K === "crash" ? esc(c.summary || "Reading the company's announcement…") + " · " : ""}Market cap at entry ${money(c.mcap)}</div></div>
   <div class="vbox">${pill(c.verdict)}<div class="conf">${c.why?.length ? esc(c.why.join(" · ")) : c.verdict === "overdone" ? "Cash for 12+ months, no going-concern warning, $50M+ market cap, valued at 3x cash or less" : ""}</div></div></div>
  <div class="fcs" style="grid-template-columns:repeat(${kd.facts.length},minmax(0,1fr))">${cards}</div>
  ${quotes ? `<div class="card"><h4>From the company's announcement</h4>${quotes}</div>` : ""}
  ${after}
  ${src ? `<div class="card srcs"><h4>Sources</h4>${src}</div>` : ""}`;
}

/* ------------------------------------------------------------------ outcomes: every backtest episode, one row */
function viewOutcomes(){
  const pts = items().filter(c => c.played && c.ret != null && c.verdict !== "pending");
  $("#app").innerHTML = `<div class="toprow"><div><h1>Outcomes</h1><div class="muted">Every backtest ${KIND[K].word}: the return if bought at entry and held 365 days, coloured by its verdict (which uses only what was public at entry). Hover a dot to see the company; click to open it.</div></div></div>
   <div class="card" id="oc">${pts.length ? "" : `<p class="empty">Outcomes appear once episodes are scored and have a full year of prices.</p>`}</div>`;
  if (!pts.length) return;
  const groups = ["overdone","close","justified"].filter(g => pts.some(c => c.verdict === g));
  const W = Math.max(720, $("#oc").clientWidth - 32), top = 34, left = 20, right = 24, band = 300, H = top + band + 48;
  const lo = -100, hi = Math.max(150, Math.min(400, Math.ceil(Math.max(...pts.map(p => p.ret)) / 50) * 50));
  const x = v => left + (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (W - left - right), med = median(pts.map(c => c.ret));
  let s = `<div class="chartleg">${groups.map(g => `<span><i style="background:${VERDICT[g][3]}"></i>${VERDICT[g][2]} · ${pts.filter(c => c.verdict === g).length}</span>`).join("")}
    <span><i class="ln" style="background:var(--ink)"></i>Median ${pct(med)} · ${pts.length}</span></div><svg width="${W}" height="${H}" role="img" aria-label="1-year returns from entry">`;
  for (let t = lo; t <= hi; t += 50) s += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H-40}" stroke="${t===0?"var(--axis)":"var(--line)"}"/><text x="${x(t)}" y="${H-24}" text-anchor="middle">${t===0?"0%":(t>0?"+":"")+t+"%"}</text>`;
  s += `<text x="${(left+W-right)/2}" y="${H-4}" text-anchor="middle">Return if bought at entry, 365 days later</text>`;
  pts.forEach(c => { const jy = top + 10 + hash(c.id) * (band - 20);
    s += `<circle class="pt" data-id="${c.id}" cx="${x(c.ret)}" cy="${jy}" r="${pts.length > 1200 ? 3.5 : 5}" fill="${VERDICT[c.verdict][3]}" fill-opacity=".85" stroke="var(--bg)" stroke-width="1" style="cursor:pointer"/>`; });
  s += `<line x1="${x(med)}" x2="${x(med)}" y1="${top-6}" y2="${top+band}" stroke="var(--ink)" stroke-width="2.5"/><text class="lab" x="${x(med)+6}" y="${top-12}" font-weight="600">Median ${pct(med)}</text>`;
  $("#oc").innerHTML = s + "</svg>";
  const byId = Object.fromEntries(pts.map(c => [c.id, c]));
  document.querySelectorAll(".pt").forEach(el => { const c = byId[el.dataset.id];
    el.onmousemove = e => showTip(e, `<b>${esc(c.name)} · ${esc(c.month)}</b>${VERDICT[c.verdict][2]} · ${pct(c.move)} fall → ${pct(c.ret)} a year after entry<br><span class="a">Click to open ›</span>`);
    el.onmouseleave = hideTip; el.onclick = () => { hideTip(); location.hash = `${base()}/c/${c.id}`; }; });
}

/* ------------------------------------------------------------------ track record */
function viewTrack(){
  const T = D.kinds[K].track || {};
  const series = [["overdone","Potentially Oversold"],["close","Too Close To Call"],["justified","Justified"]].filter(([k]) => T[k]?.path?.length)
    .map(([k,l]) => ({k, l: `${l} · ${T[k].n}`, col: VERDICT[k][3], path: T[k].path}));
  if (T.xbi?.path?.length) series.push({k:"xbi", l:"Biotech index (XBI)", col:"var(--idx)", path:T.xbi.path, dash:true});
  const S = D.kinds[K].stats || {}, f = v => v == null ? "–" : pct(v);
  const table = Object.keys(S).length ? `<div class="card"><h4>One year after entry, by verdict</h4><div class="tablewrap"><table class="stats"><thead><tr><th>Verdict</th><th class="r">${KIND[K].plural}</th><th class="r">Median</th><th class="r">Average*</th><th class="r">Biotech index average</th><th class="r">Beat the index</th><th class="r">Up 30%+</th><th class="r">Down 80%+</th></tr></thead><tbody>
    ${["overdone","close","justified"].filter(g => S[g]).map(g => `<tr><td>${pill(g)}</td><td class="r">${S[g].n}</td><td class="r">${f(S[g].median)}</td><td class="r"><b>${f(S[g].mean)}</b></td><td class="r">${f(S[g].xbi_mean)}</td><td class="r">${S[g].beat}%</td><td class="r">${S[g].win30}%</td><td class="r">${S[g].wipe80}%</td></tr>`).join("")}
    </tbody></table></div><div class="muted" style="font-size:12px;margin-top:8px">*Average with any single return capped at +500%. Biotech recoveries are lopsided (a few big winners, many slow losers), so the average and the hit rates describe a basket of picks better than the median.</div></div>` : "";
  $("#app").innerHTML = `<div class="toprow"><div><h1>Track record</h1><div class="muted">Backtest ${KIND[K].plural}: return if bought at entry, by verdict, vs. the biotech index over the same dates. The chart shows the median path.</div></div></div>
   ${table}<div class="card" id="tr">${series.length > 1 ? "" : `<p class="empty">The track record appears once episodes are scored and have a full year of prices.</p>`}</div>`;
  if (series.length < 2) return;
  const W = Math.max(720, $("#tr").clientWidth - 32), H = 420, left = 50, right = 230, top = 14, bot = 44;
  const all = series.flatMap(s => s.path), lo = Math.floor(Math.min(-10, ...all) / 10) * 10, hi = Math.ceil(Math.max(10, ...all) / 10) * 10;
  const n = series[0].path.length, x = i => left + i / (n - 1) * (W - left - right), y = v => top + (hi - v) / (hi - lo) * (H - top - bot);
  let s = `<div class="chartleg">${series.map(r => `<span><i class="ln" style="background:${r.col}"></i>${esc(r.l)}</span>`).join("")}</div><svg width="${W}" height="${H}">`;
  for (let t = lo; t <= hi; t += 10) s += `<line x1="${left}" x2="${W-right}" y1="${y(t)}" y2="${y(t)}" stroke="${t===0?"var(--axis)":"var(--line)"}"/><text x="${left-8}" y="${y(t)+4}" text-anchor="end">${t===0?"0%":(t>0?"+":"")+t+"%"}</text>`;
  [0,90,180,270,365].forEach(d => s += `<text x="${x(Math.round(d/365*(n-1)))}" y="${H-24}" text-anchor="middle">${d ? "day " + d : "entry"}</text>`);
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
  let h = (location.hash || "#/").slice(2);
  if (!/^(crash|slide)(\/|$)/.test(h)) h = "crash/" + h;          // old links (#/backtest, #/c/...) are crash links
  const [kind, ...rest] = h.split("/"), view = rest[0] || "screener";
  K = kind;
  document.querySelectorAll(".kind button").forEach(b => b.classList.toggle("on", b.dataset.kind === K));
  document.querySelectorAll("nav a").forEach(a => { a.href = `${base()}${a.dataset.nav === "screener" ? "" : "/" + a.dataset.nav}`;
    a.classList.toggle("on", a.dataset.nav === (view === "c" ? "screener" : view)); });
  document.title = KIND[K].label;
  if (view === "c") viewOne(decodeURIComponent(rest.slice(1).join("/")));
  else if (view === "outcomes") viewOutcomes(); else if (view === "track") viewTrack();
  else viewTable(view === "backtest" ? "backtest" : "screener");
  scrollTo(0, 0);
}
fetch("data.json", {cache: "no-store"}).then(r => r.json()).then(d => {
  D = d; Object.values(D.kinds).forEach(k => k.items.sort((a,b) => b.date.localeCompare(a.date)));
  document.querySelectorAll(".kind button").forEach(b => b.onclick = () => { location.hash = `#/${b.dataset.kind}`; });
  $("#foot").textContent = `Updated ${d.updated}. Data: SEC EDGAR, ClinicalTrials.gov, Twelve Data, Tiingo, Massive. Not investment advice.`;
  addEventListener("hashchange", route); route();
}).catch(() => $("#app").innerHTML = `<p class="empty">Could not load data.</p>`);
