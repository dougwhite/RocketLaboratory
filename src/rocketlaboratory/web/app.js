"use strict";
const $ = (id) => document.getElementById(id);
let token = "",
  presets = {},
  spec = null,
  jsonMode = false,
  selected = null,
  lastReport = null;
const axisNames = [
  "throttle",
  "steer",
  "pitch",
  "yaw",
  "roll",
  "dodge_forward",
  "dodge_strafe",
];
const buttonNames = ["jump", "handbrake", "activate_boost", "holding_boost"];
const pretty = (s) => s.replaceAll("_", " ");
axisNames.forEach((name) => {
  const label = document.createElement("label");
  label.textContent = pretty(name);
  const input = document.createElement("input");
  input.id = "control-" + name;
  input.type = "number";
  input.min = "-1";
  input.max = "1";
  input.step = ".05";
  input.value = "0";
  label.append(input);
  $("axes").append(label);
});
buttonNames.forEach((name) => {
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.id = "control-" + name;
  input.type = "checkbox";
  label.append(input, document.createTextNode(pretty(name)));
  $("buttons").append(label);
});
async function api(path, body) {
  const options =
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-RocketLab-Token": token,
          },
          body: JSON.stringify(body),
        };
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw Error(value.error || "Request failed");
  return value;
}
function feedback(text, error = false) {
  $("feedback").textContent = text;
  $("feedback").style.color = error ? "#ec9b90" : "#73d9e6";
}
function load(specification, key) {
  spec = structuredClone(specification);
  $("title").textContent = spec.name;
  $("json").value = JSON.stringify(spec, null, 2);
  fill();
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.key === key));
  feedback("");
}
function fill() {
  const actor = spec.actors[0],
    segment = actor.segments[0] || {
      start_step: 0,
      duration_steps: spec.duration_steps,
      controls: {},
    };
  $("name").value = spec.name;
  $("duration").value = spec.duration_steps;
  $("preparation").value = spec.preparation;
  $("actor").value = actor.car_index;
  $("start").value = segment.start_step;
  $("hold").value = segment.duration_steps;
  axisNames.forEach(
    (n) => ($("control-" + n).value = segment.controls[n] || 0),
  );
  buttonNames.forEach(
    (n) => ($("control-" + n).checked = !!segment.controls[n]),
  );
  timeline();
}
function timeline() {
  const total = Number($("duration").value),
    start = Number($("start").value),
    count = Number($("hold").value);
  $("timeline-range").textContent = `${start} → ${start + count}`;
  $("timeline-block").style.left = `${Math.min(100, (start / total) * 100)}%`;
  $("timeline-block").style.width =
    `${Math.max(0, Math.min(100 - (start / total) * 100, (count / total) * 100))}%`;
}
function current() {
  if (jsonMode) return JSON.parse($("json").value);
  const next = structuredClone(spec),
    controls = {};
  axisNames.forEach((n) => {
    const v = Number($("control-" + n).value);
    if (v) controls[n] = v;
  });
  buttonNames.forEach((n) => {
    if ($("control-" + n).checked) controls[n] = true;
  });
  next.name = $("name").value;
  next.duration_steps = Number($("duration").value);
  next.preparation = $("preparation").value;
  next.actors[0].car_index = Number($("actor").value);
  const first = {
    start_step: Number($("start").value),
    duration_steps: Number($("hold").value),
    controls,
  };
  if (next.actors[0].segments.length) next.actors[0].segments[0] = first;
  else if (Object.keys(controls).length) next.actors[0].segments = [first];
  return next;
}
function mode(json) {
  try {
    spec = current();
    $("json").value = JSON.stringify(spec, null, 2);
    jsonMode = json;
    fill();
    $("simple-editor").hidden = json;
    $("json-editor").hidden = !json;
    $("simple-tab").classList.toggle("selected", !json);
    $("json-tab").classList.toggle("selected", json);
  } catch (error) {
    feedback(error.message, true);
  }
}
$("simple-tab").onclick = () => mode(false);
$("json-tab").onclick = () => mode(true);
["duration", "start", "hold"].forEach((id) =>
  $(id).addEventListener("input", timeline),
);
$("validate").onclick = async () => {
  try {
    await api("/api/validate", current());
    feedback("Valid definition · neutral gaps · bounded input values");
  } catch (error) {
    feedback(error.message, true);
  }
};
$("run").onclick = async () => {
  try {
    $("run").disabled = true;
    const job = await api("/api/run", {
      experiment: current(),
      backend: $("backend").value,
    });
    selected = job.id;
    lastReport = null;
    clearReport();
    feedback("Run submitted. Waiting for observation stream.");
    await refresh();
  } catch (error) {
    feedback(error.message, true);
  } finally {
    $("run").disabled = false;
  }
};
$("backend").onchange = () => {
  const native = $("backend").value === "native";
  $("source-notice").querySelector("strong").textContent = native
    ? "Native offline instrument"
    : "Synthetic demo session";
  $("source-notice").querySelector("p").textContent = native
    ? "Records real game hooks. Solver phase, camera synchronization and hidden-state restoration remain unqualified."
    : "Verifies the toolkit and charts. These signals are generated fixtures, not Rocket League measurements.";
};
function text(tag, value, cls) {
  const node = document.createElement(tag);
  node.textContent = value;
  if (cls) node.className = cls;
  return node;
}
async function refresh() {
  const jobs = await api("/api/jobs");
  $("job-count").textContent = `${jobs.length} RUNS`;
  $("jobs").replaceChildren();
  if (!jobs.length)
    $("jobs").append(
      text("p", "No experiments yet. Your first run starts here.", "empty"),
    );
  for (const job of jobs) {
    const node = document.createElement("div");
    node.className = "job" + (job.id === selected ? " active" : "");
    node.tabIndex = 0;
    node.setAttribute("role", "button");
    const row = text("div", "", "job-row");
    row.append(
      text("span", job.name, "job-title"),
      text("span", job.state, "state " + job.state),
    );
    const meta = text("div", "", "job-meta");
    meta.append(
      text("span", job.backend === "demo" ? "SYNTHETIC" : "NATIVE HOOKS"),
      text("span", new Date(job.created * 1000).toLocaleTimeString()),
    );
    node.append(row, meta);
    if (job.error) node.append(text("div", job.error, "job-error"));
    if (["running", "queued"].includes(job.state)) {
      const cancel = text("button", "Cancel run", "cancel");
      cancel.onclick = async (event) => {
        event.stopPropagation();
        try {
          await api("/api/cancel", { job_id: job.id });
          feedback("Cancellation requested.");
        } catch (error) {
          feedback(error.message, true);
        }
      };
      node.append(cancel);
    }
    node.onclick = () => {
      selected = job.id;
      lastReport = null;
      clearReport();
      refresh().catch((e) => feedback(e.message, true));
    };
    node.onkeydown = (e) => {
      if (e.key === "Enter") node.click();
    };
    $("jobs").append(node);
  }
  if (!selected && jobs.length) selected = jobs[0].id;
  const chosen = jobs.find((j) => j.id === selected);
  if (chosen?.state === "complete" && lastReport !== selected) {
    const result = await api(`/api/jobs/${selected}/report`);
    $("observation-title").textContent = "Observations · " + chosen.name;
    showReport(result);
    lastReport = selected;
    feedback(
      "Capture complete · " +
        (chosen.backend === "demo" ? "synthetic fixture" : "native hooks") +
        " · timing unqualified",
    );
  }
  $("run").disabled = jobs.some((j) => ["running", "queued"].includes(j.state));
}
function clearReport() {
  $("records").textContent = "—";
  $("integrity").textContent = "—";
  $("result-source").textContent = "AWAITING CAPTURE";
  $("download").hidden = true;
  $("viewer-note").textContent =
    "Waiting for selected run. No previous observations are shown.";
  plot("position-chart", []);
  plot("input-chart", []);
}
function showReport(result) {
  $("records").textContent = result.record_count.toLocaleString();
  $("integrity").textContent = result.integrity_complete
    ? "Complete"
    : "Incomplete";
  $("result-source").textContent =
    result.source === "synthetic_demo"
      ? "SYNTHETIC FIXTURE"
      : "NATIVE · UNQUALIFIED";
  $("download").hidden = false;
  $("download").href = `/api/jobs/${selected}/capture`;
  $("viewer-note").textContent =
    result.clock + ". Preview is decimated; use raw capture for analysis.";
  const rows = result.preview;
  const points = (kind, field, index) =>
    rows
      .filter((r) => r.kind === kind && r.fields[field] !== undefined)
      .map((r) => [
        r.engine_frame,
        index === undefined ? r.fields[field] : r.fields[field][index],
      ]);
  plot("position-chart", [
    { data: points("body", "location_uu", 0), color: "#73d9e6" },
    { data: points("camera", "location_uu", 0), color: "#bc9af1" },
    { data: points("body", "location_uu", 2), color: "#e3b47a" },
  ]);
  plot(
    "input-chart",
    [
      { data: points("input_requested", "throttle"), color: "#73d9e6" },
      { data: points("input_requested", "steer_input"), color: "#e3b47a" },
    ],
    [-1.1, 1.1],
  );
}
function plot(id, series, fixed) {
  const canvas = $(id),
    rect = canvas.getBoundingClientRect(),
    dpr = devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = 180 * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const w = rect.width,
    h = 180,
    l = 43,
    r = 14,
    t = 15,
    b = 25;
  ctx.clearRect(0, 0, w, h);
  const all = series.flatMap((s) => s.data);
  if (!all.length) {
    ctx.fillStyle = "#546c80";
    ctx.font = "11px system-ui";
    ctx.fillText("Awaiting capture", l, h / 2);
    return;
  }
  let xmin = Math.min(...all.map((p) => p[0])),
    xmax = Math.max(...all.map((p) => p[0]));
  let ymin = fixed ? fixed[0] : Math.min(...all.map((p) => p[1])),
    ymax = fixed ? fixed[1] : Math.max(...all.map((p) => p[1]));
  if (xmax === xmin) xmax = xmin + 1;
  if (ymax === ymin) ymax = ymin + 1;
  if (!fixed) {
    const pad = (ymax - ymin) * 0.08;
    ymin -= pad;
    ymax += pad;
  }
  const x = (v) => l + ((v - xmin) / (xmax - xmin)) * (w - l - r),
    y = (v) => h - b - ((v - ymin) / (ymax - ymin)) * (h - t - b);
  ctx.font = "9px ui-monospace,monospace";
  for (let i = 0; i <= 4; i++) {
    const yy = t + (i * (h - t - b)) / 4;
    ctx.strokeStyle = "#263543";
    ctx.beginPath();
    ctx.moveTo(l, yy);
    ctx.lineTo(w - r, yy);
    ctx.stroke();
    ctx.fillStyle = "#6d8599";
    ctx.fillText(
      (ymax - ((ymax - ymin) * i) / 4).toFixed(fixed ? 1 : 0),
      2,
      yy + 3,
    );
    const xx = l + (i * (w - l - r)) / 4;
    ctx.fillText(Math.round(xmin + ((xmax - xmin) * i) / 4), xx - 8, h - 5);
  }
  for (const s of series) {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    s.data.forEach((p, i) => {
      if (i) ctx.lineTo(x(p[0]), y(p[1]));
      else ctx.moveTo(x(p[0]), y(p[1]));
    });
    ctx.stroke();
  }
}
async function init() {
  try {
    const session = await api("/api/session");
    token = session.token;
    presets = session.presets;
    $("instrument").textContent = session.doctor.native_configured
      ? "Native attachment configured"
      : "Local research workspace";
    $("backend").querySelector("[value=native]").disabled =
      !session.doctor.native_configured;
    $("capability").textContent = session.doctor.native_configured
      ? "Native hooks · timing unqualified"
      : "Native instrument not configured";
    Object.entries(presets).forEach(([key, value], i) => {
      const button = document.createElement("button");
      button.dataset.key = key;
      button.append(
        text("span", String(i + 1).padStart(2, "0"), "number"),
        text("span", key === "flip-cancel" ? "Flip cancellation" : pretty(key)),
      );
      button.onclick = () => load(value, key);
      $("presets").append(button);
    });
    load(presets["throttle-step"], "throttle-step");
    plot("position-chart", []);
    plot("input-chart", []);
    await refresh();
    setInterval(
      () => refresh().catch((error) => feedback(error.message, true)),
      1500,
    );
  } catch (error) {
    feedback(error.message, true);
  }
}
init();
