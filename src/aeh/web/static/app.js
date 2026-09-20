// Eval Studio -- vanilla JS single-page app. No build step, no framework.
"use strict";

const Views = document.getElementById("views");
const state = {
  suite: null,
  runs: {}, // run_id -> grid data
  activeRunId: null,
  calibrationRows: [],
  calibrationResult: null,
  cassettes: [],
  editingTaskId: null,
};

// ---------------------------------------------------------------- helpers

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let body = null;
  try { body = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const msg = (body && (body.detail || body.error)) || `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

function toast(message, isError) {
  const root = document.getElementById("toast-root");
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3500);
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children || []) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function fmtPct(x) { return (x * 100).toFixed(0) + "%"; }
function fmtNum(x) { return x == null ? "-" : x.toLocaleString(); }
function fmtCost(x) { return x == null ? "-" : "$" + x.toFixed(4); }

// ---------------------------------------------------------------- shell

const TABS = ["author", "run", "results", "calibration", "gate", "cassettes"];

function setupTabs() {
  document.getElementById("tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn) return;
    activateTab(btn.dataset.tab);
  });
}

function activateTab(name) {
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const v of document.querySelectorAll(".view")) v.classList.toggle("active", v.id === "view-" + name);
  if (name === "results") renderResultsView();
  if (name === "gate") renderGateView();
  if (name === "cassettes") renderCassettesView();
}

async function loadModeBanner() {
  const data = await api("/api/mode");
  const b = document.getElementById("mode-banner");
  b.className = "mode-banner " + (data.gemini.ready ? "live" : "offline");
  b.innerHTML = "";
  b.appendChild(el("span", { class: "mode-dot" }));
  b.appendChild(el("span", {}, [data.banner]));
}

// ================================================================== AUTHOR

function emptyTask() {
  return {
    id: "",
    input: "",
    setup: { workspace: {} },
    plan: [],
    assertions: [],
    rubric: "",
    seeds: [1, 2, 3],
    tags: [],
  };
}

function renderAuthorView() {
  const v = document.getElementById("view-author");
  v.innerHTML = "";
  v.appendChild(el("h1", {}, [`Suite: ${state.suite.name}`]));
  v.appendChild(el("p", { class: "muted small" }, [`Persisted to ${state.suite.path}`]));

  const list = el("div", { class: "task-list" });
  for (const task of state.suite.tasks) {
    const card = el("div", { class: "task-card" }, [
      el("span", { class: "tid" }, [task.id]),
      el("span", { class: "tprompt" }, [task.input]),
      el("span", { class: "task-tags" }, task.tags.map((t) => el("span", { class: "tag" }, [t]))),
      el("span", { class: "muted small" }, [`${task.seeds.length} seed(s)`]),
      el("button", { class: "btn small", onclick: () => openTaskForm(task) }, ["Edit"]),
      el("button", { class: "btn small danger", onclick: () => deleteTask(task.id) }, ["Delete"]),
    ]);
    list.appendChild(card);
  }
  v.appendChild(list);

  v.appendChild(
    el("div", { class: "row", style: "margin-top:14px" }, [
      el("button", { class: "btn primary", onclick: () => openTaskForm(emptyTask()) }, ["+ New task"]),
    ])
  );

  v.appendChild(el("div", { id: "task-form-slot" }));
}

function openTaskForm(task) {
  state.editingTaskId = task.id || null;
  const slot = document.getElementById("task-form-slot");
  slot.innerHTML = "";
  slot.appendChild(buildTaskForm(JSON.parse(JSON.stringify(task))));
  slot.scrollIntoView({ behavior: "smooth" });
}

function buildTaskForm(task) {
  const panel = el("div", { class: "panel" });
  panel.appendChild(el("h2", {}, [task.id ? `Edit task: ${task.id}` : "New task"]));

  const errBox = el("ul", { class: "err-list", style: "display:none" });

  const idInput = el("input", { type: "text", value: task.id, placeholder: "task_id" });
  const inputBox = el("textarea", { placeholder: "Prompt" }, []);
  inputBox.value = task.input;
  const rubricBox = el("textarea", { placeholder: "Rubric" }, []);
  rubricBox.value = task.rubric;
  const seedsInput = el("input", { type: "text", value: task.seeds.join(", "), placeholder: "1, 2, 3" });
  const tagsInput = el("input", { type: "text", value: task.tags.join(", "), placeholder: "flaky, regressed" });

  panel.appendChild(
    el("div", { class: "row" }, [
      el("div", { class: "col grow" }, [el("label", {}, ["Task ID"]), idInput]),
      el("div", { class: "col grow" }, [el("label", {}, ["Seeds (comma-separated)"]), seedsInput]),
      el("div", { class: "col grow" }, [el("label", {}, ["Tags (comma-separated)"]), tagsInput]),
    ])
  );
  panel.appendChild(el("div", { class: "col", style: "margin-top:8px" }, [el("label", {}, ["Prompt"]), inputBox]));
  panel.appendChild(el("div", { class: "col", style: "margin-top:8px" }, [el("label", {}, ["Rubric"]), rubricBox]));

  // -- workspace files --
  panel.appendChild(el("div", { class: "fieldset-title" }, [el("h3", {}, ["Workspace"])]));
  const wsList = el("div", {});
  const workspace = task.setup.workspace || {};
  const wsRows = Object.entries(workspace).map(([p, c]) => ({ path: p, content: c }));
  function renderWs() {
    wsList.innerHTML = "";
    wsRows.forEach((row, i) => {
      const pathInput = el("input", { type: "text", value: row.path, placeholder: "config.yaml" });
      pathInput.addEventListener("input", () => (row.path = pathInput.value));
      const contentInput = el("textarea", { placeholder: "file contents" });
      contentInput.value = row.content;
      contentInput.addEventListener("input", () => (row.content = contentInput.value));
      wsList.appendChild(
        el("div", { class: "repeat-row" }, [
          el("div", { class: "repeat-grid" }, [pathInput, contentInput]),
          el("button", { class: "btn small danger", onclick: () => { wsRows.splice(i, 1); renderWs(); } }, ["Remove"]),
        ])
      );
    });
  }
  renderWs();
  panel.appendChild(wsList);
  panel.appendChild(el("button", { class: "btn small", onclick: () => { wsRows.push({ path: "", content: "" }); renderWs(); } }, ["+ Add file"]));

  // -- plan --
  panel.appendChild(el("div", { class: "fieldset-title" }, [el("h3", {}, ["Plan (fake backend)"])]));
  const planList = el("div", {});
  const planRows = (task.plan || []).map((s) => JSON.parse(JSON.stringify(s)));
  function renderPlan() {
    planList.innerHTML = "";
    planRows.forEach((step, i) => {
      const isFinish = "finish" in step;
      const kindSelect = el("select", {}, [
        el("option", { value: "tool" }, ["tool call"]),
        el("option", { value: "finish" }, ["finish"]),
      ]);
      kindSelect.value = isFinish ? "finish" : "tool";
      let body;
      function renderBody() {
        if (kindSelect.value === "finish") {
          const statusInput = el("input", { type: "text", value: (step.finish && step.finish.status) || "done", placeholder: "status" });
          statusInput.addEventListener("input", () => { step.finish = { ...(step.finish || {}), status: statusInput.value }; delete step.tool; delete step.args; });
          body = el("div", { class: "repeat-grid" }, [statusInput]);
        } else {
          const toolSelect = el("select", {}, ["list_files", "read_file", "write_file", "search"].map((n) => el("option", { value: n }, [n])));
          toolSelect.value = step.tool || "read_file";
          const argsInput = el("textarea", { placeholder: '{"path": "config.yaml"}' });
          argsInput.value = JSON.stringify(step.args || {});
          toolSelect.addEventListener("change", () => { step.tool = toolSelect.value; delete step.finish; });
          argsInput.addEventListener("input", () => {
            try { step.args = JSON.parse(argsInput.value || "{}"); delete step.finish; } catch (e) { /* leave as-is until valid */ }
          });
          body = el("div", { class: "repeat-grid" }, [toolSelect, argsInput]);
        }
      }
      renderBody();
      kindSelect.addEventListener("change", () => {
        if (kindSelect.value === "finish") { step.finish = { status: "done" }; delete step.tool; delete step.args; }
        else { step.tool = "read_file"; step.args = {}; delete step.finish; }
        wrapper.replaceChild((renderBody(), body), wrapper.children[0].children[1] || wrapper.children[0]);
      });
      const wrapper = el("div", { class: "repeat-row" }, [
        el("div", {}, [kindSelect, body]),
        el("button", { class: "btn small danger", onclick: () => { planRows.splice(i, 1); renderPlan(); } }, ["Remove"]),
      ]);
      planList.appendChild(wrapper);
    });
  }
  renderPlan();
  panel.appendChild(planList);
  panel.appendChild(el("button", { class: "btn small", onclick: () => { planRows.push({ tool: "read_file", args: {} }); renderPlan(); } }, ["+ Add step"]));

  // -- assertions --
  panel.appendChild(el("div", { class: "fieldset-title" }, [el("h3", {}, ["Assertions"])]));
  const assertList = el("div", {});
  const assertRows = (task.assertions || []).map((s) => JSON.parse(JSON.stringify(s)));
  const ASSERTION_FIELDS = {
    file_contains: ["path", "text"],
    file_equals: ["path", "content"],
    tool_sequence: ["sequence", "mode"],
    forbidden_tool: ["tool"],
    status_equals: ["status"],
  };
  function renderAsserts() {
    assertList.innerHTML = "";
    assertRows.forEach((spec, i) => {
      const typeSelect = el("select", {}, Object.keys(ASSERTION_FIELDS).map((t) => el("option", { value: t }, [t])));
      typeSelect.value = spec.type || "file_contains";
      const fieldsBox = el("div", { class: "repeat-grid" });
      function renderFields() {
        fieldsBox.innerHTML = "";
        for (const f of ASSERTION_FIELDS[typeSelect.value]) {
          const raw = spec[f];
          const value = Array.isArray(raw) ? raw.join(", ") : raw != null ? String(raw) : "";
          const input = el("input", { type: "text", placeholder: f, value });
          input.addEventListener("input", () => {
            if (f === "sequence") spec[f] = input.value.split(",").map((s) => s.trim()).filter(Boolean);
            else spec[f] = input.value;
          });
          fieldsBox.appendChild(el("div", { class: "col" }, [el("label", {}, [f]), input]));
        }
      }
      renderFields();
      typeSelect.addEventListener("change", () => {
        spec.type = typeSelect.value;
        for (const k of Object.keys(spec)) if (k !== "type") delete spec[k];
        renderFields();
      });
      assertList.appendChild(
        el("div", { class: "repeat-row" }, [
          el("div", {}, [typeSelect, fieldsBox]),
          el("button", { class: "btn small danger", onclick: () => { assertRows.splice(i, 1); renderAsserts(); } }, ["Remove"]),
        ])
      );
    });
  }
  renderAsserts();
  assertList.dataset.role = "assert-list";
  panel.appendChild(assertList);
  panel.appendChild(el("button", { class: "btn small", onclick: () => { assertRows.push({ type: "file_contains", path: "", text: "" }); renderAsserts(); } }, ["+ Add assertion"]));

  panel.appendChild(errBox);

  panel.appendChild(
    el("div", { class: "row", style: "margin-top:14px" }, [
      el("button", {
        class: "btn primary",
        onclick: async () => {
          const payload = {
            id: idInput.value.trim(),
            input: inputBox.value,
            rubric: rubricBox.value,
            seeds: seedsInput.value.split(",").map((s) => s.trim()).filter(Boolean).map(Number),
            tags: tagsInput.value.split(",").map((s) => s.trim()).filter(Boolean),
            setup: { workspace: Object.fromEntries(wsRows.filter((r) => r.path).map((r) => [r.path, r.content])) },
            plan: planRows,
            assertions: assertRows,
          };
          try {
            const resp = await api("/api/suite/tasks", {
              method: "POST",
              body: JSON.stringify({ task: payload, editing_id: state.editingTaskId }),
            });
            if (!resp.ok) {
              errBox.style.display = "block";
              errBox.innerHTML = "";
              for (const e of resp.errors) errBox.appendChild(el("li", {}, [e]));
              return;
            }
            toast(`Saved task ${payload.id}`);
            await refreshSuite();
            document.getElementById("task-form-slot").innerHTML = "";
          } catch (e) {
            toast("Save failed: " + e.message, true);
          }
        },
      }, ["Save task"]),
      el("button", { class: "btn", onclick: () => { document.getElementById("task-form-slot").innerHTML = ""; } }, ["Cancel"]),
    ])
  );

  return panel;
}

async function deleteTask(taskId) {
  if (!confirm(`Delete task ${taskId}?`)) return;
  try {
    await api(`/api/suite/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
    toast(`Deleted ${taskId}`);
    await refreshSuite();
  } catch (e) {
    toast("Delete failed: " + e.message, true);
  }
}

