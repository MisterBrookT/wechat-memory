const $ = (selector) => document.querySelector(selector);
const canvas = $("#graph");
const context = canvas.getContext("2d");

const ROLE_LABELS = {
  private_chat_peer: "私聊对象",
  group_member_seen: "群内见过",
  official_or_service: "服务号",
  self: "本人",
  unknown: "未分类",
};

const state = {
  data: { nodes: [], edges: [], meta: {} },
  nodeMap: new Map(),
  days: 90,
  people: 80,
  groups: 12,
  scale: 1,
  offsetX: 0,
  offsetY: 0,
  selected: null,
  hovered: null,
  draggingNode: null,
  panning: false,
  pointer: null,
  simulationTicks: 0,
  graphRequest: 0,
  frameRequest: 0,
};

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function compact(value) {
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);
}

function formatDate(timestamp) {
  if (!timestamp) return "没有互动时间";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(new Date(timestamp * 1000));
}

function hash(text) {
  let value = 2166136261;
  for (const char of text) value = Math.imul(value ^ char.charCodeAt(0), 16777619);
  return Math.abs(value >>> 0);
}

function nodeRadius(node) {
  if (node.kind === "self") return 16;
  const base = node.kind === "group" ? 8 : 5;
  return Math.min(base + Math.log2((node.message_count || 0) + 1) * 1.05, node.kind === "group" ? 17 : 14);
}

function initializePositions() {
  const groups = state.data.nodes.filter((node) => node.kind === "group");
  const people = state.data.nodes.filter((node) => node.kind === "person");
  state.data.nodes.forEach((node) => {
    const seed = hash(node.id);
    if (node.kind === "self") {
      node.x = 0;
      node.y = 0;
      node.fixed = true;
    } else if (node.kind === "group") {
      const index = groups.indexOf(node);
      const angle = (index / Math.max(groups.length, 1)) * Math.PI * 2 - Math.PI / 2;
      node.x = Math.cos(angle) * 190;
      node.y = Math.sin(angle) * 190;
    } else {
      const index = people.indexOf(node);
      const angle = ((index * 2.399963 + (seed % 100) / 800) % (Math.PI * 2));
      const ring = 260 + (index % 4) * 54;
      node.x = Math.cos(angle) * ring;
      node.y = Math.sin(angle) * ring;
    }
    node.vx = 0;
    node.vy = 0;
  });
  state.simulationTicks = 0;
}

function simulationStep() {
  const nodes = state.data.nodes;
  const alpha = Math.max(.05, 1 - state.simulationTicks / 190);
  for (let left = 0; left < nodes.length; left += 1) {
    const a = nodes[left];
    for (let right = left + 1; right < nodes.length; right += 1) {
      const b = nodes[right];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      const distanceSquared = Math.max(dx * dx + dy * dy, 36);
      const distance = Math.sqrt(distanceSquared);
      const force = (a.kind === "group" || b.kind === "group" ? 650 : 380) / distanceSquared * alpha;
      dx /= distance;
      dy /= distance;
      if (!a.fixed) { a.vx -= dx * force; a.vy -= dy * force; }
      if (!b.fixed) { b.vx += dx * force; b.vy += dy * force; }
    }
  }
  state.data.edges.forEach((edge) => {
    const source = state.nodeMap.get(edge.source);
    const target = state.nodeMap.get(edge.target);
    if (!source || !target) return;
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(Math.hypot(dx, dy), 1);
    const ideal = edge.kind === "group_message" ? 92 : edge.kind === "private" ? 210 : 235;
    const strength = edge.kind === "group_message" ? .014 : .008;
    const force = (distance - ideal) * strength * alpha;
    if (!source.fixed) { source.vx += dx / distance * force; source.vy += dy / distance * force; }
    if (!target.fixed) { target.vx -= dx / distance * force; target.vy -= dy / distance * force; }
  });
  nodes.forEach((node) => {
    if (node.fixed || node === state.draggingNode) return;
    node.vx += -node.x * .0008 * alpha;
    node.vy += -node.y * .0008 * alpha;
    node.vx *= .82;
    node.vy *= .82;
    node.x += node.vx;
    node.y += node.vy;
  });
  state.simulationTicks += 1;
}

