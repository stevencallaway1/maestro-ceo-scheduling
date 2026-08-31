/* Maestro UI - vanilla JS, zero external dependencies. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const api = async (path, opts) => {
  const res = await fetch(path, opts);
  if (!res.ok) {
    // The Critic gate answers with a reason. Show it rather than a status code.
    let detail = `${path}: ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail.message || body.detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(detail);
  }
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
  toastTimer = setTimeout(() => el.classList.add("hidden"), 4600);
}

/* ---------- "what runs for real" modal ---------- */
$("#mode-more").addEventListener("click", (e) => {
  e.preventDefault();
  $("#mode-modal").classList.remove("hidden");
});
$("#mode-close").addEventListener("click", () => $("#mode-modal").classList.add("hidden"));
$("#mode-modal").addEventListener("click", (e) => {
  if (e.target.id === "mode-modal") $("#mode-modal").classList.add("hidden");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#mode-modal").classList.add("hidden");
});

/* ---------- reset ---------- */
$("#reset-btn").addEventListener("click", async () => {
  const btn = $("#reset-btn");
  btn.disabled = true;
  try {
    await api("/api/reset", { method: "POST" });
    selectedRequest = null;
    $("#run-btn").disabled = true;
    $("#flow-title").textContent = "Select a request";
    $("#flow-sub").textContent = "Pick an item from the inbox, then run it through the pipeline.";
    $("#lockout-banner").classList.add("hidden");
    document.querySelectorAll(".req-card").forEach((c) => c.classList.remove("selected"));
    renderEmptyStages();
    const active = $(".tab.active").dataset.panel;
    if (active === "brief") loadBrief();
    if (active === "trust") loadTrust();
    toast("Demo state restored. Every panel is back to its seeded starting point.");
  } catch (err) {
    toast(`Reset failed: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
});

/* =====================================================================
   Panel 1 - Request Pipeline
   ===================================================================== */
const STAGES = [
  { key: "request", title: "Intake", kind: "det", desc: "Normalize the raw request into a structured object" },
  { key: "dossier", title: "Context Dossier", kind: "det", desc: "Who is this to Zeb? No decision without a dossier" },
  { key: "policy", title: "Policy Engine", kind: "det", desc: "Which rules fire, and which one decides" },
  { key: "planner", title: "Planner", kind: "model", desc: "The decision, its rationale, and the reply draft" },
  { key: "critique", title: "Critic", kind: "model", desc: "Reviews the plan before a human ever sees it" },
  { key: "approval", title: "Approval routing", kind: "det", desc: "Queued for a human - never auto-sent" },
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
        <div class="stage-title">${s.title}
          <span class="kind ${s.kind}">${s.kind === "model" ? "model-backed" : "deterministic"}</span>
        </div>
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
  const delay = $("#instant-toggle").checked ? 0 : 700;
  try {
    const result = await api(`/api/pipeline/run/${selectedRequest.id}`, { method: "POST" });
    for (let i = 0; i < STAGES.length; i++) {
      const stage = $(`#stage-${STAGES[i].key}`);
      stage.classList.add("running");
      if (delay) await new Promise((r) => setTimeout(r, delay));
      // The lockout banner drops the moment the policy engine resolves.
      if (STAGES[i].key === "policy" && result.banner.sensitive_lockout) {
        const b = $("#lockout-banner");
        b.textContent = result.banner.text;
        b.classList.remove("hidden");
      }
      fillStage(STAGES[i].key, result);
      stage.classList.remove("running");
      stage.classList.add("done", "open");
    }
    toast("Pipeline complete. The plan is queued for approval on the Daily Brief panel.");
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
      <p class="lede">${esc(d.relationship_summary)}</p>
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
      `<p class="muted small stage-note">Evaluated in priority order. The first decisive match sets the outcome; "constrain" rules shape which slots are legal.</p>`;
  }

  if (key === "planner") {
    const d = result.decision;
    if (skippedByLock(result)) {
      status.textContent = "skipped - hard lock";
      body.innerHTML = skippedBlock(
        "The Planner never ran. A hard-locked request does not reach the model seam, so no "
        + "model was asked to summarize, paraphrase, or draft around this topic. The routing "
        + "note below is fixed text over dossier facts the pipeline already had.")
        + `<div class="rationale"><span class="lbl">Why it stopped</span>${esc(d.rationale)}</div>`
        + draftBlock(result);
      return;
    }
    status.textContent = d.outcome.replace(/_/g, " ");
    body.innerHTML = `
      <div class="outcome-line">
        <span class="outcome-badge ${d.outcome}">${d.outcome.replace(/_/g, " ")}</span>
        <span class="chip">${esc(d.trust_level)}</span>
        <span class="muted small">${esc(d.trust_note)}</span>
      </div>
      <div class="rationale"><span class="lbl">Rationale</span>${esc(d.rationale)}</div>
      ${d.delegate_to ? `<p class="stage-line"><strong>Delegated to:</strong> ${esc(d.delegate_to)}</p>` : ""}
      ${d.route_to.length ? `<p class="stage-line"><strong>Routed to:</strong> ${esc(d.route_to.join(", "))}</p>` : ""}
      ${d.proposed_slots.length ? `
        <div class="dossier-section"><h4>Proposed slots (requester-local shown first)</h4>
        ${d.proposed_slots.map((s) => `
          <div class="slot"><span class="req-tz">${esc(s.requester_local)}</span>
          <span class="ceo-tz">${esc(s.ceo_local)} for Zeb</span></div>`).join("")}
        <p class="muted small stage-note">Times come from deterministic calendar math, not the model. Only slots that are provably free and policy-clean reach the Planner.</p>
        </div>` : ""}
      ${draftBlock(result)}`;
  }

  if (key === "critique") {
    const c = result.critique;
    if (skippedByLock(result)) {
      status.textContent = "skipped - hard lock";
      body.innerHTML = skippedBlock(
        "The Critic never ran either. There is no plan to review, because none was written. "
        + "This is recorded as <em>not_run</em> rather than <em>pass</em>: a pass means four "
        + "checks came back clean, and here no check ran at all.");
      return;
    }
    status.textContent = `${c.verdict} · ${c.checks_run.length} checks`;
    body.innerHTML = `
      <div class="outcome-line">
        <span class="verdict-badge ${c.verdict}">${c.verdict === "pass" ? "passed review" : c.verdict}</span>
        <span class="muted small">${esc(c.summary)}</span>
      </div>
      <div class="check-row">${c.checks_run.map((name) => {
        const hit = c.findings.some((f) => f.check === name);
        return `<span class="check ${hit ? "hit" : "ok"}">${hit ? "!" : "✓"} ${esc(name.replace(/_/g, " "))}</span>`;
      }).join("")}</div>
      ${c.findings.length ? c.findings.map((f) => `
        <div class="finding ${esc(f.severity)}">
          <span class="f-sev">${esc(f.severity)}</span>
          <span class="f-msg">${esc(f.message)}</span>
        </div>`).join("")
        : `<p class="muted small stage-note">Nothing to flag. The reply matches the decision, keeps the sensitive rules, sounds like Zeb, and covers what he owes this person.</p>`}
      <p class="muted small stage-note">Anything other than <em>pass</em> requires a human regardless of trust level, and is the gate that governs autonomy as categories climb the ladder.</p>`;
  }

  if (key === "approval") {
    const d = result.decision;
    const c = result.critique;
    const booking = d.proposed_slots.length
      ? `Tentative hold on ${esc(d.proposed_slots[0].ceo_local)}, Zeb's calendar only.
         No invite until ${esc(result.dossier.requester_name.split(" ")[0])} picks a time.`
      : "No calendar write on this outcome";
    const gate = {
      pass: "pass - cleared all four checks",
      revise: "revise - approving requires acknowledging the findings first",
      block: "block - cannot be approved as written; override is the only path",
      not_run: "not_run - the hard lock stopped the pipeline before a plan existed",
    }[c.verdict] || c.verdict;
    status.textContent = `queued as ${result.approval_id}`;
    body.innerHTML = `
      <dl class="kv">
        <dt>Queue item</dt><dd>${esc(result.approval_id)}</dd>
        <dt>Outcome</dt><dd>${esc(d.outcome.replace(/_/g, " "))}</dd>
        <dt>Critic verdict</dt><dd>${esc(gate)}</dd>
        <dt>Model calls</dt><dd>${(result.model_calls || []).length} of ${result.model_call_cap}${
          skippedByLock(result) ? " - the lockout skipped both model stages" : ""}</dd>
        <dt>Autonomy</dt><dd>${esc(d.trust_level)} - ${esc(d.trust_note.split(":").slice(1).join(":").trim() || d.trust_note)}</dd>
        <dt>If approved</dt><dd>${booking}</dd>
      </dl>
      <p class="muted small stage-note">The draft above is what goes out, unchanged, once a human
      approves it, and it is shown again on the approval card so nobody approves a message they
      have not read. Approving runs the calendar adapter once; overriding records a reason that
      feeds the eval loop and can demote the category. Both live on the Daily Brief panel.</p>`;
  }
}

/* A run that never touched the model seam: the sensitive-category lockout. */
const skippedByLock = (result) =>
  result.banner.sensitive_lockout && (result.model_calls || []).length === 0;

const skippedBlock = (why) =>
  `<div class="skipped"><span class="skipped-tag">stage skipped</span>
     <p>${why}</p></div>`;

function emailBlock(dr, footer) {
  const internal = dr.kind === "internal_note";
  return `
    <div class="email ${internal ? "internal" : ""}">
      <div class="email-head">
        <div><span class="lbl">To</span> ${esc(dr.to)}</div>
        <div><span class="lbl">Subject</span> ${esc(dr.subject)}</div>
      </div>
      <div class="email-body">${esc(dr.body)}</div>
      ${footer ? `<div class="email-foot">${footer}</div>` : ""}
    </div>`;
}

function draftBlock(result) {
  return emailBlock(result.draft, `Never auto-sent. Waiting in the approval queue as
    <strong>${esc(result.approval_id)}</strong>.`);
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

function criticBlock(a) {
  const findings = a.critic_findings || [];
  if (a.critic_verdict === "pass") {
    return '<div class="a-critic ok">Critic: passed all four checks</div>';
  }
  if (a.critic_verdict === "not_run") {
    return `<div class="a-critic">Critic: not run - ${esc(a.critic_summary || "")}</div>`;
  }
  if (!a.critic_verdict) return "";
  // revise / block: show every finding, not just the first.
  return `
    <div class="a-critic ${esc(a.critic_verdict)}">
      <div class="a-critic-head">Critic: ${esc(a.critic_verdict)} - ${esc(a.critic_summary || "")}</div>
      <ul>${findings.map((f) => `<li><strong>${esc(f.check.replace(/_/g, " "))}:</strong> ${esc(f.message)}</li>`).join("")}</ul>
    </div>`;
}

function approvalActions(a) {
  if (a.critic_verdict === "block") {
    return `
      <div class="a-gate blocked">The Critic blocked this plan. It cannot be approved as
        written - override it, or fix the plan and re-run.</div>
      <div class="a-actions">
        <button class="btn small primary act-approve" disabled>Approve</button>
        <button class="btn small danger act-override">Override</button>
      </div>`;
  }
  if (a.critic_verdict === "revise") {
    return `
      <label class="a-gate ack">
        <input type="checkbox" class="ack-box" />
        I have read the Critic's findings above and am sending this anyway.
      </label>
      <div class="a-actions">
        <button class="btn small primary act-approve" disabled>Approve anyway</button>
        <button class="btn small danger act-override">Override</button>
      </div>`;
  }
  return `
    <div class="a-actions">
      <button class="btn small primary act-approve">Approve</button>
      <button class="btn small danger act-override">Override</button>
    </div>`;
}

function renderApprovals(pending) {
  const el = $("#approval-list");
  if (!pending.length) {
    el.innerHTML = '<p class="muted small empty">Queue is clear. Run a request through the pipeline to add one.</p>';
    return;
  }
  el.innerHTML = pending
    .map(
      (a) => `
    <div class="approval" data-id="${a.id}">
      <div class="a-head"><span class="a-req">${esc(a.requester)}</span><span class="chip">${esc(a.category)}</span></div>
      <div class="a-sum">${esc(a.summary)}</div>
      ${criticBlock(a)}
      <div class="a-rat">${esc(a.rationale)}</div>
      ${emailBlock(
        { kind: a.kind === "internal_note" ? "internal_note" : "external_reply",
          to: a.draft_to || a.requester,
          subject: a.draft_subject || a.summary,
          body: a.draft || "" },
        a.slots && a.slots.length
          ? `Approving sends this and places a tentative hold on Zeb's calendar only.
             ${esc(a.requester.split(" ")[0])} gets no invite until they pick one of the
             ${a.slots.length} times offered above.`
          : "Approving sends this. No calendar write on this outcome.")}
      ${approvalActions(a)}
      <div class="override-box hidden">
        <input type="text" placeholder="One-line reason (required - this trains the trust ladder)" />
        <button class="btn small danger act-confirm">Confirm</button>
      </div>
    </div>`
    )
    .join("");

  el.querySelectorAll(".approval").forEach((card) => {
    const id = card.dataset.id;
    const approveBtn = $(".act-approve", card);
    const ack = $(".ack-box", card);

    // A "revise" plan stays unapprovable until the findings are acknowledged.
    if (ack) {
      ack.addEventListener("change", () => { approveBtn.disabled = !ack.checked; });
    }

    approveBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      try {
        const res = await api(`/api/approvals/${id}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ acknowledge_critic: Boolean(ack && ack.checked) }),
        });
        if (res.idempotent) {
          toast(res.detail);
        } else {
          const ex = res.execution;
          toast(ex.action === "provisional_hold"
            ? `Approved. Calendar adapter (simulated): ${ex.summary}`
            : `Approved. ${ex.summary}`);
        }
        loadBrief();
      } catch (err) {
        toast(err.message, true);
        approveBtn.disabled = Boolean(ack) && !ack.checked;
      }
    });

    $(".act-override", card).addEventListener("click", () => {
      $(".override-box", card).classList.toggle("hidden");
      $(".override-box input", card).focus();
    });
    const confirm = async () => {
      const box = $(".override-box", card);
      const reason = $("input", box).value.trim();
      if (!reason) { $("input", box).focus(); return; }
      $(".act-confirm", card).disabled = true;
      try {
        const res = await api(`/api/approvals/${id}/override`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        });
        if (res.idempotent) {
          toast(res.detail);
        } else if (res.demotion) {
          toast(`Critical miss. Automatic demotion: ${res.approval.category} ${res.demotion.from} \u2192 ${res.demotion.to}. See the Trust panel.`, true);
        } else if (res.critical_miss) {
          toast("Override recorded as a critical miss. Trust metrics updated.", true);
        } else {
          toast("Override recorded. The eval loop picks this up on the Trust panel.");
        }
        loadBrief();
      } catch (err) {
        toast(err.message, true);
        $(".act-confirm", card).disabled = false;
      }
    };
    $(".act-confirm", card).addEventListener("click", confirm);
    $(".override-box input", card).addEventListener("keydown", (e) => { if (e.key === "Enter") confirm(); });
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
          ${row.locked ? '<span class="lock-chip">hard-locked L0</span>' : ""}
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
      <span class="va ${o.human_action === "approved" ? "ok" : "no"}">${esc(o.human_action)}${o.critical_miss ? " · critical" : ""}</span>
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
api("/api/status").then((s) => {
  $("#persistence-note").textContent =
    s.state_persistence === "memory"
      ? "State on this deployment lives in memory: your run is real and fully interactive, and it resets on a cold start or when you press Reset demo."
      : "State persists to the JSON files in /data. Press Reset demo to restore the seeded starting point.";
}).catch(() => {});