async function refreshSuite() {
  state.suite = await api("/api/suite");
  renderAuthorView();
  renderRunView();
}

// ================================================================== RUN

function renderRunView() {
  const v = document.getElementById("view-run");
  v.innerHTML = "";
  v.appendChild(el("h1", {}, ["Run tasks"]));

  const taskChecks = {};
  const checklist = el("div", { class: "panel" }, [
    el("h3", {}, ["Tasks"]),
    ...state.suite.tasks.map((t) => {
      const cb = el("input", { type: "checkbox", checked: "checked" });
      taskChecks[t.id] = cb;
      return el("label", { class: "row", style: "align-items:center;gap:6px;font-weight:400;color:var(--ink)" }, [cb, el("span", { class: "mono" }, [t.id])]);
    }),
  ]);
  v.appendChild(checklist);

  const seedsOverride = el("input", { type: "text", placeholder: "leave blank to use each task's own seeds" });
  const backendSelect = el("select", {}, [el("option", { value: "fake" }, ["fake (offline)"]), el("option", { value: "gemini" }, ["gemini (real, billed)"])]);
  const modelInput = el("input", { type: "text", value: "gemini-2.5-flash-lite" });
  const tempInput = el("input", { type: "number", value: "0", step: "0.1", min: "0", max: "2" });
  const judgeCheck = el("input", { type: "checkbox", checked: "checked" });

  const estimateBox = el("div", { class: "stat-row" });

  const config = el("div", { class: "panel" }, [
    el("h3", {}, ["Run configuration"]),
    el("div", { class: "row" }, [
      el("div", { class: "col grow" }, [el("label", {}, ["Backend"]), backendSelect]),
      el("div", { class: "col grow" }, [el("label", {}, ["Model"]), modelInput]),
      el("div", { class: "col grow" }, [el("label", {}, ["Temperature"]), tempInput]),
    ]),
    el("div", { class: "row", style: "margin-top:8px" }, [
      el("div", { class: "col grow" }, [el("label", {}, ["Seed override"]), seedsOverride]),
      el("label", { class: "row", style: "align-items:center;gap:6px;font-weight:400;color:var(--ink)" }, [judgeCheck, el("span", {}, ["Score with judge"])]),
    ]),
    estimateBox,
    el("div", { class: "row", style: "margin-top:10px" }, [
      el("button", { class: "btn", onclick: doEstimate }, ["Estimate cost"]),
      el("button", { class: "btn primary", onclick: doStartRun }, ["Run"]),
    ]),
  ]);
  v.appendChild(config);

  const progressPanel = el("div", { class: "panel", id: "run-progress", style: "display:none" }, [
    el("h3", {}, ["Progress"]),
    el("div", { class: "progress-bar" }, [el("div", { id: "run-progress-fill", style: "width:0%" })]),
    el("p", { class: "muted small", id: "run-progress-text" }, [""]),
    el("div", { class: "stream-log", id: "run-log" }),
  ]);
  v.appendChild(progressPanel);

  function selectedTaskIds() {
    return Object.entries(taskChecks).filter(([, cb]) => cb.checked).map(([id]) => id);
  }
  function parsedSeeds() {
    const raw = seedsOverride.value.trim();
    if (!raw) return null;
    return raw.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n));
  }

  async function doEstimate() {
    try {
      const resp = await api("/api/estimate", {
        method: "POST",
        body: JSON.stringify({ task_ids: selectedTaskIds(), seeds: parsedSeeds(), model: modelInput.value }),
      });
      estimateBox.innerHTML = "";
      estimateBox.appendChild(el("div", { class: "stat" }, [el("span", { class: "v" }, [String(resp.seed_runs)]), el("span", { class: "l" }, ["seed-runs"])]));
      estimateBox.appendChild(el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtNum(resp.estimated_tokens_in)]), el("span", { class: "l" }, ["est. tokens in"])]));
      estimateBox.appendChild(el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtNum(resp.estimated_tokens_out)]), el("span", { class: "l" }, ["est. tokens out"])]));
      estimateBox.appendChild(el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtCost(resp.estimated_cost)]), el("span", { class: "l" }, ["est. cost (before run)"])]));
    } catch (e) {
      toast("Estimate failed: " + e.message, true);
    }
  }

  async function doStartRun() {
    const ids = selectedTaskIds();
    if (!ids.length) { toast("Select at least one task", true); return; }
    try {
      const resp = await api("/api/runs", {
        method: "POST",
        body: JSON.stringify({
          task_ids: ids,
          seeds: parsedSeeds(),
          backend: backendSelect.value,
          model: modelInput.value,
          temperature: parseFloat(tempInput.value || "0"),
          use_judge: judgeCheck.checked,
        }),
      });
      state.activeRunId = resp.run_id;
      progressPanel.style.display = "";
      document.getElementById("run-log").innerHTML = "";
      streamRun(resp.run_id);
    } catch (e) {
      toast("Run failed to start: " + e.message, true);
    }
  }
}

