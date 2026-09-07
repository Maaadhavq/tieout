/* Tie-Out evidence viewer.
   One page, three panes: the fact ledger, the comparability trace for the
   selected relationship, and the source evidence with the quote highlighted
   on the rendered PDF page. */

const LABELS = [
  ["CORROBORATES", "Corroborates", "corroborates"],
  ["CONTRADICTS", "Contradicts", "contradicts"],
  ["CONTEXTUALLY_RECONCILED", "Reconciled", "reconciled"],
  ["INSUFFICIENT_EVIDENCE", "Insufficient", "insufficient"],
];
const HUMAN_DIM = {
  consolidation: "consolidation basis", vintage: "data vintage",
  price_basis: "price basis", valuation: "valuation convention",
  measure: "measure", geography: "geography", adjustment: "adjustment",
  period: "reporting period", unit: "unit", metric: "metric", value: "value",
};

// The ledger is a scrolling list, not a virtualised grid. A real corpus runs to
// thousands of facts, so render a window of them and say so rather than
// building ten thousand rows and stalling the tab.
const PAGE = 300;

const state = { facts: [], rels: [], label: null, selected: null, stats: null,
                q: "", shown: PAGE };

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function toast(msg, ms = 4200) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

/* ------------------------------------------------------------------ load */
async function load() {
  const [stats, facts, rels] = await Promise.all([
    api("/api/stats"), api("/api/facts?limit=2000"), api("/api/relationships?limit=2000"),
  ]);
  Object.assign(state, { stats, facts, rels });
  renderCorpus();
  renderFilters();
  renderRows();
  if (!state.selected && rels.length) selectRel(pickHighlight(rels));
  else renderReasoning();
}

/** Open on the most instructive relationship rather than the first row.
    Within reconciliations, prefer a dimension a reader could NOT have spotted
    themselves: a vintage or consolidation difference is a finding, whereas two
    different quarters is mostly just a time series. */
const INTERESTING = ["vintage", "consolidation", "measure", "sign_convention",
                     "price_basis", "valuation"];

function pickHighlight(rels) {
  const crossDoc = (r) => r.a_doc !== r.b_doc;
  const best = (list) => list.sort((a, b) => b.confidence - a.confidence)[0];

  const gem = best(rels.filter((r) => r.label === "CONTEXTUALLY_RECONCILED"
                                   && INTERESTING.includes(r.dimension)));
  if (gem) return gem;

  for (const label of ["CONTRADICTS", "CORROBORATES", "CONTEXTUALLY_RECONCILED",
                       "INSUFFICIENT_EVIDENCE"]) {
    const hit = best(rels.filter((r) => r.label === label && crossDoc(r)))
             || best(rels.filter((r) => r.label === label));
    if (hit) return hit;
  }
  return rels[0];
}

/* ------------------------------------------------------------- top of page */
function renderCorpus() {
  const s = state.stats;
  $("#corpus").innerHTML = "";
  const bits = [
    `${s.documents} document${s.documents === 1 ? "" : "s"}`,
    `${s.pages_extracted}/${s.pages_total} pages read`,
    `${s.facts_kept} facts`,
    `${s.relationships} relationships`,
  ];
  if (s.is_reference_set) bits.push("reference set");
  else if (s.offline) bits.push("cached · no API calls");
  bits.forEach((b) => $("#corpus").appendChild(el("span", null, b)));
}

function renderFilters() {
  const counts = state.stats.relationships_by_label || {};
  const box = $("#filters");
  box.innerHTML = "";
  LABELS.forEach(([key, name]) => {
    const b = el("button", "pill");
    b.dataset.label = key;
    b.setAttribute("aria-pressed", state.label === key);
    b.append(el("span", "dot"), el("span", null, name),
             el("span", "n", String(counts[key] || 0)));
    b.onclick = () => {
      state.label = state.label === key ? null : key;
      state.shown = PAGE;
      renderFilters(); renderRows();
    };
    box.appendChild(b);
  });
}

/* ---------------------------------------------------------------- ledger */
function visibleFacts() {
  let facts = state.facts;
  if (state.label) {
    const ids = new Set();
    state.rels.filter((r) => r.label === state.label)
      .forEach((r) => { ids.add(r.fact_a); ids.add(r.fact_b); });
    facts = facts.filter((f) => ids.has(f.fact_id));
  }
  if (state.q) {
    const q = state.q.toLowerCase();
    facts = facts.filter((f) =>
      (f.entity_raw + " " + f.metric_raw + " " + (f.quote || "")).toLowerCase().includes(q));
  }
  return facts;
}

