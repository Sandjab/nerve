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
// police UI « instrument » pour les labels dessinés sur le canvas (miroir de --ui dans theme.css)
const UI_FONT = '"Avenir Next","Avenir","Helvetica Neue",system-ui,sans-serif';

// palette des catégories : « Défaut » (scriptorium) ou « Daltonien-safe » (Okabe-Ito, encodage purement teinte).
// Choix persistant ; cc() renvoie les couleurs de catégorie actives (T() conserve bg/link/text).
const CB = {comm:["#0072B2","#009E73","#E69F00","#CC79A7","#56B4E9","#F0E442"],
            bridge:"#D55E00", node:"#0072B2", value:"#8A8A8A"};
let palette = localStorage.getItem("nerve-palette") || "default";
const cc = () => palette === "cb" ? CB : T();

// indicateur d'extraction : bouton désactivé pendant le flux + confirmation de fin
const goBtn = document.getElementById("go");
const statusBox = document.getElementById("status");
let statusTimer = null, extracting = false;
function setStatus(msg, kind){     // kind : "live" | "done" | null (= cacher)
  if(!msg){ statusBox.style.display = "none"; return; }
  statusBox.textContent = msg; statusBox.className = kind || "";
  statusBox.style.display = "block";
  clearTimeout(statusTimer);
  if(kind === "done") statusTimer = setTimeout(() => { statusBox.style.display = "none"; }, 5000);
}
function setExtracting(on){
  extracting = on;                 // l'état vide ne doit pas réapparaître pendant l'extraction
  goBtn.disabled = on; goBtn.textContent = on ? "Extraction…" : "Extraire";
  updateEmptyState();
}

let colorMode = "community", sizeMode = "centrality", showEdgeLabels = false;
let pathKeys = new Set();   // arêtes "srctgt" surlignées (chemin le plus long)
let activeES = null;        // connexion SSE d'extraction en cours
let esGen = 0;              // jeton de génération : invalide une extraction supplantée par une autre
const closeActiveES = () => { if(activeES){ activeES.close(); activeES = null; } setExtracting(false); };

// ---- bannière d'erreur (fail-loud côté UI) ----
const errorBox = document.createElement("div");
errorBox.id = "errorBanner";
errorBox.style.cssText = "position:fixed;top:64px;left:50%;transform:translateX(-50%);z-index:50;"
  + "background:#7C2A38;color:#fff;padding:8px 16px;border-radius:8px;font:14px system-ui,sans-serif;"
  + "box-shadow:0 4px 14px rgba(0,0,0,.25);max-width:80%;display:none;";
document.body.appendChild(errorBox);
let errorTimer = null;
function showError(msg){
  errorBox.textContent = msg;                 // textContent : aucune injection possible
  errorBox.style.display = "block";
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => { errorBox.style.display = "none"; }, 6000);
}

// fetch JSON fail-loud : lève sur statut non-2xx (en extrayant le `detail` renvoyé par FastAPI).
async function getJSON(url, opts){
  const r = await fetch(url, opts);
  if(!r.ok){
    let detail = `HTTP ${r.status}`;
    try { const j = await r.json(); if(j && j.detail) detail = j.detail; } catch(_){}
    throw new Error(detail);
  }
  return r.json();
}

// force-graph rend les labels en HTML : on échappe le texte LLM (XSS stocké).
function escapeHtml(str){
  return str ? String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;") : "";
}
const linkKey = (l) => (l.source.id||l.source) + "\u0001" + (l.target.id||l.target);

// ---- couleur / taille / arêtes ----
function nodeColor(n){
  if(pathKeys.size && n._inPath) return "#D6A84E";
  if(colorMode === "uniform") return cc().node;
  if(colorMode === "type") return n.kind === "value" ? cc().value : cc().node;
  if(colorMode === "set"){
    const s = n.sets || [];
    if(s.length > 1) return cc().bridge;          // hub multi-sets
    return s.length ? cc().comm[s[0] % cc().comm.length] : cc().node;
  }
  return cc().comm[(n.community || 0) % cc().comm.length];   // community
}
function nodeVal(n){
  if(sizeMode === "fixed") return 1;
  if(sizeMode === "mentions") return 1 + (n.mentions || 0);
  return 1 + (n.centrality || 0);              // centralité (degré)
}
function linkColor(l){
  if(pathKeys.has(linkKey(l))) return "#D6A84E";
  return l.is_bridge ? cc().bridge : T().link;
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
  ctx.font = `${f}px ${UI_FONT}`;
  ctx.fillStyle = T().text;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(link.predicate, x, y);
}
// nom de l'entité dessiné sous le nœud, au-delà d'un seuil de zoom (anti-encombrement)
const NODE_LABEL_MIN_SCALE = 0.7;
function drawNodeLabel(node, ctx, scale){
  if(scale < NODE_LABEL_MIN_SCALE) return;
  const label = node.label || node.id;
  if(!label) return;
  const r = Math.sqrt(Math.max(0, nodeVal(node))) * 4;   // rayon ≈ nodeRelSize par défaut
  const f = 12 / scale;
  ctx.font = `${f}px ${UI_FONT}`;
  ctx.fillStyle = T().text;
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.fillText(label, node.x, node.y + r + 2 / scale);
}

