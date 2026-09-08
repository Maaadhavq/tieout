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

const state = { facts: [], rels: [], rejects: [], label: null, selected: null,
                stats: null, q: "", shown: PAGE, view: "facts" };

const REASON_TEXT = {
  quote_not_found:
    "The model gave a quote to support this, and that span is not on the page. " +
    "Most often it stitched a table row label onto a number from a different " +
    "column — text that reads as one phrase but is never contiguous in the " +
    "document. The claim was refused rather than stored.",
  unresolvable_entity:
    "Well grounded, but the entity names nothing on its own — filings say " +
    "\"our Company\" and \"the Group\". Blocking is keyed on the entity, so " +
    "keeping these would compare every filing's \"company\" facts against every " +
    "other filing's. Refused rather than guessed at.",
  unparseable_value:
    "A measurement whose value could not be read as a number, and with no " +
    "categorical value to fall back on.",
  no_quote: "The model returned no quote at all, so there was nothing to verify.",
  no_entity_or_metric: "The claim named no entity or no metric.",
};

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
  const [stats, facts, rels, rejects] = await Promise.all([
    api("/api/stats"), api("/api/facts?limit=2000"),
    api("/api/relationships?limit=2000"), api("/api/rejects?limit=2000"),
  ]);
  Object.assign(state, { stats, facts, rels, rejects });
  renderCorpus();
  renderFilters();
  renderRows();
  if (applyHash()) return;
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
  const box = $("#corpus");
  box.innerHTML = "";
  // Figure over label, rather than five counts run together in one line of
  // 11px grey. These are the numbers a reader checks first.
  const stats = [
    [s.documents, s.documents === 1 ? "document" : "documents"],
    [`${s.pages_extracted}/${s.pages_total}`, "pages read"],
    [Number(s.facts_kept).toLocaleString(), "facts"],
    [Number(s.relationships).toLocaleString(), "relationships"],
  ];
  stats.forEach(([value, label]) => {
    const stat = el("div", "stat");
    stat.append(el("span", "v", String(value)), el("span", "l", label));
    box.appendChild(stat);
  });
  const mode = s.is_reference_set ? "reference set"
    : s.offline ? "cached · no API calls" : "live extraction";
  box.appendChild(el("div", "mode" + (s.is_reference_set ? " ref" : ""), mode));
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
      state.view = "facts";
      state.label = state.label === key ? null : key;
      state.shown = PAGE;
      renderFilters(); renderRows();
    };
    box.appendChild(b);
  });

  // The fifth pill is the honest one: what the system refused to believe.
  // Without it the failure case only exists in the terminal.
  const refused = el("button", "pill refused");
  refused.setAttribute("aria-pressed", state.view === "refused");
  refused.title = "Claims the grounding gate would not accept";
  refused.append(el("span", "dot"), el("span", null, "Refused"),
                 el("span", "n", String(state.rejects.length)));
  refused.onclick = () => {
    state.view = state.view === "refused" ? "facts" : "refused";
    state.label = null;
    state.shown = PAGE;
    state.selected = null;
    renderFilters(); renderRows();
    if (state.view === "refused") {
      state.rejects.length ? selectReject(state.rejects[0]) : renderReasoning();
    } else renderReasoning();
  };
  box.appendChild(refused);
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
  if (state.view === "refused") return renderRejectRows(box);
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
    row.appendChild(el("div", "n", String(i + 1)));
    const main = el("div", "main");
    const l1 = el("div", "l1");
    l1.append(el("span", "ent", f.entity_raw), el("span", "met", f.metric_raw));
    const b = badge(f);
    if (b) l1.appendChild(b);
    const l2 = el("div", "l2");
    l2.append(el("span", "period", f.period_raw || "no period stated"),
              el("span", "src", `${shortDoc(f.filename)} · p.${f.page_no}`));
    main.append(l1, l2);
    row.appendChild(main);
    const val = el("div", "value" + (f.value_raw ? "" : " text"),
      f.value_raw ? `${f.value_raw}${unitSuffix(f)}` : (f.value_text || "—"));
    if (!f.value_raw && f.value_text) val.title = f.value_text;
    row.appendChild(val);
    row.onclick = () => selectFact(f);
    box.appendChild(row);
  });

  if (facts.length < all.length) {
    const more = el("button", "more", `show ${Math.min(PAGE, all.length - facts.length)} more`);
    more.onclick = () => { state.shown += PAGE; renderRows(); };
    box.appendChild(more);
  }

  // Export what is on screen, not the whole store: the filters are how a user
  // says which facts they actually want in the spreadsheet.
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  const link = $("#export-facts");
  if (link) {
    link.href = state.label
      ? `/api/export/relationships.csv?label=${encodeURIComponent(state.label)}`
      : `/api/export.csv${params.toString() ? "?" + params : ""}`;
    link.textContent = state.label ? "export these relationships" : "export CSV";
  }

  renderFoot();
}