function streamRun(runId) {
  const fill = document.getElementById("run-progress-fill");
  const text = document.getElementById("run-progress-text");
  const log = document.getElementById("run-log");
  const es = new EventSource(`/api/runs/${runId}/stream`);
  function logLine(s) {
    const line = el("div", {}, [s]);
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }
  es.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch (e) { return; }
    if (data.type === "run_start") {
      text.textContent = `0 / ${data.total} seed-runs`;
    } else if (data.type === "seed_start") {
      logLine(`> ${data.task_id} seed=${data.seed} running...`);
    } else if (data.type === "seed_done") {
      const pct = data.total ? Math.round((data.completed / data.total) * 100) : 0;
      fill.style.width = pct + "%";
      const tokenText = data.cost_estimate > 0 ? ` -- tokens in=${fmtNum(data.tokens_in)} out=${fmtNum(data.tokens_out)} -- ${fmtCost(data.cost_estimate)}` : "";
      text.textContent = `${data.completed} / ${data.total} seed-runs${tokenText}`;
      logLine(`${data.passed ? "PASS" : "FAIL"} ${data.task_id} seed=${data.seed}${data.error ? " (" + data.error + ")" : ""}`);
    } else if (data.type === "run_error") {
      logLine("ERROR: " + data.error);
      toast("Run error: " + data.error, true);
    } else if (data.type === "run_end") {
      fill.style.width = "100%";
      logLine(data.status === "done" ? "Run complete." : "Run ended with error.");
      es.close();
      toast("Run finished");
      activateTab("results");
    }
  };
  es.onerror = () => { /* browser auto-reconnects; server closes stream on completion */ };
}