const G = ForceGraph()(document.getElementById("graph"))
  .nodeLabel(n => escapeHtml(n.label || n.id))
  .nodeCanvasObjectMode(() => "after").nodeCanvasObject(drawNodeLabel)
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
  const adj = new Map(); data.nodes.forEach(n => adj.set(n.id, new Set()));
  data.links.forEach(l => {
    const a = l.source.id || l.source, b = l.target.id || l.target;
    if(adj.has(a) && adj.has(b)){ adj.get(a).add(b); adj.get(b).add(a); }   // Set : dédup des prédicats parallèles
  });
  const order = [...adj.keys()].sort((x, y) => adj.get(y).size - adj.get(x).size);
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
  updateEmptyState();
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
    box.appendChild(legendRow(cc().node, "entité"));
    box.appendChild(legendRow(cc().value, "valeur"));
  }else if(colorMode === "set"){
    title.textContent = "Sets"; box.appendChild(title);
    const sets = [...new Set(data.nodes.flatMap(n => n.sets || []))].sort((a,b)=>a-b);
    sets.forEach(s => box.appendChild(legendRow(cc().comm[s % cc().comm.length], "set " + s)));
    box.appendChild(legendRow(cc().bridge, "hub multi-sets", true));
  }else if(colorMode === "community"){
    title.textContent = "Communautés"; box.appendChild(title);
    const comms = [...new Set(data.nodes.map(n => n.community || 0))].sort((a,b)=>a-b);
    comms.forEach(c => box.appendChild(legendRow(cc().comm[c % cc().comm.length], "communauté " + c)));
  }else{
    title.textContent = "Uniforme"; box.appendChild(title);
    box.appendChild(legendRow(cc().node, "nœud"));
  }
  box.appendChild(legendRow(cc().bridge, "passerelle inter-sources", true));
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
  const c = document.createElement("span"); c.append("conf ");
  const cv = document.createElement("span"); cv.className = "measure";   // mesure en mono
  cv.textContent = link.confidence == null ? "–" : link.confidence + "%";
  c.appendChild(cv); meta.appendChild(c);
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
  // idempotent : préserver l'objet-nœud existant (sa référence est déjà liée aux arêtes
  // résolues par force-graph et porte ses coordonnées) ; le recréer désancrerait les liens.
  if(!nodes.has(s)) nodes.set(s, {id:s});
  if(!nodes.has(o)) nodes.set(o, {id:o});
  const k = s + "\u0001" + (f.predicate || "") + "\u0001" + o;
  if(linkKeys.has(k)) return;
  linkKeys.add(k);
  links.push({source:s, target:o, predicate:f.predicate});
}
function redraw(){ G.graphData({nodes:[...nodes.values()], links}); applyStyles(); updateEmptyState(); }

// état vide : appel à l'action central tant qu'aucun graphe n'est affiché (premier lancement compris)
function updateEmptyState(){
  const n = G.graphData().nodes;
  document.getElementById("emptyState").style.display = (extracting || (n && n.length)) ? "none" : "flex";
}
// centre le graphe sur un nœud (résultats de recherche cliquables) ; renvoie false si absent du graphe courant
function focusNode(name){
  const node = G.graphData().nodes.find(n => n.id === name || n.label === name);
  if(!node || node.x == null) return false;
  G.centerAt(node.x, node.y, 600); G.zoom(4, 600);
  return true;
}