function renderFoot() {
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

/** Refused claims, shown with what the model actually said. Keeping these is
    what makes the hallucination rate checkable rather than a number to trust. */
function renderRejectRows(box) {
  const link = $("#export-facts");
  if (link) {
    link.href = "/api/export/refused.csv";
    link.textContent = "export refusals";
  }
  const q = state.q.toLowerCase();
  const all = state.rejects.filter((r) =>
    !q || (r.payload_json + r.filename + r.reason).toLowerCase().includes(q));
  const rows = all.slice(0, state.shown);

  $("#ledger-note").textContent =
    `${all.length} claims the grounding gate refused` +
    (rows.length < all.length ? ` · showing ${rows.length}` : "");

  if (!all.length) {
    box.appendChild(el("div", "empty", "Nothing was refused in this corpus."));
    return;
  }

  rows.forEach((r, i) => {
    let p = {};
    try { p = JSON.parse(r.payload_json); } catch { /* keep going */ }
    const row = el("div", "row");
    row.setAttribute("aria-selected", state.selected?.reject_id === r.reject_id);
    row.appendChild(el("div", "n", String(i + 1)));
    const main = el("div", "main");
    const l1 = el("div", "l1");
    l1.append(el("span", "ent", p.entity || "—"), el("span", "met", p.metric || "—"),
              el("span", "tag reason", r.reason.replace(/_/g, " ")));
    const l2 = el("div", "l2");
    l2.append(el("span", "period", p.period || "no period stated"),
              el("span", "src", `${shortDoc(r.filename)} · p.${r.page_no}`));
    main.append(l1, l2);
    row.appendChild(main);
    row.appendChild(el("div", "value" + (p.value ? "" : " text"),
                       p.value || p.value_text || "—"));
    row.onclick = () => selectReject(r);
    box.appendChild(row);
  });

  if (rows.length < all.length) {
    const more = el("button", "more", `show ${Math.min(PAGE, all.length - rows.length)} more`);
    more.onclick = () => { state.shown += PAGE; renderRows(); };
    box.appendChild(more);
  }
  renderFoot();
}

function selectReject(r) {
  state.selected = r;
  renderRows();
  renderRefusal(r);
}

function renderRefusal(r) {
  let p = {};
  try { p = JSON.parse(r.payload_json); } catch { /* keep going */ }
  const wrap = $("#reasoning");
  wrap.style.setProperty("--c", "var(--contradicts)");
  const claimed = [p.entity, p.metric, p.period].filter(Boolean).join(" · ");
  const value = [p.value || p.value_text, p.unit].filter(Boolean).join(" ");

  wrap.innerHTML = `
    <div class="section-head" style="padding:0 0 10px">
      <h2>Refused claim</h2>
      <span class="note">${esc(r.reject_id)} · never entered the fact store</span>
    </div>
    <div class="fact-card b" style="--c:var(--contradicts)">
      <span class="k">!</span>
      <span>${esc(claimed) || "—"}</span>
      <span class="v">${esc(value) || "—"}</span></div>

    <div class="trace">
      <div class="trace-head">Why it was refused
        <span class="sub">${esc(r.reason.replace(/_/g, " "))}</span></div>
      <div class="check unknown" style="--c:var(--contradicts);grid-template-columns:18px 1fr">
        <span></span><span style="font-family:var(--sans)">${esc(REASON_TEXT[r.reason] || r.reason)}</span>
      </div>
    </div>

    ${p.quote ? `<div class="trace" style="margin-top:8px">
      <div class="trace-head">The quote it offered
        <span class="sub">searched for in ${esc(r.filename)} p.${r.page_no}, not found</span></div>
      <div class="check" style="grid-template-columns:1fr">
        <span style="font-family:var(--serif);font-size:12px;line-height:1.6">“${esc(p.quote)}”</span>
      </div></div>` : ""}

    <p class="prose">This is what the hallucination rate is made of. The model's
    output is kept exactly as it came back, so the number in the footer can be
    checked rather than taken on trust — open the page below and look for the
    quote yourself.</p>`;

  renderEvidence([{ doc_id: r.doc_id, page: r.page_no, file: r.filename,
                    quote: null, bbox: null, key: "!" }], "CONTRADICTS",
                 "the page the claim came from — the quote above is NOT on it");
}

const unitSuffix = (f) => {
  const u = (f.unit_raw || "").trim();
  if (!u) return "";
  if (/^%|per\s*cent|percent/i.test(u)) return "%";
  return " " + u.replace(/^(rs\.?|inr|₹)\s*/i, "").trim();
};
// Enough of the filename to tell the six documents apart. Two segments capped
// at 14 characters rendered "delhivery-pros" for both the prospectus and the
// annual report, so the provenance line named a document nobody could identify.
const shortDoc = (n) => String(n || "").replace(/\.pdf$/i, "")
  .replace(/^\d+-/, "").replace(/-excerpt$/, "")
  .split("-").slice(0, 3).join("-").slice(0, 28);

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
async function renderEvidence(sides, label, caption) {
  const box = $("#evidence");
  box.style.setProperty("--c", cvar(label));
  box.innerHTML = `
    <div class="section-head" style="padding:0 0 9px">
      <h2>Source evidence</h2>
      <span class="note">${esc(caption
        || "every quote below was located in the PDF before the fact was stored")}</span>
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
      : '<span style="color:var(--faint)">no located span — open the page image '
        + 'and search for the claimed quote yourself</span>';

    card.querySelector(".ev-toggle").onclick = () =>
      togglePage(card, data.doc_id, data.page_no || s.page, data.bbox_json);
  }
}

/** The highlight: the rendered page plus one absolutely-positioned box per
    line of the located quote, scaled from PDF points to rendered pixels. */
async function togglePage(card, docId, page, bboxJson) {
  const existing = card.querySelector(".page-wrap");
  if (existing) { existing.remove(); card.classList.remove("open"); return; }
  if (!docId) return toast("Source PDF path not recorded for this document.");

  // Widen the card to the full pane BEFORE the image loads: side by side each
  // page renders about 370px across, which is too small to read the sentence
  // the highlight is pointing at, and the scale factor below is measured from
  // the laid-out width so it has to be settled first.
  card.classList.add("open");

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
    // No key means the extractor can only replay cached pages, and a document
    // nobody has seen has none. Saying "0 facts kept" for that reads as a
    // broken system rather than an unconfigured one.
    const nothingRead = !r.already_ingested && r.offline && r.facts_kept === 0;
    toast(r.already_ingested
      ? `${r.filename} was already in the layer — nothing recomputed.`
      : nothingRead
      ? `${r.filename} was stored, but nothing was extracted from its ` +
        `${r.pages_total} page${r.pages_total === 1 ? "" : "s"}: this run has no ` +
        `GEMINI_API_KEY, so the extractor can only replay the committed cache. ` +
        `Put a key in .env and restart to read new documents. Everything already ` +
        `in the ledger is unaffected.`
      : `${r.filename}: ${r.facts_kept} facts kept, ${r.facts_rejected} refused ` +
        `(${((r.hallucination_rate ?? 0) * 100).toFixed(0)}% ungrounded), ` +
        `${r.relationships_added} new relationships — nothing existing recomputed.`,
      nothingRead ? 12000 : 8000);
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

/* ------------------------------------------------------------ deep links */
/* #refused, #contradicts, #reconciled, #corroborates, #insufficient.
   Being able to send someone a link straight to the contradiction is worth
   fifteen lines -- it also makes the README and the demo reproducible. */
const HASH_TO_LABEL = {
  corroborates: "CORROBORATES", contradicts: "CONTRADICTS",
  reconciled: "CONTEXTUALLY_RECONCILED", insufficient: "INSUFFICIENT_EVIDENCE",
};

function applyHash() {
  const key = (location.hash || "").replace("#", "").toLowerCase();
  if (!key) return false;
  state.shown = PAGE;
  if (key === "refused") {
    state.view = "refused";
    state.label = null;
    renderFilters(); renderRows();
    if (state.rejects.length) selectReject(state.rejects[0]);
    return true;
  }
  if (HASH_TO_LABEL[key]) {
    state.view = "facts";
    state.label = HASH_TO_LABEL[key];
    renderFilters(); renderRows();
    const hit = state.rels.filter((r) => r.label === state.label)
      .sort((a, b) => b.confidence - a.confidence)[0];
    if (hit) selectRel(hit);
    return true;
  }
  return false;
}

window.addEventListener("hashchange", applyHash);

load().catch((e) => {
  $("#rows").innerHTML = `<div class="empty"><strong>Could not load</strong>${esc(e.message)}</div>`;
});