function badge(f) {
  const basis = JSON.parse(f.basis_json || "{}");
  const v = basis.vintage || basis.consolidation || basis.measure || basis.price_basis;
  if (!v) return null;
  const t = el("span", "tag", String(v).replace(/_/g, " "));
  if (basis.vintage && basis.vintage !== "realized") t.classList.add("vintage");
  return t;
}

function renderRows() {
  const box = $("#rows");
  box.innerHTML = "";
  const all = visibleFacts();
  const facts = all.slice(0, state.shown);
  const scope = state.label
    ? `in ${LABELS.find((l) => l[0] === state.label)[1].toLowerCase()} relationships`
    : "";
  $("#ledger-note").textContent = facts.length < all.length
    ? `showing ${facts.length} of ${all.length} facts ${scope}`.trim()
    : `${all.length} facts ${scope}`.trim();

  if (!all.length) {
    box.appendChild(el("div", "empty", "No facts match this filter."));
  }

  facts.forEach((f, i) => {
    const row = el("div", "row");
    row.setAttribute("aria-selected",
      state.selected && (state.selected.fact_a === f.fact_id || state.selected.fact_b === f.fact_id));
    row.append(el("div", "n", String(i + 1)), el("div", "ent", f.entity_raw));
    const m = el("div", "metric");
    m.appendChild(el("span", null, f.metric_raw));
    const b = badge(f);
    if (b) m.appendChild(b);
    row.appendChild(m);
    const val = el("div", "value" + (f.value_raw ? "" : " text"),
      f.value_raw ? `${f.value_raw}${unitSuffix(f)}` : (f.value_text || "—"));
    if (!f.value_raw && f.value_text) val.title = f.value_text;
    row.append(
      val,
      el("div", "period", f.period_raw || "—"),
      el("div", "src", `${shortDoc(f.filename)}·p${f.page_no}`),
    );
    row.onclick = () => selectFact(f);
    box.appendChild(row);
  });

  if (facts.length < all.length) {
    const more = el("button", "more", `show ${Math.min(PAGE, all.length - facts.length)} more`);
    more.onclick = () => { state.shown += PAGE; renderRows(); };
    box.appendChild(more);
  }

  const s = state.stats;
  // A hallucination rate over hand-labelled facts would be trivially zero, so
  // the reference corpus says what it is instead of quoting a flattering number.
  $("#ledger-foot").innerHTML = s.is_reference_set
    ? `<span>${s.facts_kept} facts, all grounded</span>` +
      `<span class="spacer" style="flex:1"></span>` +
      `<span>hand-labelled reference set — not extraction output</span>`
    : `<span>${s.facts_kept} facts kept</span>` +
      `<span class="bad">${s.facts_refused ?? s.facts_rejected} refused, ` +
      `${s.ungrounded} of them ungrounded</span>` +
      `<span class="spacer" style="flex:1"></span>` +
      `<span>hallucination rate ${(s.hallucination_rate * 100).toFixed(1)}%</span>`;
}

const unitSuffix = (f) => {
  const u = (f.unit_raw || "").trim();
  if (!u) return "";
  if (/^%|per\s*cent|percent/i.test(u)) return "%";
  return " " + u.replace(/^(rs\.?|inr|₹)\s*/i, "").trim();
};
const shortDoc = (n) => String(n || "").replace(/\.pdf$/i, "")
  .replace(/^\d+-/, "").split("-").slice(0, 2).join("-").slice(0, 14);

/* --------------------------------------------------------------- selection */
function selectFact(f) {
  const rel = state.rels
    .filter((r) => r.fact_a === f.fact_id || r.fact_b === f.fact_id)
    .filter((r) => !state.label || r.label === state.label)
    .sort((a, b) => b.confidence - a.confidence)[0];
  if (rel) return selectRel(rel);
  state.selected = { single: f };
  renderRows();
  renderSingleFact(f);
}

async function selectRel(rel) {
  state.selected = rel;
  renderRows();
  renderReasoning();
}

/* --------------------------------------------------------------- reasoning */
function cvar(label) {
  return { CORROBORATES: "var(--corroborates)", CONTRADICTS: "var(--contradicts)",
           CONTEXTUALLY_RECONCILED: "var(--reconciled)",
           INSUFFICIENT_EVIDENCE: "var(--insufficient)" }[label] || "var(--insufficient)";
}

