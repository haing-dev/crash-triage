/* Biotech Triage — static front end for two triages (crash, slide). Reads data.json built by pipeline/build_site.py.
   Routes: #/<kind>/<view>  kind = crash | slide; view = screener (default) | backtest | track | c/<id> */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const FACTS = [["ev","EV/Cash"],["programs","Alternative Programs"],["raise","Raise Terms"],["insider","Insider Buy/Sell"],
  ["funds","High Conviction Owners"],["top","Top Performer Owners"],["short","Short Interest"]];
const KIND = {
  crash: {label: "Crash Triage", word: "crash", plural: "crashes", facts: [["safety","Safety"], ...FACTS],
    blurb: "a US biotech stock fell 50% or more within 5 trading days, starting with a news day of −25% or worse. Entry = the close on the day the crash is detected"},
  slide: {label: "Slide Triage", word: "slide", plural: "slides", facts: FACTS,
    blurb: "a US biotech stock slid 30% below its 6-month high with no single day of −25% or worse (stocks that crashed in the same period are left out). Entry = the first close 30% below the high"}};
const ICON = {g:"✓", r:"✕", n:"–"};
const VERDICT = {
  overdone:["p-good","▲","Buy candidate","var(--g-line)"],
  close:["p-warn","●","Watch","var(--w-line)"],
  justified:["p-crit","■","Avoid","var(--r-line)"],
  tbu:["p-pend","","TBU","var(--axis)"]};
const CALLS = ["overdone","close","justified"];
const OUTCOME = {Winner:"p-good", Recovery:"p-warn", Failure:"p-crit"};
const BUCKETS = {
  safety:["Reported Death","Negative Side Effects","Well Tolerated","N/A"],
  ev:["Below cash","0–1×","1–2×","Over 2×","N/A"],
  programs:["1 trial","2–4 trials","5+ trials","N/A"], raise:["Friendly","Costly","Toxic","N/A"], insider:["Bought","Sold","N/A"],
  funds:["None","1 fund","2+ funds","N/A"], top:["Yes","No","N/A"], short:["Under 5%","5–15%","15%+","N/A"],
  mcap:["Under $50M","$50–300M","$300M–2B","Over $2B","N/A"]};
const pill = v => { const [c,i,t] = VERDICT[v] || VERDICT.tbu; return `<span class="pill ${c}">${i?`<i>${i}</i>`:""}${t}</span>`; };
const dur = c => `${c.days} day${c.days === 1 ? "" : "s"}`;
const pct = v => { if (v == null) return "–"; const r = Math.round(v); return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)}%`; };
const money = v => v == null ? "N/A" : v >= 1e9 ? `$${(v/1e9).toFixed(1)}B` : v >= 1e7 ? `$${(v/1e6).toFixed(0)}M` : `$${(v/1e6).toFixed(1)}M`;
const usd = v => "$" + Math.round(v).toLocaleString("en-US");
const mcapBucket = v => v == null ? "N/A" : v < 5e7 ? "Under $50M" : v < 3e8 ? "$50–300M" : v < 2e9 ? "$300M–2B" : "Over $2B";
let D = null, K = "crash", SHOW = 60, MODE = "screener", SORT = false, AGO = 0;
const daysAgo = c => Math.round((Date.parse(D.updated.slice(0, 10)) - Date.parse(c.date)) / 864e5);
const items = () => D.kinds[K].items;
const base = () => `#/${K}`;

const tip = $("#tip");
function showTip(e, html){ tip.innerHTML = html; tip.style.opacity = 1;
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = e.clientY - tip.offsetHeight - 12;
  tip.style.left = x + "px"; tip.style.top = (y < 8 ? e.clientY + 18 : y) + "px"; }
function hideTip(){ tip.style.opacity = 0; }

