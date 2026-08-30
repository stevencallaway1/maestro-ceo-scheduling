/* Maestro UI - vanilla JS, zero external dependencies. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const api = async (path, opts) => {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
};
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#panel-${tab.dataset.panel}`).classList.add("active");
    if (tab.dataset.panel === "brief") loadBrief();
    if (tab.dataset.panel === "trust") loadTrust();
  });
});

/* ---------- toast ---------- */
let toastTimer;
function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("bad", bad);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 4200);
}

/* =====================================================================
   Panel 1 - Request Pipeline
   ===================================================================== */
const STAGES = [
  { key: "request", title: "Intake", desc: "Parse the raw request into a structured object" },
  { key: "dossier", title: "Context Dossier", desc: "Who is this to Zeb? No decision without a dossier" },
  { key: "policy", title: "Policy Engine", desc: "Which rules fire, and which one decides" },
  { key: "decision", title: "Decision", desc: "Outcome, slots, and the written rationale" },
  { key: "draft", title: "Draft", desc: "CEO-voice reply - never auto-sent" },
];

let selectedRequest = null;
let running = false;

async function loadInbox() {
  const requests = await api("/api/requests");
  $("#inbox-count").textContent = `${requests.length} pending`;
  $("#inbox-list").innerHTML = requests
    .map(
      (r) => `
    <div class="req-card" data-id="${r.id}">
      <div class="who"><span>${esc(r.from_name)}</span><span class="chip">${esc(r.channel)}</span></div>
      <div class="subj">${esc(r.subject)}</div>
      <div class="meta"><span class="chip accent">${esc(r.from_email.split("@")[1])}</span></div>
    </div>`
    )
    .join("");
  document.querySelectorAll(".req-card").forEach((card) => {
    card.addEventListener("click", () => selectRequest(card.dataset.id, requests));
  });
}

function selectRequest(id, requests) {
  if (running) return;
  selectedRequest = requests.find((r) => r.id === id);
  document.querySelectorAll(".req-card").forEach((c) =>
    c.classList.toggle("selected", c.dataset.id === id));
  $("#flow-title").textContent = selectedRequest.subject;
  $("#flow-sub").textContent = `${selectedRequest.from_name} · via ${selectedRequest.channel}`;
  $("#run-btn").disabled = false;
  $("#lockout-banner").classList.add("hidden");
  renderEmptyStages();
}

function renderEmptyStages() {
  $("#stages").innerHTML = STAGES.map(
    (s, i) => `
    <div class="stage" id="stage-${s.key}">
      <div class="stage-head">
        <div class="stage-num">${i + 1}</div>
        <div class="stage-title">${s.title}</div>
        <div class="stage-status">${s.desc}</div>
      </div>
      <div class="stage-body"></div>
    </div>`
  ).join("");
  document.querySelectorAll(".stage-head").forEach((head) => {
    head.addEventListener("click", () => head.parentElement.classList.toggle("open"));
  });
}

$("#run-btn").addEventListener("click", async () => {
  if (!selectedRequest || running) return;
  running = true;
  $("#run-btn").disabled = true;
  $("#lockout-banner").classList.add("hidden");
  renderEmptyStages();
  const instant = $("#instant-toggle").checked;
  const delay = instant ? 0 : 800;
  try {
    const result = await api(`/api/pipeline/run/${selectedRequest.id}`, { method: "POST" });
    for (let i = 0; i < STAGES.length; i++) {
      const stage = $(`#stage-${STAGES[i].key}`);
      stage.classList.add("running");
      if (delay) await new Promise((r) => setTimeout(r, delay));
      // The lockout banner drops the moment policy resolves.
      if (STAGES[i].key === "decision" && result.banner.sensitive_lockout) {
        const b = $("#lockout-banner");
        b.textContent = result.banner.text;
        b.classList.remove("hidden");
      }
      fillStage(STAGES[i].key, result);
      stage.classList.remove("running");
      stage.classList.add("done", "open");
    }
    toast("Pipeline complete - draft queued for approval (see Daily Brief).");
  } catch (err) {
    toast(`Pipeline error: ${err.message}`, true);
  } finally {
    running = false;
    $("#run-btn").disabled = false;
  }
});

