"use strict";
// The stickman review page (spec §12): plain JavaScript, no build step. It shows GET /api/project and fetches it
// again on every server-sent event; every change goes through the API with the X-Stickman: 1 header.

const VIEWS = ["plan", "sheets", "tests", "gallery"];
const STATUS_TEXT = {
  planned: "Planned", generating: "Generating", generated: "Generated", needs_review: "Needs review",
  approved: "Approved", failed: "Failed", stale: "Stale",
};
const JOB_VERBS = { regenerate: "regenerating", replan: "replanning" };
const ui = { data: null, view: "gallery", focus: null, overlay: null, editHash: null };

// --- small helpers (exported for the tests) ---

function formatTime(seconds) {
  const tenths = Math.floor(seconds * 10 + 0.5);
  const minutes = Math.floor(tenths / 600);
  const rest = tenths - minutes * 600;
  return `${minutes}:${String(Math.floor(rest / 10)).padStart(2, "0")}.${rest % 10}`;
}

function money(usd) {
  return usd > 0 && usd < 0.1 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

function describeJob(job) {
  if (job.kind === "candidates") return `making ${job.unit} candidates`;
  return `${JOB_VERBS[job.kind] || job.kind} ${job.unit}`;
}

function capital(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function h(tag, attrs, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (key === "class") element.className = value;
    else if (value === true) element.setAttribute(key, "");
    else element.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    element.append(child instanceof Node ? child : String(child));
  }
  return element;
}

// --- talking to the server ---

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-Stickman"] = "1";
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function say(text, kind) {
  const notice = document.getElementById("notice");
  notice.textContent = text || "";
  notice.hidden = !text;
  notice.dataset.kind = kind || "info";
}

async function act(method, path, body, done) {
  try {
    const data = await api(method, path, body);
    if (data.units) ui.data = data;
    if (data.job) say(`${capital(describeJob(data.job))}…`, "info");
    else if (done) say(done, "ok");
    render();
    return data;
  } catch (error) {
    say(error.message, "error");
    return null;
  }
}

async function refresh() {
  try {
    ui.data = await api("GET", "/api/project");
  } catch (error) {
    say(`Can't reach the review server (${error.message}). Is \`stickman review\` still running?`, "error");
    return;
  }
  render();
}

function listen() {
  const events = new EventSource("/api/events");
  events.addEventListener("plan", () => refresh());
  events.addEventListener("state", () => refresh());
  events.addEventListener("job", (event) => {
    const job = JSON.parse(event.data);
    if (job.state === "started") say(`${capital(describeJob(job))}…`, "info");
    else if (job.state === "finished") say(job.message || "Done.", "ok");
    else if (job.state === "paused") say(job.message, "warn");
    else if (job.state === "failed") say(job.message, "error");
    refresh();
  });
  events.addEventListener("budget", (event) => say(JSON.parse(event.data).message, "warn"));
}

// --- what's shown ---

function unitsById() {
  return Object.fromEntries((ui.data ? ui.data.units : []).map((unit) => [unit.id, unit]));
}

function viewIds() {
  if (!ui.data) return [];
  return ui.view === "tests" ? ui.data.test_units : ui.data.order;
}

function focusedUnit() {
  return unitsById()[ui.focus] || null;
}

function currentVersion(unit) {
  return unit.versions.find((version) => version.v === unit.current_version) || null;
}

function badge(status) {
  return h("span", { class: "badge", "data-status": status }, STATUS_TEXT[status] || status);
}

function verdict(qc) {
  if (!qc) return h("span", { class: "muted" }, "not checked yet");
  const idea = qc.score ? `, idea ${qc.score}/5` : "";
  return qc.passed
    ? h("span", { class: "pass" }, `passed QC${idea}`)
    : h("span", { class: "fail" }, `failed QC: ${qc.reason.replace(/_/g, " ")}${idea}`);
}

function render() {
  const data = ui.data;
  if (!data) return;
  document.getElementById("project").textContent = data.project;
  document.title = `${data.project}: stickman review`;
  const budget = document.getElementById("budget");
  budget.textContent = data.budget ? data.budget.text : "";
  budget.classList.toggle("warn", Boolean(data.budget && data.budget.warn));
  for (const tab of document.querySelectorAll(".tabs button")) {
    tab.setAttribute("aria-current", tab.dataset.view === ui.view ? "page" : "false");
  }
  const ids = viewIds();
  if (!ids.includes(ui.focus)) ui.focus = ids[0] || null;
  const body = { plan: planView, sheets: sheetsView, tests: testsView, gallery: galleryView }[ui.view](data);
  document.getElementById("main").replaceChildren(problemsPanel(data), body);
  if (ui.overlay !== "prompt") renderOverlay(); // never redraw the editor over what's being typed
  const focused = document.querySelector(".queue-item.focused");
  if (focused) focused.scrollIntoView({ block: "nearest" });
}

function problemsPanel(data) {
  if (!data.problems || !data.problems.length) return "";
  return h("section", { class: "panel errors" }, h("h2", {}, "Something needs fixing before this page is complete"),
    h("ul", {}, data.problems.map((problem) => h("li", {}, problem))));
}

// --- Plan ---

function planView(data) {
  const approved = data.approvals.plan;
  return h("div", { class: "plan" },
    data.errors.length
      ? h("section", { class: "panel errors" }, h("h2", {}, "plan.yaml has errors"),
          h("p", { class: "muted" }, "Fix them in plan.yaml; this page reloads when you save. It shows the last valid plan meanwhile."),
          h("ul", {}, data.errors.map((error) => h("li", {}, h("code", {}, error)))))
      : "",
    h("div", { class: "toolbar" },
      h("button", {
        type: "button", class: "primary", disabled: approved || data.errors.length > 0,
        onclick: () => act("POST", "/api/plan/approve", undefined, "Plan approved."),
      }, approved ? "Plan approved" : "Approve plan"),
      h("span", { class: "muted" }, `${data.units.length} units, ${formatTime(data.duration_end || 0)} long`)),
    estimatePanel(data.estimate),
    correctionsPanel(data),
    castPanel(data.cast),
    h("section", { class: "panel" }, h("h2", {}, "Units"), h("div", { class: "table-scroll" }, unitsTable(data.units))));
}

function estimatePanel(estimate) {
  if (!estimate) return "";
  const row = (label, value) => [h("dt", {}, label), h("dd", {}, value)];
  return h("section", { class: "panel" }, h("h2", {}, "Cost and time for the whole video"),
    h("p", { class: "total" }, `${money(estimate.total_usd)}, about ${estimate.minutes} min`),
    h("dl", { class: "costs" },
      row(`${estimate.units} images`, money(estimate.images_usd)), row("Their checks", money(estimate.checks_usd)),
      row("Extras' sheets", money(estimate.sheets_usd)), row("Planning (done)", money(estimate.llm_usd))),
    h("p", { class: "muted" }, `About ${estimate.neurons.toLocaleString()} neurons, retries included. ${estimate.free_note}`));
}

function correctionsPanel(data) {
  const corrections = data.corrections.length
    ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Scene"), h("th", {}, "Heard"), h("th", {}, "Corrected to"), h("th", {}, "Why"))),
        h("tbody", {}, data.corrections.map((c) => h("tr", {}, h("td", {}, c.scene), h("td", {}, c.from), h("td", {}, c.to), h("td", {}, c.reason)))))
    : h("p", { class: "muted" }, "No corrections.");
  const merges = data.merge_check.length
    ? h("ul", {}, data.merge_check.map((m) => h("li", {}, `Line ${m.line}: the rules say ${m.rules}, the LLM says ${m.llm}.`)))
    : h("p", { class: "muted" }, "The LLM and the fragment rules agree on every merge.");
  return h("section", { class: "panel" }, h("h2", {}, "Corrections"), corrections, h("h2", {}, "Check merges"), merges);
}

