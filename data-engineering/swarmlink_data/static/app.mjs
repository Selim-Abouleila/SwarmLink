// Use .mjs so Windows MIME mappings serve this module as JavaScript.
const $ = (id) => document.getElementById(id);
const colors = ["#65e2b1", "#80b7f9", "#b6a0f5", "#efbd72", "#ef7e85", "#65d8e2"];
const state = {snapshot: null, selected: "robot-01", run: "", socket: null, retry: 1000, lastMessage: 0, paused: false,
  stopped: false, controlToken: null, retryTimer: null, comparisonTimer: null};
// Flip the physical Robot 01 marker for the requested front/back comparison.
// This display setting never changes the received or exported measurements.
const headingOffsets = new Map([["robot-01", Math.PI]]);
const headingOffset = (robot) => state.snapshot?.mode === "live" && robot?.source === "ros2" ? headingOffsets.get(robot.robot_id) ?? 0 : 0;
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (value, places = 0) => value == null || !Number.isFinite(value) ? "—" : value.toLocaleString(undefined, {minimumFractionDigits: places, maximumFractionDigits: places});
const robotName = (id) => id.startsWith("robot-") ? `Robot ${id.slice(6)}` : id;
const colorFor = (id) => colors[Math.max(0, state.snapshot?.robots.findIndex(r => r.robot_id === id) ?? 0) % colors.length];
const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString([], {hour12: false});
function text(id, value) { $(id).textContent = value; }
function toast(message) { text("toast", message); $("toast").hidden = false; }
async function loadControl() {
  if (state.stopped) return;
  try {
    const response = await fetch("/api/control", {cache:"no-store", signal:AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error("Shutdown is available from the collector PC at http://127.0.0.1:8765.");
    const control = await response.json();
    if (state.stopped) return;
    state.controlToken = control.shutdown_available ? control.token : null;
    $("shutdown-button").disabled = !state.controlToken;
    $("shutdown-button").title = control.shutdown_available ? "Stop this dashboard collector; the robot keeps running" : "Restart using Start Dashboard.exe to enable shutdown";
  } catch (error) {
    state.controlToken = null; $("shutdown-button").disabled = true;
    $("shutdown-button").title = error.message;
  }
}
function showStopped() {
  if (state.stopped) return;
  state.stopped = true; state.controlToken = null;
  clearTimeout(state.retryTimer); clearInterval(state.comparisonTimer);
  if (state.socket) { state.socket.onclose = null; state.socket.close(); }
  document.body.classList.add("disconnected");
  $("connection").classList.add("waiting"); $("connection").querySelector("span").textContent = "Dashboard stopped";
  text("mode-tag", "STOPPED");
  text("mode-description", "Open Start Dashboard.exe in data-engineering to start again. The robot continues collecting data.");
  text("stream-label", "LAST SNAPSHOT"); text("last-update", "COLLECTOR STOPPED · DISPLAYING LAST RECEIVED DATA");
  text("shutdown-button", "Dashboard stopped"); $("shutdown-button").disabled = true;
  document.querySelectorAll("main button, main select").forEach(el => { el.disabled = true; });
  toast("Dashboard shut down. The next start opens a fresh telemetry session. The raw archive is kept.");
}
function connection(connected) {
  const box = $("connection"); box.classList.toggle("waiting", !connected);
  box.querySelector("span").textContent = connected ? "Stream connected" : "Reconnecting";
  document.body.classList.toggle("disconnected", !connected);
  text("stream-label", connected ? "LIVE" : "LAST SNAPSHOT");
}
function connect() {
  if (state.stopped) return;
  if (state.socket) { state.socket.onclose = null; state.socket.close(); }
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/stream${state.run ? `?run_id=${encodeURIComponent(state.run)}` : ""}`);
  state.socket = socket;
  socket.onopen = loadControl;
  socket.onmessage = (event) => {
    if (state.stopped) return;
    const snapshot = JSON.parse(event.data);
    const restarted = state.snapshot && state.snapshot.collection.id !== snapshot.collection.id;
    const reconnectLatest = restarted && Boolean(state.run);
    if (restarted) {
      state.run = "";
      $("comparison-rows").innerHTML = '<tr><td colspan="7" class="empty-cell">Waiting for this session’s experiment events</td></tr>';
    }
    state.snapshot = snapshot; state.lastMessage = Date.now(); state.retry = 1000;
    if (!state.snapshot.robots.some(r => r.robot_id === state.selected)) state.selected = state.snapshot.robots[0]?.robot_id;
    connection(true); render();
    if (state.snapshot.shutting_down) showStopped();
    else if (reconnectLatest) connect();
    if (restarted && !state.stopped) comparison();
  };
  socket.onerror = () => socket.close();
  socket.onclose = () => { if (state.stopped) return; connection(false); state.retryTimer = setTimeout(connect, state.retry); state.retry = Math.min(10000, state.retry * 2); };
}
function render() {
  if (state.stopped) return;
  const s = state.snapshot, p = s.pipeline, selectedRun = s.runs.find(r => r.run_id === s.run_id);
  const demo = s.mode === "demo";
  $("mode-banner").classList.toggle("live", !demo);
  text("mode-tag", demo ? "SYNTHETIC FEED" : "LIVE COLLECTOR");
  text("mode-description", demo ? `${s.robots.length} simulated robots · demonstration data, not physical measurements` : `Robot telemetry · session started ${clock(s.collection.started_at)}`);
  $("demo-controls").hidden = !demo;
  if (demo) { state.paused = s.demo_paused; text("pause-button", state.paused ? "Resume feed" : "Pause feed"); }
  const options = '<option value="">Latest run</option>' + s.runs.map(r => `<option value="${esc(r.run_id)}">${esc(r.run_id)}</option>`).join("");
  if ($("run-select").innerHTML !== options) $("run-select").innerHTML = options;
  $("run-select").value = state.run;
  $("export-toggle").disabled = !s.run_id;
  const online = s.robots.filter(r => r.status === "online").length;
  $("online-count").innerHTML = `${online}<small>/ ${s.robots.length}</small>`;
  const stale = s.robots.filter(r => r.status === "stale").length;
  text("online-caption", `${stale} stale · ${s.robots.length - stale - online} awaiting first signal`);
  text("event-rate", fmt(p.events_per_s, 1)); text("event-age", fmt(p.p95_event_age_ms)); text("retained-count", fmt(p.accepted));
  text("event-age-caption", p.future_timestamps ? "Clock offset · future samples excluded" : "At ingestion · requires aligned clocks");
  text("fleet-size", String(s.robots.length).padStart(2, "0"));
  renderRobots(); renderSelected(); renderEvents(); renderQuality(); renderCanvases();
  $("chart-legend").innerHTML = s.robots.map(r => `<span><i style="background:${colorFor(r.robot_id)}"></i>${esc(robotName(r.robot_id))}</span>`).join("");
  text("last-update", `UPDATED ${clock(s.server_time)} · ${selectedRun?.source_group === "simulation" ? "SIMULATION CLOCK" : "WALL CLOCK"}`);
}
function renderRobots() {
  const list = $("robot-list");
  const ids = state.snapshot.robots.map(r => r.robot_id).join("|");
  if (list.dataset.ids !== ids) {
    list.dataset.ids = ids;
    list.replaceChildren(...state.snapshot.robots.map(r => {
      const button = document.createElement("button"); button.className = "robot-card"; button.dataset.robot = r.robot_id;
      button.addEventListener("click", () => { state.selected = r.robot_id; render(); }); return button;
    }));
  }
  for (const r of state.snapshot.robots) {
    const button = [...list.children].find(el => el.dataset.robot === r.robot_id);
    const p = r.payload, color = colorFor(r.robot_id);
    button.classList.toggle("selected", r.robot_id === state.selected); button.setAttribute("aria-pressed", String(r.robot_id === state.selected));
    button.innerHTML = `<div class="robot-top"><span class="robot-identity"><span class="robot-number" style="color:${color}">${esc(r.robot_id.slice(-2))}</span>${esc(robotName(r.robot_id))}</span><span class="robot-state ${esc(r.status)}"><i class="tiny-dot"></i>${esc(r.status)}</span></div><div class="robot-bottom"><span class="battery-track"><span class="battery-fill" style="display:block;width:${p.battery_pct ?? 0}%;background:${color}"></span></span><span>${fmt(p.battery_pct)}%</span><span>·</span><span>${fmt(p.linear_m_s, 2)} m/s</span><span class="right">${r.clock_skew ? "clock skew" : r.age_s == null ? "no data" : `${fmt(r.age_s, 1)}s ago`}</span></div>`;
  }
}
function renderSelected() {
  const r = state.snapshot.robots.find(r => r.robot_id === state.selected); if (!r) return;
  const p = r.payload, scan = p.scan;
  text("selected-name", robotName(r.robot_id));
  text("scan-description", scan ? `${scan.ranges_m.length} retained rays / ${scan.original_count} original · ${r.status === "stale" ? "last received sample" : "sensor frame"}` : "No recent LiDAR measurement available");
  text("scan-badge", scan ? r.status === "stale" ? "STALE SCAN" : `${fmt(scan.range_max_m, 1)}m RANGE` : "NO SCAN");
  $("nearest-value").innerHTML = `${fmt(r.nearest_obstacle_m, 2)}<small> m</small>`;
  $("speed-value").innerHTML = `${fmt(p.linear_m_s, 2)}<small> m/s</small>`;
  $("outbox-value").innerHTML = `${fmt(p.outbox_pending)}<small> queued</small>`;
  text("topic-ages", "At sample: " + ["odom", "scan", "battery"].map(topic => `${topic} ${p.topic_age_s?.[topic] == null ? "unavailable" : `${fmt(p.topic_age_s[topic], 1)}s`}`).join("   ·   "));
}
function renderEvents() {
  const events = state.snapshot.recent_events;
  $("event-rows").innerHTML = events.length ? events.map(e => `<tr><td>${clock(e.received_at)}</td><td style="color:${colorFor(e.robot_id)}">${esc(e.robot_id)}</td><td><span class="event-kind ${e.kind.startsWith("fault") || e.kind === "recovered" ? "fault" : e.kind !== "telemetry" ? "protocol" : ""}">${esc(e.kind)}</span></td><td>#${e.sequence}</td><td>${esc(e.source)}</td></tr>`).join("") : '<tr><td colspan="5" class="empty-cell">No events yet. Start a robot agent to populate this view.</td></tr>';
}
function renderQuality() {
  const p = state.snapshot.pipeline, total = p.accepted + p.duplicate + p.rejected;
  for (const type of ["accepted", "duplicate", "rejected"]) {
    text(`quality-${type}-count`, fmt(p[type])); $(`quality-${type}`).style.width = `${total ? p[type] / total * 100 : 0}%`;
  }
  text("gap-count", fmt(p.sequence_gaps));
  $("quarantine-list").innerHTML = state.snapshot.quarantine.map(q => `<p>${clock(q.received_at)} · ${esc(q.reason)}</p>`).join("") || "No rejected receipts.";
}
async function comparison() {
  if (state.stopped) return;
  const collectionId = state.snapshot?.collection.id;
  try {
    const response = await fetch("/api/compare", {cache:"no-store"}); if (!response.ok || state.stopped) return;
    const runs = await response.json();
    if (state.stopped || collectionId !== state.snapshot?.collection.id) return;
    $("comparison-rows").innerHTML = runs.length ? runs.map(r => `<tr><td><span class="run-name" title="${esc(r.run_id)}">${esc(r.run_id)}</span><span class="run-scenario">${esc(r.scenario)}${r.seed == null ? "" : ` · seed ${r.seed}`} ${r.source_group === "simulation" ? '<span class="simulation-label"> / synthetic</span>' : ""}</span></td><td>${esc(r.protocol)}</td><td>${r.expected_deliveries ? `${fmt(r.delivery_pct, 1)}% <span class="run-scenario">${r.delivered} / ${r.expected_deliveries} recipients</span>` : "—"}</td><td>${r.delivery_mean_ms == null ? "—" : `${fmt(r.delivery_mean_ms, 1)} ms`}</td><td>${r.injected_faults ? `${r.detected_faults} / ${r.injected_faults}` : "—"}</td><td>${r.detection_mean_ms == null ? "—" : `${fmt(r.detection_mean_ms, 1)} ms`}</td><td>${r.recovery_mean_ms == null ? "—" : `${fmt(r.recovery_mean_ms, 1)} ms`}</td></tr>`).join("") : '<tr><td colspan="7" class="empty-cell">Protocol metrics appear when a protocol adapter emits events.</td></tr>';
  } catch { /* The WebSocket status is the connectivity indicator. */ }
}
function canvas(id) {
  const el = $(id), rect = el.getBoundingClientRect(), ratio = Math.min(2, window.devicePixelRatio || 1);
  if (!rect.width || !rect.height) return null;
  el.width = Math.round(rect.width * ratio); el.height = Math.round(rect.height * ratio);
  const ctx = el.getContext("2d"); ctx.scale(ratio, ratio); ctx.font = '10px "Segoe UI", sans-serif';
  return {ctx, w: rect.width, h: rect.height};
}
function emptyCanvas(c, message) { c.ctx.fillStyle = "#829aa9"; c.ctx.textAlign = "center"; c.ctx.fillText(message, c.w / 2, c.h / 2); }
function drawMap() {
  const c = canvas("map-canvas"); if (!c) return;
  const {ctx, w, h} = c, s = state.snapshot, selected = s.robots.find(r => r.robot_id === state.selected), pose = selected?.payload.pose;
  const robots = pose?.map_id ? s.robots.filter(r => r.payload.pose?.map_id === pose.map_id) : selected?.payload.pose ? [selected] : [];
  text("map-description", pose?.map_id ? `Shared frame · ${pose.map_id}` : pose ? `Local odometry · ${selected.robot_id}` : "Waiting for odometry");
  const offset = headingOffset(selected), headingToggle = $("heading-toggle");
  headingToggle.hidden = !(s.mode === "live" && selected?.source === "ros2");
  headingToggle.disabled = state.stopped || !pose;
  headingToggle.setAttribute("aria-pressed", String(Boolean(offset)));
  headingToggle.title = `${selected ? robotName(selected.robot_id) : "Selected robot"}: display offset ${offset ? "180" : "0"}°. Click to flip the arrow.`;
  text("frame-note", pose?.map_id ? "Overlaid only because these poses declare a shared map." : "Independent robot origins · only the selected robot is plotted.");
  text("pose-count", `${robots.length} ${robots.length === 1 ? "pose" : "poses"}`);
  if (!robots.length) { emptyCanvas(c, "No recent pose in the selected robot's frame"); return; }
  const historyFor = (r) => r.history.filter(a => a.pose && a.pose.map_id === pose.map_id && (pose.map_id || a.pose.frame_id === pose.frame_id));
  const points = robots.flatMap(r => [r.payload.pose, ...historyFor(r).map(p => p.pose)]);
  const xs = points.map(p => p.x_m), ys = points.map(p => p.y_m), midX = (Math.min(...xs) + Math.max(...xs)) / 2, midY = (Math.min(...ys) + Math.max(...ys)) / 2;
  const scale = Math.min((w - 110) / Math.max(10, Math.max(...xs) - Math.min(...xs) + 3), (h - 75) / Math.max(7, Math.max(...ys) - Math.min(...ys) + 3));
  const xy = p => [w / 2 + (p.x_m - midX) * scale, (h - 20) / 2 - (p.y_m - midY) * scale];
  const step = Math.max(1, Math.ceil(28 / scale));
  ctx.strokeStyle = "#21333d"; ctx.lineWidth = .6; ctx.fillStyle = "#566e7b"; ctx.textAlign = "center"; ctx.font = '8px "Segoe UI", sans-serif';
  for (let x = Math.floor(midX - w / scale / 2); x < midX + w / scale / 2; x += step) { const [px] = xy({x_m:x,y_m:0}); ctx.beginPath(); ctx.moveTo(px,0); ctx.lineTo(px,h); ctx.stroke(); if (px > 30 && px < w - 30) ctx.fillText(String(x),px,13); }
  for (let y = Math.floor(midY - h / scale / 2); y < midY + h / scale / 2; y += step) { const [,py] = xy({x_m:0,y_m:y}); ctx.beginPath(); ctx.moveTo(0,py); ctx.lineTo(w,py); ctx.stroke(); }
  for (const r of robots) {
    const color = colorFor(r.robot_id), [x,y] = xy(r.payload.pose), history = historyFor(r);
    ctx.strokeStyle = color; ctx.globalAlpha = .3; ctx.lineWidth = 1.8; ctx.beginPath();
    history.forEach((p, i) => { const [hx,hy] = xy(p.pose); i ? ctx.lineTo(hx,hy) : ctx.moveTo(hx,hy); }); ctx.stroke(); ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(x,y,r.robot_id === state.selected ? 21 : 16,0,Math.PI*2); ctx.fillStyle = color + "12"; ctx.fill();
    ctx.strokeStyle = color + (r.robot_id === state.selected ? "80" : "30"); ctx.lineWidth = 1; ctx.setLineDash(r.status === "online" ? [] : [3,3]); ctx.stroke(); ctx.setLineDash([]);
    ctx.save(); ctx.translate(x,y); ctx.rotate(-(r.payload.pose.yaw_rad + headingOffset(r))); ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(10,0); ctx.lineTo(-6,-6); ctx.lineTo(-3,0); ctx.lineTo(-6,6); ctx.closePath(); ctx.fill(); ctx.restore();
    ctx.fillStyle = color; ctx.font = '10px "Segoe UI", sans-serif'; ctx.textAlign = "center"; ctx.fillText(robotName(r.robot_id),x,y-29);
  }
}
function drawLidar() {
  const c = canvas("lidar-canvas"); if (!c) return;
  const r = state.snapshot.robots.find(r => r.robot_id === state.selected), scan = r?.payload.scan;
  if (!scan) { emptyCanvas(c, "No fresh scan"); return; }
  const {ctx,w,h} = c, cx = w / 2, cy = h / 2 + 2, radius = Math.min(w / 2 - 25,h / 2 - 26), color = colorFor(r.robot_id);
  ctx.strokeStyle = "#2a3c46"; ctx.lineWidth = .7; ctx.textAlign = "left"; ctx.font = '8px "Segoe UI", sans-serif';
  for (let i=1;i<=4;i++) { ctx.beginPath(); ctx.arc(cx,cy,radius*i/4,0,Math.PI*2); ctx.stroke(); ctx.fillStyle="#607f90"; ctx.fillText(`${fmt(scan.range_max_m*i/4,1)}m`,cx+4,cy-radius*i/4+10); }
  ctx.beginPath(); ctx.moveTo(cx-radius,cy); ctx.lineTo(cx+radius,cy); ctx.moveTo(cx,cy-radius); ctx.lineTo(cx,cy+radius); ctx.stroke();
  scan.ranges_m.forEach((distance,index) => { if (distance == null) return; const angle = scan.angle_min_rad + index*scan.angle_increment_rad, radial = distance/scan.range_max_m*radius, x=cx-Math.sin(angle)*radial, y=cy-Math.cos(angle)*radial; ctx.fillStyle=distance < .5 ? "#efbd72" : color; ctx.beginPath(); ctx.arc(x,y,2,0,Math.PI*2); ctx.fill(); });
  ctx.fillStyle=color; ctx.beginPath(); ctx.moveTo(cx,cy-7); ctx.lineTo(cx-4,cy+4); ctx.lineTo(cx+4,cy+4); ctx.closePath(); ctx.fill(); ctx.fillStyle="#91a9b6"; ctx.textAlign="center"; ctx.fillText("FORWARD",cx,15);
}
function drawSpeed() {
  const c = canvas("speed-canvas"); if (!c) return;
  const {ctx,w,h} = c, robots = state.snapshot.robots, points = robots.flatMap(r => r.history.filter(p => p.speed != null));
  if (!points.length) { emptyCanvas(c,"Speed history appears after odometry arrives"); return; }
  const end = Math.max(...points.map(p=>p.t)), start = Math.min(...points.map(p=>p.t)), duration = Math.max(1,end-start), max = Math.max(.2,...points.map(p=>Math.abs(p.speed)))*1.2, negative=points.some(p=>p.speed<0), min=negative ? -max : 0;
  const left=42,right=w-18,top=28,bottom=h-33, px=t=>left+(t-start)/duration*(right-left),py=v=>bottom-(v-min)/(max-min)*(bottom-top);
  ctx.font='9px "Segoe UI", sans-serif'; ctx.lineWidth=.6; ctx.strokeStyle="#2b3b46"; ctx.fillStyle="#7e98a7";
  for(let i=0;i<=4;i++){const value=min+(max-min)*i/4,y=py(value);ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.textAlign="right";ctx.fillText(value.toFixed(2),left-9,y+3);}
  ctx.textAlign="left";ctx.fillText(clock(start),left,h-12);ctx.textAlign="right";ctx.fillText(clock(end),right,h-12);
  for(const r of robots){ctx.strokeStyle=colorFor(r.robot_id);ctx.lineWidth=r.robot_id===state.selected?2:1.3;ctx.globalAlpha=r.robot_id===state.selected?1:.65;ctx.beginPath();let previous=null;for(const p of r.history){if(p.speed==null){previous=null;continue;} if(!previous||p.t-previous.t>3)ctx.moveTo(px(p.t),py(p.speed));else ctx.lineTo(px(p.t),py(p.speed));previous=p;}ctx.stroke();ctx.globalAlpha=1;}
}
function renderCanvases() { if(state.snapshot) { drawMap(); drawLidar(); drawSpeed(); } }
$("shutdown-button").addEventListener("click", async () => {
  if (state.stopped || !state.controlToken) return;
  $("shutdown-button").disabled = true; text("shutdown-button", "Shutting down…");
  try {
    const response = await fetch("/api/shutdown", {method:"POST", headers:{"X-SwarmLink-Control":state.controlToken}, signal:AbortSignal.timeout(8000)});
    if (!response.ok) { const error = await response.json(); throw new Error(error.detail || "Shutdown failed"); }
    showStopped();
  } catch (error) {
    if (!state.stopped) { text("shutdown-button", "Shut down dashboard"); $("shutdown-button").disabled = false; toast(error.message); }
  }
});
$("heading-toggle").addEventListener("click", () => {
  const robot = state.snapshot?.robots.find(r => r.robot_id === state.selected);
  if (state.stopped || state.snapshot?.mode !== "live" || robot?.source !== "ros2" || !robot.payload.pose) return;
  headingOffsets.set(robot.robot_id, headingOffset(robot) ? 0 : Math.PI);
  drawMap();
});
$("run-select").addEventListener("change", (event) => { state.run=event.target.value; connect(); });
$("export-toggle").addEventListener("click", () => { const open=$("export-menu").hidden; $("export-menu").hidden=!open; $("export-toggle").setAttribute("aria-expanded",String(open)); });
document.addEventListener("click",event=>{if(!event.target.closest(".export-wrap")){ $("export-menu").hidden=true; $("export-toggle").setAttribute("aria-expanded","false"); }});
document.addEventListener("keydown",event=>{if(event.key==="Escape"){$("export-menu").hidden=true;$("export-toggle").setAttribute("aria-expanded","false");}});
$("export-menu").addEventListener("click", async(event)=>{
  const format=event.target.dataset.format;if(!format||!state.snapshot?.run_id)return;$("export-menu").hidden=true;$("export-toggle").setAttribute("aria-expanded","false");
  try{const response=await fetch(`/api/runs/${encodeURIComponent(state.snapshot.run_id)}/export?format=${format}`);if(!response.ok){const error=await response.json();throw new Error(error.detail||"Export failed");}const url=URL.createObjectURL(await response.blob()),a=document.createElement("a");a.href=url;a.download=`swarmlink-${state.snapshot.run_id}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(`Downloaded ${format.toUpperCase()} for the selected experiment.`);}catch(error){toast(error.message);}
});
async function demoAction(action){try{const response=await fetch(`/api/demo/${action}`,{method:"POST"});if(!response.ok)throw new Error("Demo control unavailable");const result=await response.json();state.paused=result.paused;text("pause-button",state.paused?"Resume feed":"Pause feed");if(action==="interrupt")toast("Synthetic dropout started for the last robot. It becomes stale after 6 seconds and resumes after 12 seconds.");}catch(error){toast(error.message);}}
$("pause-button").addEventListener("click",()=>demoAction("pause"));$("interrupt-button").addEventListener("click",()=>demoAction("interrupt"));
new ResizeObserver(renderCanvases).observe($("overview"));
setInterval(()=>{if(!state.stopped&&state.lastMessage&&Date.now()-state.lastMessage>4000){connection(false);text("last-update",`NO UPDATE FOR ${Math.floor((Date.now()-state.lastMessage)/1000)}s · DISPLAYING LAST SNAPSHOT`);}},1000);
connect(); comparison(); state.comparisonTimer = setInterval(comparison,5000);
