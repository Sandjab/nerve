// nerve/web/graph.js — visualisation nerve (rendu force-graph + analytics graphology)
// Globals fournis par index.html : `graphology` (constructeur Graph, UMD), `ForceGraph` (UMD),
// `window.graphologyLouvain` (package standalone chargé en ESM ; module asynchrone, d'où la garde).

const THEMES = {
  light: {bg:"#F4F6FA", node:"#23537F", value:"#7A889B", bridge:"#7C2A38",
          link:"rgba(35,83,127,0.30)", text:"#15202E",
          comm:["#23537F","#1C6A4C","#7C2A38","#2C77B6","#9B3443","#43536A","#B07A1E"]},
  dark:  {bg:"#0F1A26", node:"#2C77B6", value:"#7A889B", bridge:"#C2566A",
          link:"rgba(111,168,218,0.30)", text:"#cfe0f0",
          comm:["#6FA8DA","#3FA77E","#C2566A","#2C77B6","#D98AA0","#9FB3C8","#D6A84E"]},
};
let theme = localStorage.getItem("nerve-theme") || "light";
document.documentElement.setAttribute("data-theme", theme);
const T = () => THEMES[theme];

let colorMode = "community", sizeMode = "centrality", showEdgeLabels = false;
let pathKeys = new Set();   // arêtes "srctgt" surlignées (chemin le plus long)

// force-graph rend les labels en HTML : on échappe le texte LLM (XSS stocké).
function escapeHtml(str){
  return str ? String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;") : "";
}
const linkKey = (l) => (l.source.id||l.source) + "\u0001" + (l.target.id||l.target);

// ---- couleur / taille / arêtes ----
function nodeColor(n){
  if(pathKeys.size && n._inPath) return "#D6A84E";
  if(colorMode === "uniform") return T().node;
  if(colorMode === "type") return n.kind === "value" ? T().value : T().node;
  if(colorMode === "set"){
    const s = n.sets || [];
    if(s.length > 1) return T().bridge;          // hub multi-sets
    return s.length ? T().comm[s[0] % T().comm.length] : T().node;
  }
  return T().comm[(n.community || 0) % T().comm.length];   // community
}
function nodeVal(n){
  if(sizeMode === "fixed") return 1;
  if(sizeMode === "mentions") return 1 + (n.mentions || 0);
  return 1 + (n.centrality || 0);              // centralité (degré)
}
function linkColor(l){
  if(pathKeys.has(linkKey(l))) return "#D6A84E";
  return l.is_bridge ? T().bridge : T().link;
}
function linkWidth(l){
  if(pathKeys.has(linkKey(l))) return 4;
  const c = (l.confidence == null ? 70 : l.confidence) / 100;
  return l.is_bridge ? 3 : 0.5 + 2 * c;        // confiance -> épaisseur
}
function drawEdgeLabel(link, ctx, scale){
  if(!showEdgeLabels || !link.predicate) return;
  const s = link.source, t = link.target;
  if(typeof s !== "object" || typeof t !== "object") return;
  const x = (s.x + t.x) / 2, y = (s.y + t.y) / 2;
  const f = 10 / scale;
  ctx.font = `${f}px -apple-system,system-ui,sans-serif`;
  ctx.fillStyle = T().text;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(link.predicate, x, y);
}

const G = ForceGraph()(document.getElementById("graph"))
  .nodeLabel(n => escapeHtml(n.label || n.id))
  .linkDirectionalArrowLength(3.5).linkDirectionalArrowRelPos(0.92);

function applyStyles(){
  G.backgroundColor(T().bg)
   .nodeColor(nodeColor).nodeVal(nodeVal)
   .linkColor(linkColor).linkWidth(linkWidth)
   .linkCanvasObjectMode(() => showEdgeLabels ? "after" : undefined)
   .linkCanvasObject(drawEdgeLabel);
  renderLegend();
}

// ---- analytics graphology (communautés louvain + centralité de degré) ----
function analyze(data){
  try{
    const g = new graphology.Graph({type:"undirected", allowSelfLoops:true});
    data.nodes.forEach(n => { if(!g.hasNode(n.id)) g.addNode(n.id); });
    data.links.forEach(l => {
      const a = l.source.id || l.source, b = l.target.id || l.target;
      if(a !== b && !g.hasEdge(a, b)) g.addEdge(a, b);
    });
    if(window.graphologyLouvain) window.graphologyLouvain.assign(g);
    data.nodes.forEach(n => {
      n.community = g.getNodeAttribute(n.id, "community") || 0;
      n.centrality = g.degree(n.id);
    });
  }catch(e){ console.warn("graphology indisponible:", e); }
}