// ================================================================== RESULTS

async function renderResultsView() {
  const v = document.getElementById("view-results");
  v.innerHTML = "";
  v.appendChild(el("h1", {}, ["Results"]));

  let runsList;
  try {
    runsList = (await api("/api/runs")).runs;
  } catch (e) {
    v.appendChild(el("p", { class: "muted" }, ["No runs yet."]));
    return;
  }
  if (!runsList.length) {
    v.appendChild(el("p", { class: "muted" }, ["No runs yet."]));
    return;
  }

  const select = el("select", {}, runsList.map((r) => el("option", { value: r.id }, [`${r.id} -- ${new Date(r.created_at * 1000).toLocaleString()} (${r.status})`])));
  select.value = state.activeRunId && runsList.some((r) => r.id === state.activeRunId) ? state.activeRunId : runsList[0].id;

  const gridSlot = el("div", { id: "grid-slot" });

  v.appendChild(el("div", { class: "row" }, [el("div", { class: "col grow" }, [el("label", {}, ["Run"]), select]), el("button", { class: "btn", onclick: () => loadGrid(select.value) }, ["Refresh"])]));
  v.appendChild(gridSlot);

  select.addEventListener("change", () => loadGrid(select.value));

  async function loadGrid(runId) {
    state.activeRunId = runId;
    const grid = await api(`/api/runs/${runId}`);
    gridSlot.innerHTML = "";

    const s = grid.summary;
    const isReal = s.backend !== "fake";
    const stats = [
      el("div", { class: "stat" }, [el("span", { class: "v" }, [`${s.completed_seed_runs}/${s.total_seed_runs}`]), el("span", { class: "l" }, ["seed-runs"])]),
      el("div", { class: "stat" }, [el("span", { class: "v" }, [s.status]), el("span", { class: "l" }, ["status"])]),
    ];
    if (isReal) {
      stats.push(el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtNum(s.tokens_in)]), el("span", { class: "l" }, ["tokens in (measured)"])]));
      stats.push(el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtNum(s.tokens_out)]), el("span", { class: "l" }, ["tokens out (measured)"])]));
      stats.push(el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtCost(s.cost_estimate)]), el("span", { class: "l" }, ["cost (from measured tokens)"])]));
    } else {
      stats.push(el("div", { class: "stat" }, [el("span", { class: "v muted" }, ["n/a"]), el("span", { class: "l" }, ["tokens/cost (fake backend is free, offline)"])]));
    }
    gridSlot.appendChild(el("div", { class: "stat-row" }, stats));

    const allSeeds = new Set();
    for (const row of grid.rows) for (const seed of Object.keys(row.seeds)) allSeeds.add(seed);
    const seedCols = [...allSeeds].sort((a, b) => Number(a) - Number(b));

    const table = el("table", { class: "grid" });
    table.appendChild(
      el("thead", {}, [
        el("tr", {}, [
          el("th", {}, ["Task"]),
          ...seedCols.map((sc) => el("th", {}, [`seed ${sc}`])),
          el("th", {}, ["Pass rate"]),
          el("th", {}, ["Flake score"]),
          el("th", {}, ["Verdict"]),
        ]),
      ])
    );
    const tbody = el("tbody", {});
    for (const row of grid.rows) {
      const cells = seedCols.map((sc) => {
        const seedRow = row.seeds[sc];
        if (!seedRow) return el("td", {}, [el("span", { class: "cell-seed pending" })]);
        const cls = seedRow.passed ? "pass" : "fail";
        const cell = el("span", { class: `cell-seed ${cls}`, title: seedRow.error || "" });
        cell.addEventListener("click", () => openTrajectoryDrawer(state.activeRunId, row.task_id, sc));
        return el("td", {}, [cell]);
      });
      tbody.appendChild(
        el("tr", {}, [
          el("td", { class: "task-cell" }, [row.task_id]),
          ...cells,
          el("td", { class: "num" }, [fmtPct(row.pass_rate)]),
          el("td", { class: "num" }, [row.flake_score.toFixed(2)]),
          el("td", {}, [el("span", { class: `pill verdict-pill ${row.verdict}` }, [row.verdict])]),
        ])
      );
    }
    table.appendChild(tbody);
    gridSlot.appendChild(el("div", { class: "grid-wrap" }, [table]));

    gridSlot.appendChild(
      el("div", { class: "row", style: "margin-top:12px" }, [
        el("button", { class: "btn primary", onclick: () => saveBaseline(runId) }, ["Save as baseline"]),
      ])
    );
  }

  await loadGrid(select.value);
}