function castPanel(cast) {
  return h("section", { class: "panel" }, h("h2", {}, "Cast"),
    h("ul", {}, cast.map((member) => h("li", {}, h("strong", {}, member.name),
      ` (${member.figures} figure${member.figures === 1 ? "" : "s"}${member.sheet ? ", has a sheet" : ""}): ${member.description}`))));
}

function unitsTable(units) {
  return h("table", { class: "units" },
    h("thead", {}, h("tr", {}, ["Time", "Part", "Text", "Corrected", "Visual idea", "Shot", "Characters", "Notes"].map((t) => h("th", {}, t)))),
    h("tbody", {}, units.map((unit) => h("tr", {},
      h("td", {}, formatTime(unit.start)), h("td", {}, unit.part || ""), h("td", { class: "text" }, unit.source_text),
      h("td", { class: "text" }, unit.corrected_text === unit.source_text ? "" : unit.corrected_text),
      h("td", { class: "text" }, unit.visual_idea), h("td", {}, unit.shot),
      h("td", {}, unit.characters.map((c) => c.ref).join(", ")),
      h("td", {}, unitTags(unit))))));
}

function unitTags(unit) {
  return [
    unit.prompt_locked ? h("span", { class: "tag" }, "locked") : "",
    unit.softened ? h("span", { class: "tag" }, "softened") : "",
    unit.split === "split" ? h("span", { class: "tag" }, "split") : "",
    unit.split === "no_valid_cut" ? h("span", { class: "tag" }, "no valid cut") : "",
  ];
}