function resize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

function screenPoint(node) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: rect.width / 2 + state.offsetX + node.x * state.scale,
    y: rect.height / 2 + state.offsetY + node.y * state.scale,
  };
}

function worldPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left - rect.width / 2 - state.offsetX) / state.scale,
    y: (event.clientY - rect.top - rect.height / 2 - state.offsetY) / state.scale,
  };
}

function connectedIds(nodeId) {
  const ids = new Set([nodeId]);
  state.data.edges.forEach((edge) => {
    if (edge.source === nodeId) ids.add(edge.target);
    if (edge.target === nodeId) ids.add(edge.source);
  });
  return ids;
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  context.clearRect(0, 0, rect.width, rect.height);
  const focus = state.selected ? connectedIds(state.selected.id) : null;

  state.data.edges.forEach((edge) => {
    const source = state.nodeMap.get(edge.source);
    const target = state.nodeMap.get(edge.target);
    if (!source || !target) return;
    const a = screenPoint(source);
    const b = screenPoint(target);
    const active = !focus || (focus.has(edge.source) && focus.has(edge.target));
    context.beginPath();
    context.moveTo(a.x, a.y);
    context.lineTo(b.x, b.y);
    context.globalAlpha = active ? (edge.kind === "group_message" ? .38 : .28) : .045;
    context.strokeStyle = edge.kind === "private" ? "#111416" : "#88aeb4";
    context.lineWidth = Math.min(.5 + Math.log2((edge.weight || 0) + 1) * .18, 2.2);
    if (edge.kind !== "private") context.setLineDash([3, 4]);
    context.stroke();
    context.setLineDash([]);
  });
  context.globalAlpha = 1;

  const labelThreshold = [...state.data.nodes]
    .filter((node) => node.kind === "person")
    .map((node) => node.message_count || 0)
    .sort((a, b) => b - a)[Math.min(22, state.data.nodes.length - 1)] || 0;
  state.data.nodes.forEach((node) => {
    const point = screenPoint(node);
    const radius = nodeRadius(node) * Math.max(.75, Math.min(state.scale, 1.35));
    const active = !focus || focus.has(node.id);
    context.globalAlpha = active ? 1 : .16;
    context.beginPath();
    if (node.kind === "group") {
      context.moveTo(point.x, point.y - radius);
      context.lineTo(point.x + radius, point.y);
      context.lineTo(point.x, point.y + radius);
      context.lineTo(point.x - radius, point.y);
      context.closePath();
    } else {
      context.arc(point.x, point.y, radius, 0, Math.PI * 2);
    }
    context.fillStyle = node.kind === "self" ? "#4f78a8" : node.kind === "group" ? "#88aeb4" : "#111416";
    context.fill();
    if (node === state.selected || node === state.hovered) {
      context.lineWidth = 1;
      context.strokeStyle = "#4f78a8";
      context.stroke();
      context.beginPath();
      context.arc(point.x, point.y, radius + 6, 0, Math.PI * 2);
      context.globalAlpha = .45;
      context.stroke();
    }
    const showLabel = node.kind !== "person" || node.message_count >= labelThreshold || node === state.selected || node === state.hovered || state.scale > 1.55;
    if (showLabel) {
      context.globalAlpha = active ? .92 : .13;
      context.font = node.kind === "self" ? "600 12px ui-sans-serif" : "10px ui-sans-serif";
      context.textAlign = "center";
      context.textBaseline = "top";
      context.fillStyle = "#111416";
      const label = node.label.length > 13 ? `${node.label.slice(0, 12)}…` : node.label;
      context.fillText(label, point.x, point.y + radius + 6);
    }
  });
  context.globalAlpha = 1;
}