async function saveBaseline(runId) {
  try {
    const resp = await api(`/api/runs/${runId}/baseline`, { method: "POST" });
    toast(`Baseline saved for ${resp.task_count} task(s).`);
  } catch (e) {
    toast("Save baseline failed: " + e.message, true);
  }
}

// ---- trajectory drawer (shared by results + cassettes) ----

function ensureDrawer() {
  let backdrop = document.getElementById("drawer-backdrop");
  if (backdrop) return backdrop;
  backdrop = el("div", { class: "drawer-backdrop", id: "drawer-backdrop" });
  const drawer = el("div", { class: "drawer", id: "drawer" });
  backdrop.appendChild(drawer);
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) closeDrawer(); });
  document.body.appendChild(backdrop);
  return backdrop;
}
function closeDrawer() { document.getElementById("drawer-backdrop").classList.remove("open"); }

async function openTrajectoryDrawer(runId, taskId, seed) {
  let data;
  try {
    data = await api(`/api/runs/${runId}/trajectory/${encodeURIComponent(taskId)}/${seed}`);
  } catch (e) {
    toast("Trajectory load failed: " + e.message, true);
    return;
  }
  const backdrop = ensureDrawer();
  const drawer = document.getElementById("drawer");
  drawer.innerHTML = "";
  drawer.appendChild(el("button", { class: "btn small drawer-close", onclick: closeDrawer }, ["Close"]));
  drawer.appendChild(el("h2", {}, [`${taskId} -- seed ${seed}`]));
  drawer.appendChild(el("p", {}, [el("span", { class: `pill verdict-pill ${data.passed ? "stable-pass" : "stable-fail"}` }, [data.passed ? "PASS" : "FAIL"]), data.diverged ? el("span", { class: "pill", style: "margin-left:6px;color:var(--warn);border-color:var(--warn)" }, ["diverged"]) : null]));
  if (data.error) drawer.appendChild(el("p", { class: "small", style: "color:var(--critical)" }, [data.error]));

  drawer.appendChild(el("h3", {}, ["Trajectory"]));
  for (const ev of data.events || []) {
    if (!["llm_call", "tool_call", "llm_result", "tool_result"].includes(ev.type)) continue;
    const pre = el("pre", {}, [JSON.stringify(ev.type.includes("call") ? ev.payload.request : ev.payload.response, null, 2)]);
    drawer.appendChild(el("div", { class: "trace-step" }, [el("span", { class: "kind" }, [ev.type]), pre]));
  }

  drawer.appendChild(el("h3", {}, ["Final workspace"]));
  const files = (data.final_state && data.final_state.workspace) || {};
  if (!Object.keys(files).length) drawer.appendChild(el("p", { class: "muted small" }, ["(no files)"]));
  for (const [path, content] of Object.entries(files)) {
    drawer.appendChild(el("div", { class: "trace-step" }, [el("span", { class: "kind" }, [path]), el("pre", {}, [content])]));
  }

  drawer.appendChild(el("h3", {}, ["Assertions"]));
  for (const a of data.assertions || []) {
    drawer.appendChild(el("div", { class: `assert-line ${a.passed ? "pass" : "fail"}` }, [el("span", { class: "mark" }, [a.passed ? "✓" : "✕"]), el("span", {}, [`${a.type}: ${a.message}`])]));
  }

  if (data.judge) {
    drawer.appendChild(el("h3", {}, ["Judge"]));
    drawer.appendChild(el("p", { class: "small" }, [`label=${data.judge.label} -- ${data.judge.rationale}`]));
    drawer.appendChild(el("p", { class: "muted small" }, ["Pass/fail comes from assertions. The judge is advisory."]));
  }

  backdrop.classList.add("open");
}