// --- Sheets ---

function sheetsView(data) {
  const boot = data.bootstrap;
  if (!boot) return h("p", { class: "empty" }, "Bootstrap's candidates can't be read; see the problem above.");
  // While candidates are being made, their job's store would save over an approval: wait for it.
  const making = Boolean(data.job && data.job.kind === "candidates");
  return h("div", { class: "sheets" },
    stepSection("anchor", "Style anchor", boot.anchor, boot.anchor_done,
      "Every image takes its line weight and look from the anchor. Approve one, then make the mascot sheet.", making),
    stepSection("mascot", "Mascot sheet", boot.mascot, boot.mascot_done,
      "The main character's head and hair, sent with every scene he's in.", making),
    h("section", { class: "panel" }, h("h2", {}, "Extras"),
      h("p", { class: "muted" }, "Extras are drawn from their description until their sheets arrive (M7)."),
      h("ul", {}, data.cast.filter((m) => m.id !== "mascot").map((m) => h("li", {}, h("strong", {}, m.name), `: ${m.description}`)))));
}

function stepSection(step, title, state, done, blurb, making) {
  const cards = state.candidates.map((c) => h("figure", { class: `candidate${c.approved ? " approved" : ""}` },
    h("div", { class: "paper" }, h("img", { src: c.url, alt: `${title} candidate ${c.n}`, loading: "lazy" })),
    h("figcaption", {}, h("strong", {}, `c${c.n} `), verdict(c.qc),
      c.previous_anchor ? h("span", { class: "note" }, ". Made with a previous anchor") : ""),
    c.approved
      ? h("span", { class: "tag ok" }, "Approved")
      : h("button", {
          type: "button", disabled: c.previous_anchor || making,
          onclick: () => act("POST", `/api/sheets/${step}/approve`, { candidate: c.n }, `Approved ${title.toLowerCase()} c${c.n}.`),
        }, "Approve")));
  return h("section", { class: "panel" },
    h("h2", {}, title, done ? " " : "", done ? h("span", { class: "tag ok" }, "done") : ""),
    h("p", { class: "muted" }, blurb),
    cards.length ? h("div", { class: "candidates" }, cards) : h("p", { class: "empty" }, "No candidates yet."),
    h("button", {
      type: "button", title: "Spends neurons: two images and their checks", disabled: making,
      onclick: () => act("POST", `/api/sheets/${step}/regenerate`),
    }, "Make 2 more"));
}

// --- Tests and Gallery ---

function testsView(data) {
  if (!data.test_units.length) {
    return h("section", { class: "panel" }, h("h2", {}, "Test images"),
      h("p", { class: "empty" }, "No test units yet. From M7, three units are made first and wait here for approval before the full batch."));
  }
  return h("div", {},
    h("div", { class: "toolbar" }, h("button", {
      type: "button", class: "primary", disabled: data.approvals.tests,
      onclick: () => act("POST", "/api/tests/approve", undefined, "Tests approved."),
    }, data.approvals.tests ? "Tests approved" : "Approve tests and start the batch"), shortcutsHint()),
    galleryBody(data, data.test_units));
}