document.getElementById("go").addEventListener("click", async () => {
  const text = document.getElementById("txt").value.trim(); if(!text) return;
  closeActiveES();
  nodes = new Map(); links = []; linkKeys = new Set(); pathKeys = new Set(); redraw();
  setExtracting(true); setStatus("Extraction…", "live");   // retour immédiat dès le clic
  const gen = ++esGen;
  let document_id;
  try {
    ({document_id} = await getJSON("/api/documents", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({title:"Coller", text})}));
  } catch(err) {
    if(gen === esGen){          // ne pas réinitialiser l'UI d'une extraction plus récente
      setExtracting(false); setStatus(null);
      showError("Extraction impossible : " + err.message);
    }
    return;
  }
  if(gen !== esGen) return;          // un autre « Extraire » a pris la main entre-temps
  const es = new EventSource(`/api/documents/${document_id}/events`);
  activeES = es;
  es.onmessage = (e) => {
    if(activeES !== es) return;   // ignorer les messages d'un flux supplanté
    let m;
    try { m = JSON.parse(e.data); } catch(err) { console.warn("frame SSE invalide:", e.data); return; }
    if(m.type === "replay"){ m.facts.forEach(addFact); redraw(); setStatus(`Extraction… ${links.length} faits`, "live"); }
    else if(m.type === "fact" && !m.is_duplicate){ addFact(m.fact); redraw(); setStatus(`Extraction… ${links.length} faits`, "live"); }
    else if(m.type === "done" || m.type === "error"){
      es.close(); if(activeES === es) activeES = null;
      setExtracting(false);
      if(m.type === "done"){
        analyze({nodes:[...nodes.values()], links}); redraw();   // communautés/centralité sur le graphe extrait
        setStatus(`${links.length} faits extraits`, "done");
      }else{ setStatus(null); showError("Extraction échouée : " + (m.message || "")); }
    }
  };
  es.onerror = () => {           // n'agir que pour le flux courant ; signaler l'interruption (fail-loud)
    es.close();
    if(activeES === es){
      activeES = null;
      setExtracting(false); setStatus(null);
      showError("Flux d'extraction interrompu.");
    }
  };
});

// ---- navigation sets / docs / recherche / transverse (I-4) ----
async function loadSets(){
  let sets;
  try { sets = await getJSON("/api/sets"); }
  catch(err){ showError("Chargement des sets impossible : " + err.message); return; }
  const box = document.getElementById("sets"); box.replaceChildren();
  sets.forEach(s => {
    const el = document.createElement("div"); el.className = "item";
    el.append(s.name + " ");
    const cnt = document.createElement("span"); cnt.className = "measure";   // compteur en mono
    cnt.textContent = `(${s.document_count})`;
    el.appendChild(cnt);
    el.onclick = () => openSet(s.id, el);
    box.appendChild(el);
  });
}
async function openSet(id, el){
  closeActiveES();
  let detail;
  try {
    const [graphData, setDetail] = await Promise.all([    // requêtes indépendantes -> en parallèle
      getJSON(`/api/sets/${id}/graph`),
      getJSON(`/api/sets/${id}`),
    ]);
    renderGraph(graphData);
    detail = setDetail;
  } catch(err){ showError("Ouverture du set impossible : " + err.message); return; }
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
  closeActiveES();
  let facts;
  try { ({facts} = await getJSON(`/api/documents/${id}/facts`)); }
  catch(err){ showError("Ouverture du document impossible : " + err.message); return; }
  nodes = new Map(); links = []; linkKeys = new Set(); pathKeys = new Set();
  (facts || []).forEach(addFact); redraw();
}
document.getElementById("searchBtn").addEventListener("click", async () => {
  const q = document.getElementById("q").value.trim(); if(!q) return;
  let res;
  try { res = await getJSON(`/api/search?q=${encodeURIComponent(q)}`); }
  catch(err){ showError("Recherche impossible : " + err.message); return; }
  const box = document.getElementById("results"); box.replaceChildren();
  (res.results || []).forEach(r => {
    const el = document.createElement("div"); el.className = "r";
    el.textContent = `${r.subject} · ${r.predicate} · ${r.object}`;
    el.title = "Centrer sur ce nœud";              // affordance identique aux sets
    el.addEventListener("click", () => { focusNode(r.subject) || focusNode(r.object); });
    box.appendChild(el);
  });
});
document.getElementById("transBtn").addEventListener("click", async () => {
  const ent = document.getElementById("ent").value.trim(); if(!ent) return;
  closeActiveES();
  try { renderGraph(await getJSON(`/api/transverse?entity=${encodeURIComponent(ent)}`)); }
  catch(err){ showError("Sous-graphe impossible : " + err.message); }
});

// ---- contrôles ----
document.getElementById("colorMode").addEventListener("change", (e) => {
  colorMode = e.target.value; applyStyles();
});
document.getElementById("sizeMode").addEventListener("change", (e) => {
  sizeMode = e.target.value; applyStyles();
});
document.getElementById("paletteMode").addEventListener("change", (e) => {
  palette = e.target.value; localStorage.setItem("nerve-palette", palette); applyStyles();
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
document.getElementById("paletteMode").value = palette;
applyStyles();
updateEmptyState();
loadSets();