function animate() {
  if (state.simulationTicks < 190) {
    simulationStep();
    simulationStep();
  }
  draw();
  state.frameRequest = requestAnimationFrame(animate);
}

function nodeAt(event) {
  const point = worldPoint(event);
  let best = null;
  let distance = Infinity;
  state.data.nodes.forEach((node) => {
    const candidate = Math.hypot(node.x - point.x, node.y - point.y);
    const hit = nodeRadius(node) + 7 / state.scale;
    if (candidate < hit && candidate < distance) {
      best = node;
      distance = candidate;
    }
  });
  return best;
}

function renderConnections(node) {
  const rows = state.data.edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .map((edge) => ({
      edge,
      node: state.nodeMap.get(edge.source === node.id ? edge.target : edge.source),
    }))
    .filter((item) => item.node)
    .sort((a, b) => b.edge.weight - a.edge.weight)
    .slice(0, 18);
  const wrapper = element("div", "connection-list");
  rows.forEach((item) => {
    const row = element("div", "connection");
    row.append(element("strong", "", item.node.label));
    row.append(element("span", "", `${compact(item.edge.weight)} 条`));
    wrapper.append(row);
  });
  return wrapper;
}

async function selectNode(node) {
  state.selected = node;
  $("#inspector").classList.add("open");
  const card = element("article", "node-card");
  card.append(element("span", "node-kind", node.kind === "self" ? "CENTER" : node.kind === "group" ? "GROUP CONTEXT" : "PERSON"));
  card.append(element("h2", "", node.label));
  card.append(element("p", "node-meta", node.kind === "group" ? "群内发言构成的局部上下文" : [node.remark, node.nickname].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" · ") || "本地微信身份"));
  const metrics = element("div", "metric-pair");
  const count = element("div", "metric");
  count.append(element("strong", "", compact(node.message_count)));
  count.append(element("span", "", "可见消息"));
  const latest = element("div", "metric");
  latest.append(element("strong", "", node.last_ts ? formatDate(node.last_ts).replace(/\s/g, "") : "—"));
  latest.append(element("span", "", "最近出现"));
  metrics.append(count, latest);
  card.append(metrics);

  const connections = element("section");
  connections.append(element("h3", "", "直接连接"), renderConnections(node));
  card.append(connections);
  $("#inspector").replaceChildren(card);

  if (node.kind !== "person" || !node.person_id) return;
  try {
    const data = await api(`/api/people/${node.person_id}`);
    if (state.selected !== node) return;
    if (data.person.roles?.length) {
      const roleSection = element("section");
      roleSection.append(element("h3", "", "身份分类"));
      const tags = element("div", "tags");
      data.person.roles.forEach((role) => tags.append(element("span", "tag", ROLE_LABELS[role] || role)));
      roleSection.append(tags);
      card.append(roleSection);
    }
    const profileSection = element("section");
    profileSection.append(element("h3", "", "人物画像"));
    profileSection.append(element("p", "summary", data.profile?.summary || "尚未生成人物画像。图中的节点来自互动事实，不会用空画像补全。"));
    if (data.facts?.length) {
      const facts = element("div", "fact-list");
      data.facts.slice(0, 8).forEach((fact) => {
        const item = element("article", "fact");
        item.append(element("small", "", `${fact.category} · 原消息 #${fact.evidence_message_id}`));
        item.append(element("p", "", fact.value));
        facts.append(item);
      });
      profileSection.append(facts);
    }
    card.append(profileSection);
  } catch (error) {
    card.append(element("p", "error", `人物资料读取失败：${error.message}`));
  }
}

async function loadOverview() {
  const data = await api("/api/overview");
  $("#message-total").textContent = compact(data.messages);
  const finished = data.last_ingest?.finished_at;
  $("#freshness").textContent = finished ? `最近整理 ${new Date(finished).toLocaleString("zh-CN")}` : "尚未运行整理";
}