function fillStage(key, result) {
  const body = $(`#stage-${key} .stage-body`);
  const status = $(`#stage-${key} .stage-status`);
  if (key === "request") {
    const r = result.request;
    status.textContent = `${r.meeting_type} · ${r.requested_duration_minutes} min · urgency ${r.urgency}`;
    body.innerHTML = `
      <dl class="kv">
        <dt>Requester</dt><dd>${esc(r.requester.name)} - ${esc(r.requester.role)}${r.requester.role.includes(r.requester.org) ? "" : ", " + esc(r.requester.org)}
          <span class="chip ${r.requester.internal ? "good" : "warn"}">${r.requester.internal ? "internal" : "external"}</span></dd>
        <dt>Meeting type</dt><dd><span class="chip accent">${esc(r.meeting_type)}</span></dd>
        <dt>Duration</dt><dd>${r.requested_duration_minutes} minutes</dd>
        <dt>Urgency</dt><dd>${esc(r.urgency)} <span class="muted small">(${esc(r.urgency_signal)})</span></dd>
        ${r.proposed_start ? `<dt>Proposed time</dt><dd>${esc(r.proposed_start)}</dd>` : ""}
        <dt>Channel</dt><dd>${esc(r.channel)}</dd>
      </dl>
      <div class="raw">${esc(r.raw_source_text)}</div>`;
  }
  if (key === "dossier") {
    const d = result.dossier;
    status.textContent = `relevance ${d.strategic_relevance}/100${d.vip ? " · VIP" : ""}${d.sensitive_category ? " · SENSITIVE" : ""}`;
    body.innerHTML = `
      <p style="font-size:14px">${esc(d.relationship_summary)}</p>
      <div class="dossier-section">
        <h4>Strategic relevance</h4>
        <div class="meter">
          <div class="meter-track"><div class="meter-fill" style="width:${d.strategic_relevance}%"></div></div>
          <div class="meter-val">${d.strategic_relevance}/100</div>
        </div>
        <p class="muted small">${esc(d.relevance_justification)}</p>
      </div>
      <div class="dossier-section">
        <h4>Flags</h4>
        ${d.vip ? '<span class="chip accent">VIP</span> ' : ""}
        ${d.sensitive_category ? `<span class="chip bad">sensitive: ${esc(d.sensitive_category)}</span> ` : ""}
        <span class="chip">${esc(d.timezone)}</span>
        <span class="chip">${d.known_person ? `${d.interaction_count} interactions on file` : "unknown sender"}</span>
      </div>
      ${d.last_interactions.length ? `
      <div class="dossier-section">
        <h4>Last interactions</h4>
        ${d.last_interactions.map((i) => `
          <div class="int-row"><div class="d">${esc(i.date)}</div>
          <div>${esc(i.topic)}<div class="o">→ ${esc(i.outcome)}</div></div></div>`).join("")}
      </div>` : ""}
      ${d.open_threads.length ? `
      <div class="dossier-section">
        <h4>Open threads &amp; commitments</h4>
        <ul class="thread-list">${d.open_threads.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
      </div>` : ""}`;
  }
  if (key === "policy") {
    const p = result.policy;
    status.textContent = `${p.fired_rules.length} rules fired · decided by ${p.deciding_rule_id}`;
    body.innerHTML = p.fired_rules
      .map(
        (r) => `
      <div class="rule ${r.decisive ? "decisive" : ""}">
        <span class="rid">${esc(r.id)}</span>
        <span class="rtext">${esc(r.plain_english)}</span>
        <span class="raction ${r.decisive ? "decided" : "constraint"}">${r.decisive ? "→ " + esc(r.action) : esc(r.action)}</span>
      </div>`
      )
      .join("") +
      `<p class="muted small" style="margin-top:8px">Evaluated in priority order. The first decisive match sets the outcome; "constrain" rules shape slot selection.</p>`;
  }
  if (key === "decision") {
    const d = result.decision;
    status.textContent = d.outcome.replace(/_/g, " ");
    body.innerHTML = `
      <div class="outcome-line">
        <span class="outcome-badge ${d.outcome}">${d.outcome.replace(/_/g, " ")}</span>
        <span class="chip">${esc(d.trust_level)}</span>
        <span class="muted small">${esc(d.trust_note)}</span>
      </div>
      <div class="rationale"><span class="lbl">Rationale</span>${esc(d.rationale)}</div>
      ${d.delegate_to ? `<p style="font-size:13.5px"><strong>Delegated to:</strong> ${esc(d.delegate_to)}</p>` : ""}
      ${d.route_to.length ? `<p style="font-size:13.5px"><strong>Routed to:</strong> ${esc(d.route_to.join(", "))}</p>` : ""}
      ${d.proposed_slots.length ? `
        <div class="dossier-section"><h4>Proposed slots (requester-local shown first)</h4>
        ${d.proposed_slots.map((s) => `
          <div class="slot"><span class="req-tz">${esc(s.requester_local)}</span>
          <span class="ceo-tz">${esc(s.ceo_local)} for Zeb</span></div>`).join("")}</div>` : ""}`;
  }
  if (key === "draft") {
    const dr = result.draft;
    const internal = dr.kind === "internal_note";
    status.textContent = internal ? "internal routing note" : "external reply, queued";
    body.innerHTML = `
      <div class="email ${internal ? "internal" : ""}">
        <div class="email-head">
          <div><span class="lbl">To</span> ${esc(dr.to)}</div>
          <div><span class="lbl">Subject</span> ${esc(dr.subject)}</div>
        </div>
        <div class="email-body">${esc(dr.body)}</div>
        <div class="email-foot">✋ Never auto-sent - waiting in the approval queue as <strong>${esc(result.approval_id)}</strong>. Approve or override it on the Daily Brief panel.</div>
      </div>`;
  }
}

/* =====================================================================
   Panel 2 - Daily Brief
   ===================================================================== */
function renderMarkdown(md) {
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>");
  const out = [];
  let inList = false;
  for (const line of md.split("\n")) {
    if (line.startsWith("- ") || line.startsWith("  - ")) {
      if (!inList) { out.push("<ul>"); inList = true; }
      const sub = line.startsWith("  - ");
      out.push(`<li class="${sub ? "sub" : ""}">${inline(line.replace(/^\s*- /, ""))}</li>`);
      continue;
    }
    if (inList) { out.push("</ul>"); inList = false; }
    if (line.startsWith("## ")) out.push(`<h2>${inline(line.slice(3))}</h2>`);
    else if (line.startsWith("# ")) out.push(`<h1>${inline(line.slice(2))}</h1>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (inList) out.push("</ul>");
  return out.join("\n");
}

async function loadBrief() {
  const data = await api("/api/brief");
  $("#brief-md").innerHTML = renderMarkdown(data.markdown);
  renderApprovals(data.pending_approvals);
}

function renderApprovals(pending) {
  const el = $("#approval-list");
  if (!pending.length) {
    el.innerHTML = '<p class="muted small" style="margin-top:12px">Queue is clear. Run a request through the pipeline to add one.</p>';
    return;
  }
  el.innerHTML = pending
    .map(
      (a) => `
    <div class="approval" data-id="${a.id}">
      <div class="a-head"><span class="a-req">${esc(a.requester)}</span><span class="chip">${esc(a.category)}</span></div>
      <div class="a-sum">${esc(a.summary)}</div>
      <div class="a-rat">${esc(a.rationale)}</div>
      <div class="a-actions">
        <button class="btn small primary act-approve">Approve</button>
        <button class="btn small danger act-override">Override</button>
      </div>
      <div class="override-box hidden">
        <input type="text" placeholder="One-line reason (required - this trains the trust ladder)" />
        <button class="btn small danger act-confirm">Confirm</button>
      </div>
    </div>`
    )
    .join("");

  el.querySelectorAll(".approval").forEach((card) => {
    const id = card.dataset.id;
    $(".act-approve", card).addEventListener("click", async () => {
      await api(`/api/approvals/${id}/approve`, { method: "POST" });
      toast("Approved. Recorded in overrides.json - acceptance metrics update on the Trust panel.");
      loadBrief();
    });
    $(".act-override", card).addEventListener("click", () => {
      $(".override-box", card).classList.toggle("hidden");
      $("input", card).focus();
    });
    const confirm = async () => {
      const reason = $("input", card).value.trim();
      if (!reason) { $("input", card).focus(); return; }
      const res = await api(`/api/approvals/${id}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (res.demotion) {
        toast(`Critical miss - automatic demotion: ${res.approval.category} ${res.demotion.from} → ${res.demotion.to}. See Trust panel.`, true);
      } else if (res.critical_miss) {
        toast("Override recorded as a critical miss. Trust metrics updated.", true);
      } else {
        toast("Override recorded - the eval loop learns from this on the Trust panel.");
      }
      loadBrief();
    };
    $(".act-confirm", card).addEventListener("click", confirm);
    $("input", card).addEventListener("keydown", (e) => { if (e.key === "Enter") confirm(); });
  });
}