// ================================================================== CALIBRATION

async function renderCalibrationView() {
  const v = document.getElementById("view-calibration");
  v.innerHTML = "";
  v.appendChild(el("h1", {}, ["Judge calibration"]));
  v.appendChild(el("p", { class: "muted small" }, ["Relabel a row and re-run to see kappa change."]));

  const rows = (await api("/api/calibration")).rows;
  state.calibrationRows = rows;

  const backendSelect = el("select", {}, [el("option", { value: "fake" }, ["offline stub judge"]), el("option", { value: "gemini" }, ["gemini judge (real, billed)"])]);
  const modelInput = el("input", { type: "text", value: "gemini-2.5-flash-lite" });
  const resultSlot = el("div", { id: "calibration-result" });

  v.appendChild(
    el("div", { class: "panel" }, [
      el("div", { class: "row" }, [
        el("div", { class: "col grow" }, [el("label", {}, ["Judge backend"]), backendSelect]),
        el("div", { class: "col grow" }, [el("label", {}, ["Model"]), modelInput]),
        el("button", { class: "btn primary", onclick: () => runCalibration(backendSelect.value, modelInput.value) }, ["Run judge over set"]),
        el("button", { class: "btn", onclick: resetLabels }, ["Reset relabels"]),
      ]),
    ])
  );

  const tableWrap = el("div", { class: "grid-wrap" });
  const table = el("table", { class: "grid" });
  table.appendChild(el("thead", {}, [el("tr", {}, [el("th", {}, ["ID"]), el("th", {}, ["Expect substring"]), el("th", {}, ["Human label"]), el("th", {}, ["Judge label"])])]));
  const tbody = el("tbody", {});
  table.appendChild(tbody);
  tableWrap.appendChild(table);

  function renderRows(judgeByAdmin) {
    tbody.innerHTML = "";
    for (const row of state.calibrationRows) {
      const labelSelect = el("select", {}, [el("option", { value: "pass" }, ["pass"]), el("option", { value: "fail" }, ["fail"])]);
      labelSelect.value = row.human_label;
      labelSelect.addEventListener("change", async () => {
        try {
          await api("/api/calibration/relabel", { method: "POST", body: JSON.stringify({ id: row.id, label: labelSelect.value }) });
          row.human_label = labelSelect.value;
          toast(`Relabeled ${row.id} -> ${labelSelect.value}`);
        } catch (e) {
          toast("Relabel failed: " + e.message, true);
        }
      });
      const judgeLabel = judgeByAdmin ? judgeByAdmin[row.id] : null;
      const mismatch = judgeLabel && judgeLabel !== row.human_label;
      tbody.appendChild(
        el("tr", { class: mismatch ? "diff-row regressed" : "" }, [
          el("td", { class: "mono" }, [row.id]),
          el("td", {}, [row.rubric_hint && row.rubric_hint.expect_substring != null ? String(row.rubric_hint.expect_substring) : "-"]),
          el("td", {}, [labelSelect]),
          el("td", {}, [judgeLabel || "-"]),
        ])
      );
    }
  }
  renderRows(null);
  v.appendChild(tableWrap);
  v.appendChild(resultSlot);

  async function resetLabels() {
    await api("/api/calibration/reset", { method: "POST" });
    const rows2 = (await api("/api/calibration")).rows;
    state.calibrationRows = rows2;
    renderRows(null);
    toast("Relabels reset");
  }

  async function runCalibration(backend, model) {
    resultSlot.innerHTML = "<p class='muted small'>Running judge...</p>";
    try {
      const result = await api("/api/calibration/run", { method: "POST", body: JSON.stringify({ backend, model }) });
      state.calibrationResult = result;
      const judgeByAdmin = {};
      for (const d of result.disagreements) judgeByAdmin[d.id] = d.judge_label;
      // also fill agreements: derive from confusion is not per-row, so best effort: mark matches as human label
      for (const row of state.calibrationRows) if (!(row.id in judgeByAdmin)) judgeByAdmin[row.id] = row.human_label;
      renderRows(judgeByAdmin);

      resultSlot.innerHTML = "";
      resultSlot.appendChild(
        el("div", { class: "stat-row" }, [
          el("div", { class: "stat" }, [el("span", { class: "v" }, [String(result.n)]), el("span", { class: "l" }, ["n"])]),
          el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtPct(result.raw_agreement)]), el("span", { class: "l" }, ["raw agreement"])]),
          el("div", { class: "stat" }, [el("span", { class: "v" }, [result.kappa.toFixed(3)]), el("span", { class: "l" }, ["Cohen's kappa"])]),
          result.cost_estimate != null ? el("div", { class: "stat" }, [el("span", { class: "v" }, [fmtCost(result.cost_estimate)]), el("span", { class: "l" }, ["cost"])]) : null,
        ])
      );

      const labels = Object.keys(result.confusion).sort();
      const allLabels = new Set(labels);
      for (const l of Object.values(result.confusion)) for (const k of Object.keys(l)) allLabels.add(k);
      const cols = [...allLabels].sort();
      const ctable = el("table", { class: "grid confusion-table" });
      ctable.appendChild(el("thead", {}, [el("tr", {}, [el("th", {}, ["human \\ judge"]), ...cols.map((c) => el("th", {}, [c]))])]));
      const cbody = el("tbody", {});
      for (const rowLabel of cols) {
        cbody.appendChild(
          el("tr", {}, [
            el("th", {}, [rowLabel]),
            ...cols.map((c) => {
              const v = (result.confusion[rowLabel] || {})[c] || 0;
              return el("td", { class: rowLabel === c ? "diag" : "off" }, [String(v)]);
            }),
          ])
        );
      }
      ctable.appendChild(cbody);
      resultSlot.appendChild(el("h3", {}, ["Confusion matrix"]));
      resultSlot.appendChild(el("div", { class: "grid-wrap" }, [ctable]));

      resultSlot.appendChild(el("h3", {}, [`Disagreements (${result.disagreements.length})`]));
      if (!result.disagreements.length) resultSlot.appendChild(el("p", { class: "muted small" }, ["None."]));
      for (const d of result.disagreements) {
        resultSlot.appendChild(
          el("div", { class: "disagreement" }, [
            el("div", { class: "head" }, [el("span", { class: "mono" }, [d.id]), el("span", {}, [`human=${d.human_label} judge=${d.judge_label}`])]),
            el("p", { class: "small" }, [d.rationale]),
          ])
        );
      }
    } catch (e) {
      resultSlot.innerHTML = "";
      toast("Calibration run failed: " + e.message, true);
    }
  }
}