function cell(f, wide){ const [k,v,n] = f || ["n","N/A",""], t = n ? ` title="${esc(n)}"` : "";
  return v === "N/A" ? `<td class="f f-na${wide?" wide":""}"${t}>N/A</td>` : `<td class="f f-${k}${wide?" wide":""}${n?" tip":""}"${t}><span class="ic">${ICON[k]||""}</span>${esc(v)}</td>`; }
const th = (label, sub, cls="") => `<th class="${cls}">${label}${sub ? `<div class="hs">${sub}</div>` : ""}</th>`;
const retCell = (c, bt) => c.not_bought ? `<td class="r muted" title="Stopped trading ${esc(c.stopped)}, before the buy day ${esc(c.buy_date)}">Not bought</td>` :
  c.ret == null ? `<td class="r muted"${c.buy_date > D.updated.slice(0, 10) ? ` title="Bought at the close 91 days after entry">Buy ${esc(c.buy_date)}` : ">N/A"}</td>` :
  `<td class="r" style="white-space:nowrap" title="Bought at the close of ${esc(c.buy_date)}, 91 days after entry"><span class="num" style="color:${c.ret >= 0 ? "var(--good)" : "var(--bad)"}">${pct(c.ret)}</span>
   <div class="muted sm">${c.ctrl_ret != null ? `similar biotechs ${pct(c.ctrl_ret)}` : ""}${c.stopped ? `${c.ctrl_ret != null ? "<br>" : ""}stopped trading ${esc(c.stopped)}` : ""}</div></td>`;

/* ------------------------------------------------------------------ the two tables
   screener = episodes less than a year past entry (return to date); backtest = a full year since entry */