async function loadGraph() {
  const ticket = ++state.graphRequest;
  $("#loading").hidden = false;
  const data = await api(`/api/graph?days=${state.days}&people=${state.people}&groups=${state.groups}`);
  if (ticket !== state.graphRequest) return;
  state.data = data;
  state.nodeMap = new Map(data.nodes.map((node) => [node.id, node]));
  state.selected = null;
  state.hovered = null;
  state.scale = 1;
  state.offsetX = 0;
  state.offsetY = 0;
  initializePositions();
  $("#people-total").textContent = compact(data.meta.people);
  $("#group-total").textContent = compact(data.meta.groups);
  $("#graph-note").textContent = data.meta.meaning;
  $("#period-label").textContent = state.days ? `最近 ${state.days} 天` : "全部本地记录";
  $("#inspector").classList.remove("open");
  $("#inspector").innerHTML = '<div class="inspector-empty"><span class="crosshair" aria-hidden="true"></span><h2>选择一个节点</h2><p>查看人物、群聊，以及这条连线具体代表什么。</p></div>';
  $("#loading").hidden = true;
}

canvas.addEventListener("pointerdown", (event) => {
  canvas.setPointerCapture(event.pointerId);
  const node = nodeAt(event);
  state.pointer = { x: event.clientX, y: event.clientY, moved: false };
  if (node) {
    state.draggingNode = node;
  } else {
    state.panning = true;
  }
});

canvas.addEventListener("pointermove", (event) => {
  if (state.pointer) {
    const dx = event.clientX - state.pointer.x;
    const dy = event.clientY - state.pointer.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) state.pointer.moved = true;
    if (state.draggingNode) {
      const point = worldPoint(event);
      state.draggingNode.x = point.x;
      state.draggingNode.y = point.y;
      state.draggingNode.vx = 0;
      state.draggingNode.vy = 0;
      state.simulationTicks = Math.min(state.simulationTicks, 150);
    } else if (state.panning) {
      state.offsetX += dx;
      state.offsetY += dy;
    }
    state.pointer.x = event.clientX;
    state.pointer.y = event.clientY;
  } else {
    state.hovered = nodeAt(event);
  }
});

canvas.addEventListener("pointerup", (event) => {
  const clicked = state.pointer && !state.pointer.moved ? nodeAt(event) : null;
  state.draggingNode = null;
  state.panning = false;
  state.pointer = null;
  if (clicked) selectNode(clicked);
});

canvas.addEventListener("pointerleave", () => { if (!state.pointer) state.hovered = null; });
canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const before = worldPoint(event);
  state.scale = Math.max(.38, Math.min(3.2, state.scale * Math.exp(-event.deltaY * .001)));
  const after = worldPoint(event);
  state.offsetX += (after.x - before.x) * state.scale;
  state.offsetY += (after.y - before.y) * state.scale;
}, { passive: false });
canvas.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    state.selected = null;
    $("#inspector").classList.remove("open");
  }
});

$("#period-control").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-days]");
  if (!button) return;
  $("#period-control").querySelectorAll("button").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  state.days = Number(button.dataset.days);
  loadGraph().catch(showError);
});

let densityTimer;
$("#people-limit").addEventListener("input", (event) => {
  state.people = Number(event.target.value);
  $("#people-limit-value").textContent = state.people;
  clearTimeout(densityTimer);
  densityTimer = setTimeout(() => loadGraph().catch(showError), 180);
});

$("#show-groups").addEventListener("change", (event) => {
  state.groups = event.target.checked ? 12 : 0;
  loadGraph().catch(showError);
});

function showError(error) {
  $("#loading").hidden = true;
  $("#inspector").replaceChildren(element("p", "error", `本地图谱读取失败：${error.message}`));
  $("#inspector").classList.add("open");
}

new ResizeObserver(resize).observe(canvas);
Promise.all([loadOverview(), loadGraph()]).catch(showError);
animate();