// ================================================================== GATE

async function renderGateView() {
  const v = document.getElementById("view-gate");
  v.innerHTML = "";
  v.appendChild(el("h1", {}, ["Baseline & gate"]));

  const baseline = await api("/api/baseline");
  const baselineBox = el("div", { class: "panel panel-2" });
  if (!baseline.exists) {
    baselineBox.appendChild(el("p", { class: "muted" }, ["No baseline saved."]));
  } else {
    baselineBox.appendChild(el("h3", {}, ["Current baseline"]));
    baselineBox.appendChild(el("p", { class: "small" }, [`${baseline.suite_name} -- saved ${new Date(baseline.created_at * 1000).toLocaleString()} -- ${Object.keys(baseline.tasks).length} task(s)`]));
  }
  v.appendChild(baselineBox);

  let runsList = [];
  try { runsList = (await api("/api/runs")).runs; } catch (e) { /* none yet */ }
  if (!runsList.length) {
    v.appendChild(el("p", { class: "muted" }, ["No run to compare."]));
    return;
  }

  const select = el("select", {}, runsList.map((r) => el("option", { value: r.id }, [`${r.id} (${r.status})`])));
  const resultSlot = el("div", { id: "gate-result" });
  v.appendChild(
    el("div", { class: "row" }, [
      el("div", { class: "col grow" }, [el("label", {}, ["Compare run"]), select]),
      el("button", { class: "btn primary", onclick: () => runGate(select.value) }, ["Run gate"]),
    ])
  );
  v.appendChild(resultSlot);

  async function runGate(runId) {
    if (!baseline.exists) { toast("Save a baseline first", true); return; }
    try {
      const result = await api("/api/gate", { method: "POST", body: JSON.stringify({ run_id: runId }) });
      resultSlot.innerHTML = "";
      resultSlot.appendChild(
        el("p", {}, [el("span", { class: `pill verdict-pill ${result.gate_passed ? "stable-pass" : "stable-fail"}` }, [result.gate_passed ? "GATE PASSED" : "GATE FAILED"]), el("span", { class: "muted small", style: "margin-left:8px" }, [`tolerance z=${result.z}`])])
      );
      const table = el("table", { class: "grid" });
      table.appendChild(el("thead", {}, [el("tr", {}, [el("th", {}, ["Task"]), el("th", {}, ["Baseline"]), el("th", {}, ["Current"]), el("th", {}, ["Margin"]), el("th", {}, ["Classification"])])]));
      const tbody = el("tbody", {});
      for (const d of result.diffs) {
        const base = d.baseline_pass_rate;
        const cur = d.current_pass_rate;
        const margin = d.margin;
        const track = el("div", { class: "margin-track" });
        if (base != null) {
          const lo = Math.max(0, base - margin) * 100;
          const hi = Math.min(1, base + margin) * 100;
          track.appendChild(el("div", { class: "margin-band", style: `left:${lo}%;width:${hi - lo}%` }));
          track.appendChild(el("div", { class: "margin-point", style: `left:${base * 100}%` }));
          if (cur != null) track.appendChild(el("div", { class: "margin-point", style: `left:${cur * 100}%;background:var(--accent)` }));
        }
        tbody.appendChild(
          el("tr", { class: `diff-row ${d.classification}` }, [
            el("td", { class: "task-cell" }, [d.task_id]),
            el("td", { class: "num" }, [base != null ? fmtPct(base) : "-"]),
            el("td", { class: "num" }, [cur != null ? fmtPct(cur) : "-"]),
            el("td", {}, [el("div", { class: "margin-bar" }, [track, el("span", { class: "num small" }, [`±${(margin * 100).toFixed(1)}pp`])])]),
            el("td", {}, [el("span", { class: `pill verdict-pill ${d.classification === "regressed" ? "stable-fail" : d.classification === "improved" ? "stable-pass" : "unknown"}` }, [d.classification])]),
          ])
        );
      }
      table.appendChild(tbody);
      resultSlot.appendChild(el("div", { class: "grid-wrap" }, [table]));
      resultSlot.appendChild(
        el("p", { class: "muted small", style: "margin-top:8px" }, [
          "Margin = z * sqrt(p*(1-p)/n) from the baseline's own measured pass rate and seed count -- a task that always passed gets zero slack, a flaky task gets a wide band, so normal seed-to-seed variance never trips the gate.",
        ])
      );
    } catch (e) {
      toast("Gate run failed: " + e.message, true);
    }
  }
}