const rowsFor = mode => items().filter(c => mode === "backtest" ? c.played : !c.played);
const bucketOf = (c, k) => k === "mcap" ? mcapBucket(c.mcap) : c.factors?.[k]?.[3] || "N/A";
function viewTable(mode){
  MODE = mode; SHOW = 60; SORT = false; AGO = 0;
  const kd = KIND[K], rows = rowsFor(mode), bt = mode === "backtest", crash = K === "crash";
  const years = [...new Set(rows.map(c => c.date.slice(0,4)))].sort().reverse();
  const trig = [...new Set(rows.map(c => c.cause).filter(Boolean))].sort();
  const opts = k => { const seen = new Set(rows.map(c => bucketOf(c, k))); return (BUCKETS[k] || [...seen].sort()).filter(o => seen.has(o)); };
  const sel = (id, list) => `<select class="hsel" id="${id}"><option value="">All</option>${list.map(([v,l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>`;
  const cols = kd.facts.map(([k, label, sub]) => k === "insider"
    ? `<th class="fh sortable" id="sort_ins" title="Sort: biggest buy first, biggest sell last">${label} <span id="sort_ic">⇅</span></th>` : th(label, "", "fh")).join("");
  const nT = rows.filter(c => c.verdict === "tbu").length;
  $("#app").innerHTML = `
  <div class="toprow"><div><h1>${bt ? "Backtest" : "Screener"}</h1><div class="muted">${rows.length} times ${kd.blurb}. ${bt
     ? "A full year since the buy (the close 91 days after entry): total return held one year · Winner ≥ +30%, Recovery 0 to +30%, Failure below 0%"
     : "Less than a year since the buy (the close 91 days after entry): total return to the latest close"}${nT ? ` · ${nT} TBU: an input for the entry day is still being collected` : ""}</div></div>
   <div class="filters">
    <select id="fy"><option value="">All years</option>${years.map(y=>`<option>${y}</option>`).join("")}</select>
    <input id="fq" placeholder="Search company or ticker"></div></div>
  <div class="tablewrap"><table class="crash"><thead>
   <tr><th>Company</th><th class="r sortable" id="sort_ago" title="Sort by days since the ${kd.word}">Drawdown <span id="ago_ic">⇅</span></th>${crash ? "<th>What happened</th>" : ""}${th("Market Cap at Entry","","r")}${cols}
    <th>Call</th>${th(bt ? "1-Year Return" : "Return","","r")}${bt ? "<th>Outcome</th>" : ""}</tr>
   <tr class="frow"><th></th><th></th>${crash ? `<th>${sel("ft", trig.map(t => [t, t]))}</th>` : ""}<th>${sel("f_mcap", opts("mcap").map(o => [o,o]))}</th>
    ${kd.facts.map(([k]) => `<th>${sel("f_"+k, opts(k).map(o => [o,o]))}</th>`).join("")}
    <th>${sel("f_verdict", Object.entries(VERDICT).filter(([k]) => rows.some(c => (c.verdict||"tbu") === k)).map(([k,v]) => [k, v[2]]))}</th><th></th>
    ${bt ? `<th>${sel("f_outcome", ["Winner","Recovery","Failure"].map(o => [o,o]))}</th>` : ""}</tr>
  </thead><tbody id="rows"></tbody></table></div>
  <div class="legend"><span class="lg f-g"><span class="ic">✓</span>Better than similar biotechs</span><span class="lg f-n"><span class="ic">–</span>Neutral</span><span class="lg f-r"><span class="ic">✕</span>Worse than similar biotechs</span>
   <span class="muted" id="cnt"></span></div><div class="more" id="more"></div>`;
  document.querySelectorAll("#fy,#fq,.hsel").forEach(el => el.addEventListener("input", () => { SHOW = 60; fill(); }));
  document.querySelectorAll(".hsel").forEach(el => el.addEventListener("click", e => e.stopPropagation()));
  $("#sort_ins").onclick = () => { SORT = !SORT; AGO = 0; $("#sort_ic").textContent = SORT ? "↓" : "⇅"; $("#ago_ic").textContent = "⇅"; SHOW = 60; fill(); };
  $("#sort_ago").onclick = () => { AGO = AGO === 1 ? -1 : 1; SORT = false; $("#ago_ic").textContent = AGO === 1 ? "↑" : "↓"; $("#sort_ic").textContent = "⇅"; SHOW = 60; fill(); };
  fill();
}
function fill(){
  const kd = KIND[K], bt = MODE === "backtest", crash = K === "crash", val = id => $("#"+id)?.value || "";
  const y = val("fy"), t = val("ft"), q = val("fq").trim().toLowerCase(), v = val("f_verdict"), o = val("f_outcome");
  const keys = ["mcap", ...kd.facts.map(f => f[0])], fv = Object.fromEntries(keys.map(k => [k, val("f_"+k)]));
  const list = rowsFor(MODE).filter(c => (!y || c.date.startsWith(y)) && (!t || c.cause === t) && (!v || (c.verdict||"tbu") === v)
    && (!o || c.outcome === o) && keys.every(k => !fv[k] || bucketOf(c, k) === fv[k])
    && (!q || `${c.name} ${c.ticker} ${c.summary||""}`.toLowerCase().includes(q)));
  if (SORT) list.sort((a, b) => (b.insider_net ?? 0) - (a.insider_net ?? 0));
  if (AGO) list.sort((a, b) => AGO * (daysAgo(a) - daysAgo(b)));          // fewest days ago first, or most
  $("#rows").innerHTML = list.slice(0, SHOW).map(c => `<tr data-id="${c.id}"><td><b>${esc(c.name)}</b><div class="muted sm">${esc(c.ticker)} · ${esc(c.entry_date)}</div></td>
    <td class="r" style="white-space:nowrap"><span class="num">${pct(c.move)}</span><div class="muted sm">${daysAgo(c)} days ago</div></td>
    ${crash ? `<td class="what">${esc(c.summary || "Reading the company's announcement…")}</td>` : ""}
    <td class="r ${c.mcap == null ? "muted" : ""}" style="white-space:nowrap">${money(c.mcap)}${c.price != null ? `<div class="muted sm">$${c.price < 1 ? c.price.toFixed(3) : c.price.toFixed(2)}/share</div>` : ""}</td>
    ${kd.facts.map(f => cell(c.factors?.[f[0]], f[0] === "insider")).join("")}
    <td${c.why?.length ? ` title="${esc(c.why.join(" · "))}"` : ""}>${pill(c.verdict)}</td>${retCell(c, bt)}
    ${bt ? `<td>${c.outcome ? `<span class="pill ${OUTCOME[c.outcome]}">${c.outcome}</span>` : ""}</td>` : ""}</tr>`).join("") ||
    `<tr><td colspan="20" class="empty">Nothing matches these filters.</td></tr>`;
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
     <div><b>${pct(c.ret)}</b><span>${c.played ? "1-year total return" : `total return to ${esc(c.ret_to)}`} if bought at the close of ${esc(c.buy_date)}, 91 days after entry${c.stopped ? `; stopped trading ${esc(c.stopped)}, valued at its last close` : ""}</span></div>
     ${c.ctrl_ret == null ? "" : `<div><b>${pct(c.ctrl_ret)}</b><span>10 similar biotechs bought the same day (same size and 6-month price change, no crash or slide of their own)</span></div>`}
     ${c.outcome ? `<div><b><span class="pill ${OUTCOME[c.outcome]}">${c.outcome}</span></b><span>outcome</span></div>` : ""}</div></div>`;
  const head = K === "crash" ? `News day ${esc(c.date)} (${pct(c.news_day)} that day) · entry close ${esc(c.entry_date)}`
    : `Entry ${esc(c.entry_date)} · 6-month high on ${esc(c.peak_date)} · worst single day ${pct(c.news_day)}`;
  $("#app").innerHTML = `
  <div class="vhead"><div><div class="muted">${head} · <a href="${back}">‹ ${c.played ? "Backtest" : "Screener"}</a></div>
    <h2>${esc(c.name)} <span class="muted" style="font-size:18px">${esc(c.ticker)}</span><span class="mv">${pct(c.move)}</span><span class="muted" style="font-size:16px;margin-left:8px">${daysAgo(c)} days ago</span></h2>
    <div class="what1">${K === "crash" ? esc(c.summary || "Reading the company's announcement…") + " · " : ""}Market cap at entry ${money(c.mcap)}</div></div>
   <div class="vbox">${pill(c.verdict)}<div class="conf">${c.why?.length ? esc(c.why.join(" · ")) : c.verdict === "overdone" ? "No going-concern warning, share price $1+, no toxic financing, market cap $50M+, EV / Cash 2× or less" : ""}</div></div></div>
  <div class="fcs" style="grid-template-columns:repeat(${Math.min(kd.facts.length, 4)},minmax(0,1fr))">${cards}</div>
  ${quotes ? `<div class="card"><h4>From the company's announcement</h4>${quotes}</div>` : ""}
  ${after}
  ${src ? `<div class="card srcs"><h4>Sources</h4>${src}</div>` : ""}`;
}

/* ------------------------------------------------------------------ track record */
function lineChart(el, series, n, xlab, fmt){
  const W = Math.max(520, el.clientWidth - 32), H = 340, left = 56, right = 150, top = 14, bot = 40;
  const all = series.flatMap(s => s.path), lo = fmt.lo(Math.min(...all)), hi = fmt.hi(Math.max(...all));
  const x = i => left + i / (n - 1) * (W - left - right), y = v => top + (hi - v) / (hi - lo) * (H - top - bot);
  let s = `<div class="chartleg">${series.map(r => `<span><i class="ln" style="background:${r.col}"></i>${esc(r.l)}</span>`).join("")}</div><svg width="${W}" height="${H}">`;
  fmt.ticks(lo, hi).forEach(t => s += `<line x1="${left}" x2="${W-right}" y1="${y(t)}" y2="${y(t)}" stroke="${t===fmt.zero?"var(--axis)":"var(--line)"}"/><text x="${left-8}" y="${y(t)+4}" text-anchor="end">${fmt.tick(t)}</text>`);
  xlab.forEach(([i, l]) => s += `<text x="${x(i)}" y="${H-16}" text-anchor="middle">${esc(l)}</text>`);
  const ends = series.map((r,i) => ({i, y: y(r.path[n-1])})).sort((a,b) => a.y - b.y); let last = -1e9;
  ends.forEach(e => { e.ly = Math.max(e.y, last + 18); last = e.ly; });
  series.forEach((r, i) => { s += `<path d="M${r.path.map((v,j)=>`${x(j).toFixed(1)},${y(v).toFixed(1)}`).join(" L")}" fill="none" stroke="${r.col}" stroke-width="2.5" ${r.dash?'stroke-dasharray="6 5"':""}/>`;
    const e = ends.find(z => z.i === i); s += `<circle cx="${x(n-1)}" cy="${y(r.path[n-1])}" r="4.5" fill="${r.col}"/><text class="lab" x="${x(n-1)+10}" y="${e.ly+4}" font-weight="700">${fmt.end(r.path[n-1])}</text>`; });
  s += `<line class="xh" y1="${top}" y2="${H-bot}" stroke="var(--axis)" opacity="0"/><rect x="${left}" y="${top}" width="${W-left-right}" height="${H-top-bot}" fill="transparent" class="hit"/></svg>`;
  el.innerHTML += s;
  const hit = el.querySelector(".hit"), xh = el.querySelector(".xh");
  hit.onmousemove = e => { const b = hit.getBoundingClientRect(), j = Math.max(0, Math.min(n-1, Math.round((e.clientX - b.left) / b.width * (n - 1))));
    xh.setAttribute("x1", x(j)); xh.setAttribute("x2", x(j)); xh.setAttribute("opacity", 1);
    showTip(e, `<b>${esc(fmt.at(j))}</b>` + series.map(r => `${esc(r.l)}: ${fmt.end(r.path[j])}`).join("<br>")); };
  hit.onmouseleave = () => { xh.setAttribute("opacity", 0); hideTip(); };
}
const PCT = {lo: v => Math.floor(Math.min(-10, v) / 10) * 10, hi: v => Math.ceil(Math.max(10, v) / 10) * 10, zero: 0,
  ticks: (lo, hi) => { const st = (hi - lo) > 120 ? 20 : 10, o = []; for (let t = lo; t <= hi; t += st) o.push(t); return o; },
  tick: t => t === 0 ? "0%" : (t > 0 ? "+" : "−") + Math.abs(t) + "%", end: v => pct(v)};

function barChart(el, data, keys){
  const W = Math.max(520, el.clientWidth - 32), H = 330, left = 56, right = 16, top = 22, bot = 56;
  const vals = data.flatMap(d => keys.map(k => d[k.k]).filter(v => v != null)), lo = Math.floor(Math.min(0, ...vals) / 10) * 10 - 10, hi = Math.ceil(Math.max(10, ...vals) / 10) * 10 + 10;
  const y = v => top + (hi - v) / (hi - lo) * (H - top - bot), gw = (W - left - right) / data.length, bw = Math.min(30, (gw - 16) / keys.length);
  let s = `<div class="chartleg">${keys.map(k => `<span><i class="ln" style="background:${k.col}"></i>${esc(k.l)}</span>`).join("")}</div><svg width="${W}" height="${H}">`;
  PCT.ticks(lo, hi).forEach(t => s += `<line x1="${left}" x2="${W-right}" y1="${y(t)}" y2="${y(t)}" stroke="${t===0?"var(--axis)":"var(--line)"}"/><text x="${left-8}" y="${y(t)+4}" text-anchor="end">${PCT.tick(t)}</text>`);
  data.forEach((d, i) => { const x0 = left + i * gw + gw / 2 - keys.length * bw / 2;
    keys.forEach((k, j) => { const v = d[k.k]; if (v == null) return; const a = y(Math.max(v, 0)), b = y(Math.min(v, 0));
      s += `<rect x="${x0 + j * bw + 1}" y="${a}" width="${bw - 3}" height="${Math.max(1, b - a)}" rx="3" fill="${k.col}"/><text class="lab" x="${x0 + j * bw + bw / 2}" y="${v >= 0 ? a - 5 : b + 13}" text-anchor="middle" font-size="11">${pct(v)}</text>`; });
    s += `<text class="lab" x="${left + i * gw + gw / 2}" y="${H - 30}" text-anchor="middle" font-weight="600">${esc(d.label)}</text><text x="${left + i * gw + gw / 2}" y="${H - 14}" text-anchor="middle" font-size="11">${esc(d.sub)}</text>`; });
  el.innerHTML += s + "</svg>";
}

function viewTrack(){
  const kd = D.kinds[K], T = kd.track, Y = kd.years || {}, ys = Object.keys(Y).sort(), C = kd.causes || {}, f = v => v == null ? "–" : pct(v);
  const steps = kd.steps || [], last = a => a?.length ? a[a.length - 1] : null;
  const causes = Object.keys(C).length ? `<div class="card"><h4>One year after the buy, by cause of the crash (all calls)</h4><div class="tablewrap"><table class="stats">
    <thead><tr><th>Cause</th><th class="r">Crashes</th><th class="r">Median</th><th class="r">Average*</th><th class="r">Similar biotechs, average</th><th class="r">Up 30%+</th><th class="r">Down 80%+</th></tr></thead><tbody>
    ${Object.entries(C).map(([k, s]) => `<tr><td>${esc(k)}</td><td class="r">${s.n}</td><td class="r">${f(s.median)}</td><td class="r"><b>${f(s.mean)}</b></td><td class="r">${f(s.ctrl_mean)}</td><td class="r">${s.win30}%</td><td class="r">${s.wipe80}%</td></tr>`).join("")}</tbody></table></div></div>` : "";
  $("#app").innerHTML = `<div class="toprow"><div><h1>Track record</h1><div class="muted">Buy candidates (called at entry), bought at the close 91 days after entry and held a year, against 10 similar biotechs bought the same day and the biotech index (XBI) over the same dates.</div></div></div>
   <div class="card" id="tr"><h4>Average return after buying${T ? ` · ${T.n} Buy candidates with a full year` : ""}</h4>${T ? `<div class="tiles" style="margin-bottom:12px">
     <div><b>${f(last(T.own))}</b><span>Buy candidates, 1 year later</span></div><div><b>${f(last(T.ctrl))}</b><span>similar biotechs</span></div><div><b>${f(last(T.xbi))}</b><span>biotech index (XBI)</span></div></div>` : `<p class="empty">Appears once Buy candidates have a full year of prices.</p>`}</div>
   <div class="card" id="pf"><div class="toprow" style="margin-bottom:4px"><h4 style="margin:0">$10,000 test: all Buy candidates bought in one year, equal money in each, each held a year</h4>
     ${ys.length ? `<select id="py">${ys.map(y => `<option${y === ys[ys.length - 2] || (ys.length === 1) ? " selected" : ""}>${y}</option>`).join("")}</select>` : ""}</div><div id="pfb"></div></div>
   ${causes}<div class="fnote">*Each return capped at +500%. Total return: price change plus cash paid out per share; a company that stopped trading (bought out, delisted) counts at its last close; reverse splits are taken out of the price. No trading costs. Similar biotechs: the 10 closest in market value and 6-month price change on the entry day, with no crash or slide of their own.</div>`;
  const xl = n => [0,90,180,270,365].map(d => [steps.indexOf(d) >= 0 ? steps.indexOf(d) : Math.round(d/365*(n-1)), d ? "day " + d : "bought"]).filter(([i]) => i < n);
  if (T) lineChart($("#tr"), [{l: "Buy candidates", col: "var(--g-line)", path: T.own}, {l: "Similar biotechs", col: "var(--idx)", path: T.ctrl, dash: true},
    {l: "Biotech index (XBI)", col: "var(--blue)", path: T.xbi, dash: true}], T.own.length, xl(T.own.length), {...PCT, at: j => `Day ${steps[j]}`});
  const draw = () => { const y = $("#py").value, v = Y[y], n = v.path.own.length, money = a => a.map(q => 10000 * (1 + q / 100));
    $("#pfb").innerHTML = `<div class="tiles" style="margin:8px 0 10px"><div><b>${usd(v.own)}</b><span>in ${v.n} Buy candidates bought in ${y}</span></div><div><b>${usd(v.ctrl)}</b><span>in their similar biotechs</span></div><div><b>${usd(v.xbi)}</b><span>in the biotech index (XBI)</span></div></div>
      ${v.open ? `<div class="fnote" style="margin:0 0 8px">${v.open} of them bought less than a year ago: valued at the latest close.</div>` : ""}`;
    if (n > 1) lineChart($("#pfb"), [{l: "Buy candidates", col: "var(--g-line)", path: money(v.path.own)}, {l: "Similar biotechs", col: "var(--idx)", path: money(v.path.ctrl), dash: true},
      {l: "Biotech index (XBI)", col: "var(--blue)", path: money(v.path.xbi), dash: true}], n, xl(n),
      {lo: v => Math.floor(Math.min(10000, v) / 2000) * 2000, hi: v => Math.ceil(Math.max(10000, v) / 2000) * 2000, zero: 10000,
       ticks: (lo, hi) => { const st = (hi - lo) > 16000 ? 4000 : 2000, o = []; for (let t = lo; t <= hi; t += st) o.push(t); return o; },
       tick: t => "$" + (t / 1000) + "k", end: v => usd(v), at: j => `Day ${steps[j]}`}); };
  if (ys.length) { $("#py").onchange = draw; draw(); }
}

function median(a){ if (!a.length) return 0; const s = [...a].sort((x,y)=>x-y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m-1]+s[m]) / 2; }
function hash(s){ let h = 2166136261; for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619); return ((h >>> 0) % 1000) / 1000; }

function route(){
  hideTip();
  let h = (location.hash || "#/").slice(2);
  if (!/^(crash|slide)(\/|$)/.test(h)) h = "crash/" + h;
  const [kind, ...rest] = h.split("/"), view = rest[0] || "screener";
  K = kind;
  document.querySelectorAll(".kind button").forEach(b => b.classList.toggle("on", b.dataset.kind === K));
  document.querySelectorAll("nav a").forEach(a => { a.href = `${base()}${a.dataset.nav === "screener" ? "" : "/" + a.dataset.nav}`;
    a.classList.toggle("on", a.dataset.nav === (view === "c" ? "screener" : view)); });
  document.title = KIND[K].label;
  if (view === "c") viewOne(decodeURIComponent(rest.slice(1).join("/")));
  else if (view === "track") viewTrack();
  else viewTable(view === "backtest" ? "backtest" : "screener");
  scrollTo(0, 0);
}
fetch("data.json", {cache: "no-store"}).then(r => r.json()).then(d => {
  D = d; Object.values(D.kinds).forEach(k => k.items.sort((a,b) => b.date.localeCompare(a.date)));
  document.querySelectorAll(".kind button").forEach(b => b.onclick = () => { location.hash = `#/${b.dataset.kind}`; });
  $("#foot").textContent = `Updated ${d.updated}. Data: SEC EDGAR (filings, XBRL, Form 4, 13F), FINRA short interest and prices via Massive, ClinicalTrials.gov, Twelve Data, Tiingo. Not investment advice.`;
  addEventListener("hashchange", route); route();
}).catch(() => $("#app").innerHTML = `<p class="empty">Could not load data.</p>`);