function factSummary(r, side) {
  const basis = JSON.parse(r[`${side}_basis`] || "{}");
  const bits = [r.entity_raw, side === "b" ? r.b_metric : r.metric_raw];
  if (r[`${side}_period`]) bits.push(r[`${side}_period`]);
  Object.values(basis).forEach((v) => bits.push(String(v).replace(/_/g, " ")));
  return bits.join(" · ");
}

function renderSingleFact(f) {
  $("#reasoning").innerHTML =
    `<div class="section-head" style="padding:0 0 10px"><h2>Fact</h2>
       <span class="note">no relationship found for this fact</span></div>
     <div class="fact-card"><span class="k">A</span>
       <span>${esc(f.entity_raw)} · ${esc(f.metric_raw)}${f.period_raw ? " · " + esc(f.period_raw) : ""}</span>
       <span class="v">${esc(f.value_raw || f.value_text || "—")}</span></div>
     <p class="prose">This fact is grounded in its source but has no counterpart in
       the corpus to compare against — no other document states the same quantity for
       a comparable period.</p>`;
  renderEvidence([{ doc_id: f.doc_id, page: f.page_no, file: f.filename,
                    quote: f.quote, bbox: f.bbox_json, key: "A" }], "INSUFFICIENT_EVIDENCE");
}

function renderReasoning() {
  const r = state.selected;
  if (!r) {
    $("#reasoning").innerHTML = `<div class="empty"><strong>Nothing selected</strong>
      Pick a fact on the left to see how it was compared.</div>`;
    $("#evidence").innerHTML = "";
    return;
  }
  if (r.single) return renderSingleFact(r.single);

  const c = cvar(r.label);
  const trace = JSON.parse(r.rule_trace_json || "[]");
  const nDiff = trace.filter((t) => t.status === "differs").length;

  const wrap = $("#reasoning");
  wrap.style.setProperty("--c", c);
  wrap.innerHTML = `
    <div class="section-head" style="padding:0 0 10px">
      <h2>Relationship</h2>
      <span class="note">${esc(r.rel_id)} · decided by ${esc(r.decided_by)}</span>
    </div>
    <div class="fact-card"><span class="k">A</span>
      <span>${esc(factSummary(r, "a"))}</span>
      <span class="v">${esc(r.a_value || r.a_text || "—")}${r.a_value ? esc(shortUnit(r.a_unit)) : ""}</span></div>
    <div class="fact-card b" style="--c:${c}"><span class="k">B</span>
      <span>${esc(factSummary(r, "b"))}</span>
      <span class="v">${esc(r.b_value || r.b_text || "—")}${r.b_value ? esc(shortUnit(r.b_unit)) : ""}</span></div>

    <div class="trace">
      <div class="trace-head">Comparability trace
        <span class="sub">${trace.length} dimensions · ${r.decided_by === "llm" ? "1 adjudication call" : "0 model calls"}</span>
      </div>
      ${trace.map((t) => checkRow(t, c)).join("")}
    </div>

    <div class="verdict" style="--c:${c}">
      <div>
        <div class="label">${esc(r.label.replace(/_/g, " "))}</div>
        ${r.dimension ? `<div class="dim">dimension: ${esc(HUMAN_DIM[r.dimension] || r.dimension)}</div>` : ""}
      </div>
      <div style="flex:1"></div>
      <div><div class="conf">${Number(r.confidence).toFixed(2)}</div>
           <div class="conf-l">confidence</div></div>
    </div>
    <p class="prose">${esc(r.explanation)}</p>`;

  renderEvidence([
    { doc_id: null, page: r.a_page, file: r.a_doc, fact: r.fact_a, key: "A" },
    { doc_id: null, page: r.b_page, file: r.b_doc, fact: r.fact_b, key: "B" },
  ], r.label);
}

const shortUnit = (u) => {
  const s = (u || "").trim();
  if (!s) return "";
  if (/^%|per\s*cent|percent/i.test(s)) return "%";
  return " " + s;
};

const ICON = {
  same: '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="var(--corroborates)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 7.5l3 3 6-7"/></svg>',
  differs: (c) => `<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"><path d="M3.5 3.5l7 7M10.5 3.5l-7 7"/></svg>`,
  unknown: '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="var(--muted)" stroke-width="2" stroke-linecap="round"><path d="M4.8 5a2.2 2.2 0 1 1 2.4 2.4V9"/><circle cx="7" cy="11.4" r=".9" fill="var(--muted)" stroke="none"/></svg>',
  skipped: '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="var(--faint)" stroke-width="2" stroke-linecap="round"><path d="M3.5 7h7"/></svg>',
};