function galleryView(data) {
  const remaining = data.units.filter((unit) => unit.status === "generated").length;
  return h("div", {},
    timeline(data),
    h("div", { class: "toolbar" },
      h("button", {
        type: "button", disabled: remaining === 0,
        onclick: () => act("POST", "/api/units/approve-remaining", undefined, `Approved ${remaining} unit(s).`),
      }, remaining ? `Approve all remaining (${remaining})` : "Nothing left to approve"),
      shortcutsHint()),
    galleryBody(data, data.order));
}

function shortcutsHint() {
  return h("span", { class: "shortcuts" }, h("kbd", {}, "A"), " approve  ", h("kbd", {}, "R"), " regenerate  ",
    h("kbd", {}, "E"), " edit prompt  ", h("kbd", {}, "H"), " history  ", h("kbd", {}, "J"), h("kbd", {}, "K"), " next and previous  ",
    h("kbd", {}, "1"), h("kbd", {}, "2"), " keep left or right");
}

function timeline(data) {
  return h("div", { class: "timeline", role: "list", "aria-label": "Units along the video" },
    data.units.map((unit) => {
      const segment = h("button", {
        type: "button", role: "listitem", class: `segment${unit.id === ui.focus ? " focused" : ""}`,
        "data-status": unit.status,
        title: `${unit.id} at ${formatTime(unit.start)}: ${STATUS_TEXT[unit.status]}`,
        "aria-label": `${unit.id} at ${formatTime(unit.start)}, ${STATUS_TEXT[unit.status]}`,
        onclick: () => focusUnit(unit.id),
      });
      // Through the CSSOM: the Content-Security-Policy blocks style="" attributes, not this.
      segment.style.flexGrow = String(Math.max(unit.end - unit.start, 0.1));
      return segment;
    }));
}

function galleryBody(data, ids) {
  const units = unitsById();
  const list = ids.map((id) => units[id]).filter(Boolean);
  if (!list.length) return h("p", { class: "empty" }, "No units to show.");
  return h("div", { class: "gallery" },
    h("ol", { class: "queue", "aria-label": "Units, flagged first" }, list.map(queueItem)),
    detail(units[ui.focus] || list[0]));
}

function queueItem(unit) {
  const current = currentVersion(unit);
  const focused = unit.id === ui.focus;
  return h("li", {}, h("button", {
    type: "button", class: `queue-item${focused ? " focused" : ""}`, "aria-current": focused ? "true" : null,
    onclick: () => focusUnit(unit.id),
  },
    current ? h("img", { src: current.url, alt: "", loading: "lazy" }) : h("span", { class: "no-image" }, "no image"),
    h("span", { class: "queue-text" }, h("span", { class: "queue-id" }, unit.id),
      h("span", { class: "queue-time" }, formatTime(unit.start)), badge(unit.status))));
}