// ================================================================== CASSETTES

async function renderCassettesView() {
  const v = document.getElementById("view-cassettes");
  v.innerHTML = "";
  v.appendChild(el("h1", {}, ["Cassette browser"]));

  const data = await api("/api/cassettes");
  v.appendChild(el("p", { class: "muted small" }, [`${data.cassettes.length} cassette(s) in ${data.cassette_dir}`]));

  const table = el("table", { class: "grid" });
  table.appendChild(el("thead", {}, [el("tr", {}, [el("th", {}, ["Task"]), el("th", {}, ["Seed"]), el("th", {}, ["File"]), el("th", {}, ["Size"]), el("th", {}, ["Actions"])])]));
  const tbody = el("tbody", {});
  for (const c of data.cassettes) {
    const resultCell = el("span", { class: "muted small" }, ["-"]);
    tbody.appendChild(
      el("tr", {}, [
        el("td", { class: "task-cell" }, [c.task_id]),
        el("td", { class: "num" }, [c.seed == null ? "-" : String(c.seed)]),
        el("td", { class: "mono small" }, [c.file]),
        el("td", { class: "num" }, [`${c.size_bytes} B`]),
        el("td", {}, [
          el("button", { class: "btn small", onclick: () => viewCassette(c.file) }, ["View"]),
          el("button", { class: "btn small", onclick: () => replayCassette(c.file, resultCell) }, ["Replay offline"]),
          resultCell,
        ]),
      ])
    );
  }
  table.appendChild(tbody);
  v.appendChild(el("div", { class: "grid-wrap" }, [table]));

  async function viewCassette(file) {
    const data = await api(`/api/cassettes/${encodeURIComponent(file)}`);
    const backdrop = ensureDrawer();
    const drawer = document.getElementById("drawer");
    drawer.innerHTML = "";
    drawer.appendChild(el("button", { class: "btn small drawer-close", onclick: closeDrawer }, ["Close"]));
    drawer.appendChild(el("h2", {}, [file]));
    for (const ev of data.events) {
      if (!["llm_call", "tool_call", "llm_result", "tool_result", "run_start", "run_end"].includes(ev.type)) continue;
      const payload = ev.payload.request || ev.payload.response || ev.payload;
      drawer.appendChild(el("div", { class: "trace-step" }, [el("span", { class: "kind" }, [ev.type]), el("pre", {}, [JSON.stringify(payload, null, 2)])]));
    }
    backdrop.classList.add("open");
  }

  async function replayCassette(file, resultCell) {
    resultCell.textContent = "replaying...";
    try {
      const result = await api(`/api/cassettes/${encodeURIComponent(file)}/replay`, { method: "POST", body: JSON.stringify({ backend: "gemini", on_divergence: "passthrough" }) });
      if (!result.ok) {
        resultCell.textContent = "error";
        resultCell.style.color = "var(--critical)";
        toast(result.error, true);
        return;
      }
      resultCell.textContent = result.diverged ? "DIVERGED" : result.passed ? "clean, PASS" : "clean, FAIL";
      resultCell.style.color = result.diverged ? "var(--warn)" : result.passed ? "var(--safe)" : "var(--critical)";
    } catch (e) {
      resultCell.textContent = "error";
      toast("Replay failed: " + e.message, true);
    }
  }
}

// ================================================================== boot

function buildViews() {
  Views.innerHTML = "";
  for (const name of TABS) {
    Views.appendChild(el("section", { class: "view" + (name === "author" ? " active" : ""), id: "view-" + name }));
  }
}

async function boot() {
  setupTabs();
  buildViews();
  await loadModeBanner();
  state.suite = await api("/api/suite");
  renderAuthorView();
  renderRunView();
  await renderCalibrationView();
}

boot().catch((e) => toast("Load failed: " + e.message, true));