// ---- chemin le plus long (heuristique bornée : DFS depuis les hauts degrés) ----
function longestPath(data){
  const adj = new Map(); data.nodes.forEach(n => adj.set(n.id, []));
  data.links.forEach(l => {
    const a = l.source.id || l.source, b = l.target.id || l.target;
    if(adj.has(a) && adj.has(b)){ adj.get(a).push(b); adj.get(b).push(a); }
  });
  const order = [...adj.keys()].sort((x, y) => adj.get(y).length - adj.get(x).length);
  let best = [];
  const CAP = 6;                                  // fanout plafonné (anti-explosion)
  function dfs(node, seen, path){
    if(path.length > best.length) best = path.slice();
    let n = 0;
    for(const nb of adj.get(node)){
      if(seen.has(nb)) continue;
      if(++n > CAP) break;
      seen.add(nb); path.push(nb); dfs(nb, seen, path);
      path.pop(); seen.delete(nb);
    }
  }
  for(const start of order.slice(0, 8)){
    dfs(start, new Set([start]), [start]);
    if(best.length >= adj.size) break;
  }
  const keys = new Set();
  for(let i = 0; i + 1 < best.length; i++)
    keys.add(best[i] + "\u0001" + best[i+1]).add(best[i+1] + "\u0001" + best[i]);
  data.nodes.forEach(n => { n._inPath = best.includes(n.id); });
  return keys;
}

// ---- rendu d'un graphe {nodes, links} (set / transverse) ----
let nodes = new Map(), links = [], linkKeys = new Set();
function renderGraph(data){
  linkKeys = new Set();
  const d = {nodes: data.nodes || [], links: data.links || []};
  analyze(d);
  pathKeys = document.getElementById("pathBtn").classList.contains("on")
    ? longestPath(d) : new Set();
  G.graphData(d);
  applyStyles();
}

// ---- légende dynamique selon le mode de couleur ----
function legendRow(color, label, bar){
  const row = document.createElement("div"); row.className = "row";
  const mark = document.createElement("span");
  mark.className = bar ? "bar" : "dot"; mark.style.background = color;
  row.appendChild(mark); row.appendChild(document.createTextNode(" " + label));
  return row;
}
function renderLegend(){
  const box = document.getElementById("legend"); box.replaceChildren();
  const title = document.createElement("div"); title.className = "lt";
  const data = G.graphData();
  if(colorMode === "type"){
    title.textContent = "Type"; box.appendChild(title);
    box.appendChild(legendRow(T().node, "entité"));
    box.appendChild(legendRow(T().value, "valeur"));
  }else if(colorMode === "set"){
    title.textContent = "Sets"; box.appendChild(title);
    const sets = [...new Set(data.nodes.flatMap(n => n.sets || []))].sort((a,b)=>a-b);
    sets.forEach(s => box.appendChild(legendRow(T().comm[s % T().comm.length], "set " + s)));
    box.appendChild(legendRow(T().bridge, "hub multi-sets", true));
  }else if(colorMode === "community"){
    title.textContent = "Communautés"; box.appendChild(title);
    const comms = [...new Set(data.nodes.map(n => n.community || 0))].sort((a,b)=>a-b);
    comms.forEach(c => box.appendChild(legendRow(T().comm[c % T().comm.length], "communauté " + c)));
  }else{
    title.textContent = "Uniforme"; box.appendChild(title);
    box.appendChild(legendRow(T().node, "nœud"));
  }
  box.appendChild(legendRow(T().bridge, "passerelle inter-sources", true));
}

// ---- carte de fait au survol d'une arête ----
const card = document.getElementById("factcard");
G.onLinkHover(link => {
  if(!link){ card.style.display = "none"; return; }
  card.replaceChildren();
  const triple = document.createElement("div"); triple.className = "triple";
  const sb = document.createElement("b"); sb.textContent = (link.source.label || link.source.id || "");
  const pr = document.createElement("span"); pr.className = "pred"; pr.textContent = " " + (link.predicate || "") + " ";
  const ob = document.createElement("b"); ob.textContent = (link.target.label || link.target.id || "");
  triple.append(sb, pr, ob);
  const meta = document.createElement("div"); meta.className = "meta";
  const c = document.createElement("span");
  c.textContent = "conf " + (link.confidence == null ? "–" : link.confidence + "%");
  meta.appendChild(c);
  card.append(triple, meta); card.style.display = "block";
});
document.getElementById("graph").addEventListener("mousemove", e => {
  if(card.style.display === "block"){
    card.style.left = (e.offsetX + 14) + "px"; card.style.top = (e.offsetY + 14) + "px";
  }
});