function detail(unit) {
  const current = currentVersion(unit);
  const earlier = unit.compare_with ? unit.versions.find((version) => version.v === unit.compare_with) : null;
  let stage;
  if (earlier && current) {
    stage = h("div", { class: "compare" }, pick(unit, earlier, 1, "Previous"), pick(unit, current, 2, "New"));
  } else if (current) {
    stage = h("div", { class: "paper" }, h("img", { src: current.url, alt: `${unit.id}: ${unit.visual_idea}` }));
  } else {
    const why = unit.status === "failed" ? `No image: ${unit.error || "the request failed"}.` : "No image yet: `stickman generate` makes it.";
    stage = h("div", { class: "paper empty-image" }, h("p", {}, why));
  }
  const qc = current ? current.qc : null;
  return h("article", { class: "detail", "aria-label": `Unit ${unit.id}` },
    h("div", { class: "detail-head" },
      h("h2", {}, unit.id), h("span", { class: "muted" }, `${formatTime(unit.start)} to ${formatTime(unit.end)}`),
      unit.part ? h("span", { class: "muted" }, `part ${unit.part}`) : "", badge(unit.status), unitTags(unit)),
    stage,
    h("div", { class: "words" },
      h("p", { class: "idea" }, unit.visual_idea),
      h("p", {}, unit.corrected_text),
      unit.corrected_text !== unit.source_text ? h("p", { class: "muted" }, `Heard as: ${unit.source_text}`) : "",
      current ? h("p", {}, verdict(qc), qc && qc.notes ? `. ${qc.notes}` : "") : "",
      unit.status === "needs_review" && unit.review_reason ? h("p", { class: "fail" }, `Needs review: ${unit.review_reason.replace(/_/g, " ")}`) : "",
      unit.error && unit.status !== "failed" ? h("p", { class: "muted" }, unit.error) : ""),
    h("div", { class: "actions" },
      h("button", { type: "button", class: "primary", disabled: !current || unit.status === "approved", onclick: () => approve(unit) },
        unit.status === "approved" ? "Approved" : "Approve"),
      h("button", { type: "button", title: "Spends neurons: a new image, its check and any retries", onclick: () => regenerate(unit) }, "Regenerate"),
      h("button", { type: "button", onclick: () => openOverlay("prompt") }, "Edit prompt"),
      h("button", { type: "button", disabled: !unit.versions.length, onclick: () => openOverlay("history") }, `History (${unit.versions.length})`),
      replanForm(unit)));
}

function pick(unit, version, key, label) {
  return h("figure", { class: "pick" },
    h("div", { class: "paper" }, h("img", { src: version.url, alt: `${label} image of ${unit.id}, version ${version.v}` })),
    h("figcaption", {}, `${key}: ${label}, v${version.v}, `, verdict(version.qc)),
    h("button", { type: "button", onclick: () => choose(unit, version.v) }, `Keep this one (${key})`));
}

function replanForm(unit) {
  const input = h("input", { type: "text", name: "hint", placeholder: "Replan with a hint, for example: show it at night", "aria-label": `Hint for replanning ${unit.id}` });
  return h("form", {
    class: "replan",
    onsubmit: (event) => { event.preventDefault(); replan(unit, input.value); },
  }, input, h("button", { type: "submit" }, "Replan"));
}

function focusUnit(id) {
  ui.focus = id;
  render();
}

function step(delta) {
  const ids = viewIds();
  if (!ids.length) return;
  const index = Math.max(0, ids.indexOf(ui.focus));
  focusUnit(ids[(index + delta + ids.length) % ids.length]);
}

// --- changes ---

function approve(unit) {
  return act("POST", `/api/units/${unit.id}/approve`, undefined, `Approved ${unit.id}.`);
}

function regenerate(unit) {
  return act("POST", `/api/units/${unit.id}/regenerate`);
}

function choose(unit, v) {
  return act("POST", `/api/units/${unit.id}/select-version`, { v }, `${unit.id} now shows version ${v}.`);
}

function replan(unit, hint) {
  return act("POST", `/api/units/${unit.id}/replan`, { hint });
}

// --- the history and the prompt editor ---

function openOverlay(kind) {
  const unit = focusedUnit();
  if (!unit || (kind === "history" && !unit.versions.length)) return;
  ui.overlay = kind;
  if (kind === "prompt") ui.editHash = ui.data.plan_hash; // the plan as it was when the editor opened (spec §12.4)
  renderOverlay();
}

function closeOverlay() {
  ui.overlay = null;
  renderOverlay();
}

function renderOverlay() {
  const root = document.getElementById("overlay");
  const unit = focusedUnit();
  if (!ui.overlay || !unit) {
    root.hidden = true;
    root.replaceChildren();
    return;
  }
  root.hidden = false;
  root.replaceChildren(ui.overlay === "history" ? historyDialog(unit) : promptDialog(unit));
  const first = root.querySelector("[data-autofocus]");
  if (first) first.focus();
}