function checkRow(t, c) {
  const icon = t.status === "same" ? ICON.same
    : t.status === "differs" ? ICON.differs(c)
    : t.status === "unknown" ? ICON.unknown : ICON.skipped;
  return `<div class="check ${esc(t.status)}" style="--c:${c}">
    <span>${icon}</span>
    <span class="dim">${esc(t.dimension)}</span>
    <span>${esc(t.detail)}</span></div>`;
}

/* ---------------------------------------------------------------- evidence */
async function renderEvidence(sides, label) {
  const box = $("#evidence");
  box.style.setProperty("--c", cvar(label));
  box.innerHTML = `
    <div class="section-head" style="padding:0 0 9px">
      <h2>Source evidence</h2>
      <span class="note">every quote below was located in the PDF before the fact was stored</span>
    </div>
    <div class="ev-grid" id="ev-grid"></div>`;

  const grid = $("#ev-grid");
  for (const s of sides) {
    const card = el("div", "ev" + (s.key === "B" ? " b" : ""));
    card.innerHTML = `<div class="ev-head">
        <span style="font-weight:600">${s.key}</span>
        <span class="file">${esc(s.file || "")}</span>
        <button class="ev-toggle">page image</button>
        <span>p.${s.page}</span>
      </div><div class="ev-quote">loading…</div>`;
    grid.appendChild(card);

    let data = s;
    if (s.fact) {
      try { data = { ...s, ...(await api(`/api/facts/${s.fact}`)) }; }
      catch { /* fall through to what we have */ }
    }
    const quote = data.quote || "";
    card.querySelector(".ev-quote").innerHTML = quote
      ? `<mark>${esc(quote)}</mark>`
      : '<span style="color:var(--faint)">quote unavailable</span>';

    card.querySelector(".ev-toggle").onclick = () =>
      togglePage(card, data.doc_id, data.page_no || s.page, data.bbox_json);
  }
}

/** The highlight: the rendered page plus one absolutely-positioned box per
    line of the located quote, scaled from PDF points to rendered pixels. */
async function togglePage(card, docId, page, bboxJson) {
  const existing = card.querySelector(".page-wrap");
  if (existing) { existing.remove(); return; }
  if (!docId) return toast("Source PDF path not recorded for this document.");

  const wrap = el("div", "page-wrap");
  const img = new Image();
  img.src = `/api/pages/${docId}/${page}.png`;
  wrap.appendChild(img);
  card.appendChild(wrap);

  img.onerror = () => { wrap.remove(); toast("Could not render that page."); };
  img.onload = () => {
    let boxes = [];
    try { boxes = JSON.parse(bboxJson || "[]"); } catch { /* none */ }
    if (!boxes.length) return;
    // Boxes are in PDF points; the image is rendered at RENDER_DPI. Scale by
    // the ratio of the displayed width to the page width in points.
    const scale = img.clientWidth / (img.naturalWidth / (132 / 72));
    boxes.forEach(([x0, y0, x1, y1]) => {
      const hl = el("div", "hl");
      hl.style.left = `${x0 * scale}px`;
      hl.style.top = `${y0 * scale}px`;
      hl.style.width = `${(x1 - x0) * scale}px`;
      hl.style.height = `${(y1 - y0) * scale}px`;
      wrap.appendChild(hl);
    });
    wrap.scrollTop = Math.max(0, boxes[0][1] * scale - 120);
  };
}

/* ------------------------------------------------------------------ upload */
$("#upload-btn").onclick = () => $("#upload-input").click();
$("#upload-input").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const btn = $("#upload-btn");
  btn.disabled = true;
  btn.textContent = "Reading…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const r = await api("/api/documents", { method: "POST", body: fd });
    toast(r.already_ingested
      ? `${r.filename} was already in the layer — nothing recomputed.`
      : `${r.filename}: ${r.facts_kept} facts kept, ${r.facts_rejected} refused ` +
        `(${((r.hallucination_rate ?? 0) * 100).toFixed(0)}% ungrounded), ` +
        `${r.relationships_added} new relationships — nothing existing recomputed.`, 8000);
    await load();
  } catch (err) {
    toast(`Upload failed: ${err.message}`, 7000);
  } finally {
    btn.disabled = false;
    btn.textContent = "Add PDF";
    e.target.value = "";
  }
};

$("#search").oninput = (e) => {
  state.q = e.target.value.trim();
  state.shown = PAGE;
  renderRows();
};

load().catch((e) => {
  $("#rows").innerHTML = `<div class="empty"><strong>Could not load</strong>${esc(e.message)}</div>`;
});