// ---- flux live (extraction SSE) : rendu incrémental, style uniforme pendant le stream ----
function addFact(f){
  const s = f.subject_canonical || f.subject, o = f.object_canonical || f.object;
  if(!s || !o) return;
  nodes.set(s, {id:s}); nodes.set(o, {id:o});
  const k = s + "\u0001" + (f.predicate || "") + "\u0001" + o;
  if(linkKeys.has(k)) return;
  linkKeys.add(k);
  links.push({source:s, target:o, predicate:f.predicate});
}
function redraw(){ G.graphData({nodes:[...nodes.values()], links}); applyStyles(); }

document.getElementById("go").addEventListener("click", async () => {
  const text = document.getElementById("txt").value.trim(); if(!text) return;
  nodes = new Map(); links = []; linkKeys = new Set(); pathKeys = new Set(); redraw();
  const r = await fetch("/api/documents", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({title:"Coller", text})});
  const {document_id} = await r.json();
  const es = new EventSource(`/api/documents/${document_id}/events`);
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if(m.type === "replay"){ m.facts.forEach(addFact); redraw(); }
    else if(m.type === "fact" && !m.is_duplicate){ addFact(m.fact); redraw(); }
    else if(m.type === "done" || m.type === "error"){ es.close(); }
  };
  es.onerror = () => es.close();
});

// ---- navigation sets / docs / recherche / transverse (I-4) ----
async function loadSets(){
  const sets = await (await fetch("/api/sets")).json();
  const box = document.getElementById("sets"); box.replaceChildren();
  sets.forEach(s => {
    const el = document.createElement("div"); el.className = "item";
    el.textContent = `${s.name} (${s.document_count})`;
    el.onclick = () => openSet(s.id, el);
    box.appendChild(el);
  });
}
async function openSet(id, el){
  renderGraph(await (await fetch(`/api/sets/${id}/graph`)).json());
  const detail = await (await fetch(`/api/sets/${id}`)).json();
  const prev = document.querySelector("#setDocs"); if(prev) prev.remove();
  const sub = document.createElement("div"); sub.id = "setDocs";
  detail.documents.forEach(d => {
    const elDoc = document.createElement("div"); elDoc.className = "item"; elDoc.style.paddingLeft = "16px";
    elDoc.textContent = `· ${d.title}`;
    elDoc.onclick = (e) => { e.stopPropagation(); openDocument(d.id); };
    sub.appendChild(elDoc);
  });
  el.insertAdjacentElement("afterend", sub);
}
async function openDocument(id){
  const {facts} = await (await fetch(`/api/documents/${id}/facts`)).json();
  nodes = new Map(); links = []; linkKeys = new Set(); pathKeys = new Set();
  (facts || []).forEach(addFact); redraw();
}
document.getElementById("searchBtn").addEventListener("click", async () => {
  const q = document.getElementById("q").value.trim(); if(!q) return;
  const res = await (await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json();
  const box = document.getElementById("results"); box.replaceChildren();
  (res.results || []).forEach(r => {
    const el = document.createElement("div"); el.className = "r";
    el.textContent = `${r.subject} · ${r.predicate} · ${r.object}`;
    box.appendChild(el);
  });
});
document.getElementById("transBtn").addEventListener("click", async () => {
  const ent = document.getElementById("ent").value.trim(); if(!ent) return;
  renderGraph(await (await fetch(`/api/transverse?entity=${encodeURIComponent(ent)}`)).json());
});

// ---- contrôles ----
document.getElementById("colorMode").addEventListener("change", (e) => {
  colorMode = e.target.value; applyStyles();
});
document.getElementById("sizeMode").addEventListener("change", (e) => {
  sizeMode = e.target.value; applyStyles();
});
document.getElementById("edgeLabelsBtn").addEventListener("click", (e) => {
  showEdgeLabels = !showEdgeLabels; e.target.classList.toggle("on", showEdgeLabels); applyStyles();
});
document.getElementById("pathBtn").addEventListener("click", (e) => {
  const on = !e.target.classList.contains("on"); e.target.classList.toggle("on", on);
  pathKeys = on ? longestPath(G.graphData()) : new Set();
  if(!on) G.graphData().nodes.forEach(n => { n._inPath = false; });
  applyStyles();
});
document.getElementById("themeBtn").addEventListener("click", (e) => {
  theme = theme === "light" ? "dark" : "light";
  localStorage.setItem("nerve-theme", theme);
  document.documentElement.setAttribute("data-theme", theme);
  e.target.textContent = theme === "light" ? "☾" : "☀";
  applyStyles();
});

document.getElementById("themeBtn").textContent = theme === "light" ? "☾" : "☀";
applyStyles();
loadSets();