function historyDialog(unit) {
  const versions = [...unit.versions].reverse();
  return h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": `History of ${unit.id}` },
    h("h2", {}, `Every version of ${unit.id}`),
    h("div", { class: "history" }, versions.map((version, index) => h("figure", {},
      h("div", { class: "paper" }, h("img", { src: version.url, alt: `${unit.id} version ${version.v}`, loading: "lazy" })),
      h("figcaption", {}, h("strong", {}, `v${version.v} `), verdict(version.qc),
        version.v === unit.approved_version ? h("span", { class: "tag ok" }, " approved") : ""),
      version.v === unit.current_version
        ? h("span", { class: "tag" }, "Shown now")
        : h("button", {
            type: "button", "data-autofocus": index === 0 ? true : null,
            onclick: async () => { closeOverlay(); await choose(unit, version.v); },
          }, "Show this one")))),
    h("div", { class: "actions" }, h("button", { type: "button", onclick: closeOverlay, "data-autofocus": true }, "Close")));
}

function promptDialog(unit) {
  const area = h("textarea", { id: "prompt-text", "aria-label": `Image prompt of ${unit.id}`, "data-autofocus": true });
  area.value = unit.image_prompt;
  return h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": `Edit the prompt of ${unit.id}` },
    h("h2", {}, `Image prompt of ${unit.id}`),
    h("p", { class: "muted" }, unit.prompt_locked
      ? "This prompt is locked: field changes in plan.yaml don't rebuild it."
      : "Saving locks the prompt, so field changes in plan.yaml won't rebuild it. `stickman rebuild-prompt` unlocks it."),
    area,
    h("div", { class: "actions" },
      h("button", { type: "button", class: "primary", onclick: savePrompt }, "Save prompt"),
      h("button", { type: "button", onclick: closeOverlay }, "Cancel"),
      h("span", { class: "shortcuts" }, h("kbd", {}, "Ctrl"), "+", h("kbd", {}, "Enter"), " saves")));
}

async function savePrompt() {
  const unit = focusedUnit();
  const area = document.getElementById("prompt-text");
  if (!unit || !area) return;
  const saved = await act("PUT", `/api/units/${unit.id}/prompt`, { prompt: area.value, plan_hash: ui.editHash }, `Saved and locked the prompt of ${unit.id}.`);
  if (saved) closeOverlay();
}

// --- keyboard (spec §12.3) ---

function onKey(event) {
  if (event.key === "Escape") {
    if (ui.overlay) {
      event.preventDefault();
      closeOverlay();
    }
    return;
  }
  const typing = event.target.closest && event.target.closest("input, textarea, select");
  if (typing) {
    if (ui.overlay === "prompt" && event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      savePrompt();
    }
    return;
  }
  if (ui.overlay || event.ctrlKey || event.metaKey || event.altKey) return;
  if (ui.view !== "gallery" && ui.view !== "tests") return;
  const unit = focusedUnit();
  const shortcuts = {
    a: () => unit && approve(unit),
    r: () => unit && regenerate(unit),
    e: () => openOverlay("prompt"),
    h: () => openOverlay("history"),
    j: () => step(1),
    ArrowRight: () => step(1),
    k: () => step(-1),
    ArrowLeft: () => step(-1),
    "1": () => unit && unit.compare_with && choose(unit, unit.compare_with),
    "2": () => unit && unit.compare_with && choose(unit, unit.current_version),
  };
  const handler = shortcuts[event.key.length === 1 ? event.key.toLowerCase() : event.key];
  if (handler) {
    event.preventDefault();
    handler();
  }
}

function start() {
  const wanted = location.hash.slice(1);
  if (VIEWS.includes(wanted)) ui.view = wanted;
  for (const tab of document.querySelectorAll(".tabs button")) {
    tab.addEventListener("click", () => {
      ui.view = tab.dataset.view;
      history.replaceState(null, "", `#${ui.view}`);
      render();
    });
  }
  document.addEventListener("keydown", onKey);
  document.getElementById("overlay").addEventListener("click", (event) => {
    if (event.target.id === "overlay") closeOverlay();
  });
  refresh();
  listen();
}

if (typeof document !== "undefined") start();
if (typeof module !== "undefined") module.exports = { formatTime, money, describeJob };