/* =====================================================================
   Panel 3 - Trust & Audit
   ===================================================================== */
const CAT_LABEL = {
  internal_team: "Internal team", exec_1on1: "Exec 1:1s", external_partner: "External partners",
  vendor: "Vendors", investor: "Investors", press: "Press", board: "Board", personal: "Personal",
};
const LEVELS = ["L0", "L1", "L2", "L3"];

async function loadTrust() {
  const [report, auditLog] = await Promise.all([api("/api/trust"), api("/api/audit")]);
  const m = report.metrics.overall;
  $("#stat-row").innerHTML = `
    <div class="stat"><div class="v">${(m.rate * 100).toFixed(1)}%</div>
      <div class="l">Suggestion acceptance · rolling ${report.metrics.window_days}d</div></div>
    <div class="stat"><div class="v">${m.samples}</div>
      <div class="l">Decisions reviewed in window</div></div>
    <div class="stat ${m.critical_misses ? "bad" : ""}"><div class="v">${m.critical_misses}</div>
      <div class="l">Critical misses (auto-demotion events)</div></div>`;

  $("#ladder").innerHTML = report.ladder
    .map((row) => {
      const cat = report.trust_state.categories[row.category] || {};
      const hist = (cat.history || []).slice().reverse();
      return `
      <div class="ladder-row">
        <div class="ladder-top">
          <div class="ladder-cat">${CAT_LABEL[row.category] || row.category}</div>
          <div class="lvl-steps">${LEVELS.map((l) =>
            `<span class="lvl ${LEVELS.indexOf(l) <= LEVELS.indexOf(row.level) ? "on" : ""}">${l}</span>`).join("")}</div>
          <div class="spacer"></div>
          ${row.locked ? '<span class="lock-chip">🔒 HARD-LOCKED L0</span>' : ""}
        </div>
        <div class="ladder-progress">
          <div class="meter-track"><div class="meter-fill" style="width:${row.progress_pct}%"></div></div>
          <span class="ladder-reason">${esc(row.reason)}</span>
        </div>
        ${hist.length ? `
        <details class="ladder-hist"><summary>Promotion / demotion history (${hist.length})</summary>
          <ul>${hist.map((h) =>
            `<li class="${h.reason.includes("DEMOTION") ? "demote" : ""}">${esc(h.date)}: ${esc(h.from)} → ${esc(h.to)} - ${esc(h.reason)}</li>`).join("")}</ul>
        </details>` : ""}
      </div>`;
    })
    .join("");

  $("#override-feed").innerHTML = report.recent_overrides
    .map(
      (o) => `
    <div class="verdict">
      <span class="vd">${esc(o.date)}</span>
      <span class="vs">${esc(o.summary)} <span class="chip">${esc(o.category)}</span>
        ${o.reason ? `<div class="vr">"${esc(o.reason)}"</div>` : ""}</span>
      <span class="va ${o.human_action === "approved" ? "ok" : "no"}">${esc(o.human_action)}${o.critical_miss ? " ⚠" : ""}</span>
    </div>`
    )
    .join("");

  $("#audit-feed").innerHTML = auditLog
    .map(
      (a) => `
    <div class="audit-row">
      <span class="audit-ts">${esc(a.ts.replace("T", " ").slice(5, 16))}</span>
      <span class="audit-stage ${a.actor === "human" ? "human" : ""}">${esc(a.stage)}</span>
      <span class="audit-sum">${esc(a.summary)}</span>
    </div>`
    )
    .join("");
}

/* ---------- boot ---------- */
loadInbox();
renderEmptyStages();
