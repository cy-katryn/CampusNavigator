"""Build a single-file HTML preview of the map, for checking before you commit.

    python tools/preview.py --serve               # live: rebuild on every save  <- use this
    python tools/preview.py                       # write the html once
    python tools/preview.py --pois X --paths Y    # any two files

--serve prints a local address; open it, edit a .geojson, save, and the page reloads
itself with the new network. Nothing else to run. Plain `python tools/preview.py`
still writes .tmp/preview/index.html for a one-off look: no server, no network.

What it is for
--------------
Checking the *network*, not looking at the picture:

  * click one place, then another, and the shortest route is drawn and measured
  * click one place and every other place is listed by real walking distance, with
    everything unreachable grouped at the bottom -- that is the "are all combinations
    possible" question, answered for one origin at a time
  * junctions are drawn as small dots, so you can see whether two lines really meet
  * the diagnostics at the top name the places that no route can reach

It reuses tools/build.py, so the graph here is the graph the app would get: same
vertices, same contraction, same attachment rule. If a route is wrong here it is wrong
there.

Why live mode needs a process
-----------------------------
A page opened as a file cannot read your .geojson -- browsers refuse file:// fetches --
and no static file can learn that you pressed Save. So --serve does the two things a
file cannot: it watches the three inputs by timestamp and re-runs the builder, and it
hands the tab a /status stamp the page compares against its own. A save therefore shows
up in about a second, with no command to retype.
"""
from __future__ import annotations

import argparse
import html as html_lib
import importlib.util
import json
import math
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / ".tmp" / "preview"

# There is no tolerance anywhere in this tool. A place is connected when its coordinate
# is one that a drawn line passes through, and not otherwise: no snapping, no projection
# onto a segment, no "near enough". You asked to check the network yourself, which means
# the page must not close gaps on your behalf -- not even ones that look obviously meant.


def load_builder():
    spec = importlib.util.spec_from_file_location("builder", ROOT / "tools" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def features(path: Path, geometry: str) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))  # a BOM is a save artefact, not data
    return [
        f for f in raw.get("features") or []
        if ((f.get("geometry") or {}).get("type") or "").startswith(geometry)
    ]


def segment_metres(lat: float, lng: float, a: tuple, b: tuple) -> float:
    """Distance from a point to a segment, in a local flat approximation."""
    k = math.cos(math.radians(lat))
    ax, ay = (a[1] - lng) * 111320.0 * k, (a[0] - lat) * 111320.0
    bx, by = (b[1] - lng) * 111320.0 * k, (b[0] - lat) * 111320.0
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dy = (lat1 - lat2) * 111320.0
    dx = (lng1 - lng2) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dx, dy)


def components(nodes: list[dict], edges: list[dict]) -> list[list[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adj[edge["u"]].add(edge["v"])
        adj[edge["v"]].add(edge["u"])
    seen, out = set(), []
    for node in nodes:
        if node["id"] in seen or not adj[node["id"]]:
            continue
        queue, comp = deque([node["id"]]), []
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            queue.extend(adj[cur] - seen)
        out.append(comp)
    out.sort(key=len, reverse=True)
    return out


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Map preview</title>
<style>
  :root {
    --ink: #2a1f22; --muted: #7d6b70; --line: #e0dada; --panel: #ffffff;
    --land: #f2efe9; --path: #cfcac2; --route: #0f62d6; --accent: #a02338;
    --fp-fill: #e8d9c8; --fp-edge: #b08d6a; --ground-fill: #dfe6d5; --ground-edge: #aab89a;
    --label: #6b5340;
    --building: #6d3010; --service: #6b5b95; --entry: #a02338; --place: #2b6cb0;
    --bad: #b3261e; --stair: #b45309;
  }
  * { box-sizing: border-box; }
  body { margin: 0; display: flex; font: 13px/1.45 "Segoe UI", system-ui, sans-serif; color: var(--ink); }
  #map { flex: 1; height: 100vh; background: var(--land); cursor: crosshair; }
  aside { width: 340px; height: 100vh; overflow: auto; border-left: 1px solid var(--line);
          background: var(--panel); padding: 12px 14px; }
  h1 { font-size: 14px; margin: 0 0 2px; letter-spacing: .01em; }
  .sub { color: var(--muted); font-size: 11.5px; margin-bottom: 10px; }
  .row { display: flex; gap: 6px; align-items: center; margin: 4px 0; }
  .row b { min-width: 52px; font-weight: 600; }
  input[type=search] { width: 100%; padding: 6px 8px; border: 1px solid var(--line); border-radius: 8px; font: inherit; }
  button { font: inherit; padding: 4px 9px; border: 1px solid var(--line); border-radius: 8px;
           background: #f8f2f3; color: var(--muted); cursor: pointer; }
  button:hover { color: var(--ink); }
  .stat { display: flex; justify-content: space-between; font-size: 12px; padding: 1px 0; }
  .stat span:last-child { font-variant-numeric: tabular-nums; }
  .sep { height: 1px; background: var(--line); margin: 10px 0; }
  #list { margin-top: 6px; }
  .item { display: flex; justify-content: space-between; gap: 8px; padding: 3px 6px;
          border-radius: 6px; cursor: pointer; }
  .item:hover { background: #f9eef0; }
  .item.on { background: #f9eef0; outline: 1px solid #cdb4b9; }
  .item i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .item em { font-style: normal; color: var(--muted); font-variant-numeric: tabular-nums; }
  .unreach { color: var(--bad); }
  .nowrap-warn { color: var(--muted); }
  .warn { color: var(--bad); }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 999px; background: #f2e0e2; font-size: 11px; }
  .key { display: flex; align-items: center; gap: 7px; font-size: 11.5px; color: var(--muted); padding: 2px 0; }
  .key i { width: 22px; border-top: 2.6px solid var(--path); display: inline-block; }
  .key i.k-stair { border-top-color: var(--stair); }
  .key i.k-orphan { border-top-color: var(--bad); border-top-style: dashed; }
  .key i.k-derived { border-top-color: #4a7d3f; border-top-style: dotted; border-top-width: 3.2px; }
  .key i.k-structure { border-top: 0; height: 9px; width: 14px; background: var(--fp-fill);
                       border: 1px solid var(--fp-edge); border-radius: 2px; }
  .key i.k-route { border-top-color: var(--route); }
  .banner { background: #fdf3e3; border: 1px solid #dfae6c; color: #7a4a09;
            border-radius: 8px; padding: 8px 10px; font-size: 11.5px;
            line-height: 1.5; margin: 8px 0; }
  .banner b { color: #5e3606; }
  code { background: #f3ece1; padding: 0 3px; border-radius: 3px; font-size: 11px; }
  .stamp { font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .stamp .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
                margin-right: 5px; vertical-align: middle; background: #bbb; }
  .stamp .dot.live { background: #2f9e44; }
  .stamp .dot.dead { background: var(--bad); }
  #list .item.off { opacity: .45; }
  .toggles { display: flex; gap: 10px; font-size: 11.5px; color: var(--muted); margin: 4px 0 6px; }
  .toggles label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
</style>
<svg id="map" xmlns="http://www.w3.org/2000/svg"></svg>
<aside>
  <h1>Map preview</h1>
  <div class="sub" id="sources"></div>
  <div class="stamp"><span id="watch" class="dot"></span><span id="built"></span></div>
  <div id="stale"></div>
  <div id="stats"></div>
  <div id="legend"></div>
  <div id="frags"></div>
  <div class="sep"></div>
  <div class="row"><b>from</b><span id="from">click a place</span></div>
  <div class="row"><b>to</b><span id="to">click another</span></div>
  <div class="row"><b>route</b><span id="result">&mdash;</span></div>
  <div class="row"><button id="clear">clear</button><button id="fit">fit</button><button id="rebuild">rebuild</button></div>
  <div class="toggles"><label><input type="checkbox" id="only-broken">
    only places the network cannot reach</label></div>
  <div class="sep"></div>
  <input type="search" id="q" placeholder="filter places (name, kind, floor)">
  <div id="list"></div>
</aside>
<script>
const DATA = __DATA__;
const nodes = DATA.nodes, edges = DATA.edges;
const byId = new Map(nodes.map(n => [n.id, n]));
const M = 111320;
const lats = nodes.map(n => n.lat), lngs = nodes.map(n => n.lng);
/* Structures can reach past the outermost path, and the initial view must hold them
   too, or the map opens with buildings sliced off at the edge. */
(DATA.shapes || []).forEach(s => (s.rings || []).forEach(r => r.forEach(p => {
  lats.push(p[0]); lngs.push(p[1]);
})));
const bounds = {
  minLat: Math.min(...lats) - 0.0006, maxLat: Math.max(...lats) + 0.0006,
  minLng: Math.min(...lngs) - 0.0006, maxLng: Math.max(...lngs) + 0.0006,
};
bounds.cos = Math.cos(((bounds.minLat + bounds.maxLat) / 2) * Math.PI / 180);
const W = (bounds.maxLng - bounds.minLng) * M * bounds.cos;
const H = (bounds.maxLat - bounds.minLat) * M;
let view = { x: 0, y: 0, w: W, h: H };

function project(lat, lng) {
  return { x: (lng - bounds.minLng) * M * bounds.cos, y: (bounds.maxLat - lat) * M };
}
function applyView() {
  $('map').setAttribute('viewBox', view.x + ' ' + view.y + ' ' + view.w + ' ' + view.h);
}

const NS = 'http://www.w3.org/2000/svg';
function el(name, attrs) {
  const n = document.createElementNS(NS, name);
  Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v));
  return n;
}
function $(id) { return document.getElementById(id); }

const COLOUR = { building: 'var(--building)', service: 'var(--service)',
                 entrance: 'var(--entry)', place: 'var(--place)' };

/* Structures are drawn and nothing else. A polygon is a picture of a building, never
   a destination: no node is created from one, so nothing here can be routed to. The
   optional "display" property prints a short label at the polygon's centroid, which is
   how Core A gets to say "A" without becoming a place. */
function ringPath(rings) {
  return rings.map(ring => ring.map((p, i) => {
    const q = project(p[0], p[1]);
    return (i ? 'L' : 'M') + q.x.toFixed(1) + ' ' + q.y.toFixed(1);
  }).join(' ') + ' Z').join(' ');
}

function centroidOf(ring) {
  let twice = 0, cx = 0, cy = 0;
  for (let i = 0; i < ring.length; i++) {
    const a = project(ring[i][0], ring[i][1]);
    const b = project(ring[(i + 1) % ring.length][0], ring[(i + 1) % ring.length][1]);
    const cross = a.x * b.y - b.x * a.y;
    twice += cross; cx += (a.x + b.x) * cross; cy += (a.y + b.y) * cross;
  }
  if (!twice) {                       // degenerate ring: fall back to the average
    const xs = ring.map(p => project(p[0], p[1]).x), ys = ring.map(p => project(p[0], p[1]).y);
    return { x: xs.reduce((s, v) => s + v, 0) / xs.length,
             y: ys.reduce((s, v) => s + v, 0) / ys.length };
  }
  return { x: cx / (3 * twice), y: cy / (3 * twice) };
}

function drawShapes() {
  const g = el('g', { id: 'layer-shapes' });
  const labels = el('g', { id: 'layer-labels' });
  (DATA.shapes || []).forEach(s => {
    if (!s.rings || !s.rings.length) return;
    const building = s.kind === 'building';
    g.appendChild(el('path', {
      d: ringPath(s.rings), fill: building ? 'var(--fp-fill)' : 'var(--ground-fill)',
      'fill-rule': 'evenodd',
      stroke: building ? 'var(--fp-edge)' : 'var(--ground-edge)',
      'stroke-width': 0.8, 'stroke-linejoin': 'round',
    }));
    if (!s.display) return;
    const c = centroidOf(s.rings[0]);
    const t = el('text', {
      x: c.x.toFixed(1), y: c.y.toFixed(1), 'font-size': 11, 'text-anchor': 'middle',
      'dominant-baseline': 'middle', fill: 'var(--label)', 'font-weight': 600,
      'pointer-events': 'none',
    });
    t.textContent = s.display;
    labels.appendChild(t);
  });
  return [g, labels];
}

function drawBase() {
  const svg = $('map');
  svg.innerHTML = '';
  const [gShapes, gLabels] = drawShapes();
  const gPaths = el('g', {}), gDots = el('g', {}), gPlaces = el('g', {});
  svg.appendChild(gShapes);
  edges.forEach(e => {
    const pts = (e.path && e.path.length > 1) ? e.path : null;
    if (!pts) return;
    const d = pts.map((p, i) => (i ? 'L' : 'M') + project(p[0], p[1]).x.toFixed(1) + ' '
              + project(p[0], p[1]).y.toFixed(1)).join(' ');
    gPaths.appendChild(el('path', {
      d: d, fill: 'none', 'stroke-linecap': 'round',
      stroke: e.orphan ? 'var(--bad)' : (e.stairs ? 'var(--stair)' : 'var(--path)'),
      'stroke-width': 1.6,
      'stroke-dasharray': e.orphan ? '4 3' : null,
    }));
  });
  nodes.forEach(n => {
    const p = project(n.lat, n.lng);
    if (n.kind === 'waypoint') {
      gDots.appendChild(el('circle', { cx: p.x, cy: p.y, r: 1.1, fill: '#8c8377' }));
      return;
    }
    const g = el('g', { 'data-id': n.id });
    g.appendChild(el('circle', { cx: p.x, cy: p.y, r: n.kind === 'building' ? 4 : 3,
                                 fill: COLOUR[n.kind] || 'var(--muted)', stroke: '#fff',
                                 'stroke-width': .7 }));
    g.addEventListener('click', ev => { ev.stopPropagation(); pick(n.id); });
    gPlaces.appendChild(g);
  });
  svg.appendChild(gPaths); svg.appendChild(gDots); svg.appendChild(gPlaces);
  svg.appendChild(gLabels);
  svg.appendChild(el('g', { id: 'layer-route' }));
  applyView();
}

/* Dijkstra from one origin: every distance at once, which is what "all the possible
   paths from here" actually asks. */
function distances(origin) {
  const adj = new Map(nodes.map(n => [n.id, []]));
  edges.forEach(e => { adj.get(e.u).push([e.v, e.metres, e]); adj.get(e.v).push([e.u, e.metres, e]); });
  const dist = new Map(), prev = new Map(), done = new Set();
  dist.set(origin, 0);
  const frontier = [[0, origin]];
  while (frontier.length) {
    frontier.sort((a, b) => a[0] - b[0]);
    const [d, u] = frontier.shift();
    if (done.has(u)) continue;
    done.add(u);
    for (const [v, w, e] of adj.get(u) || []) {
      const nd = d + w;
      if (nd < (dist.has(v) ? dist.get(v) : Infinity)) {
        dist.set(v, nd); prev.set(v, [u, e]);
        frontier.push([nd, v]);
      }
    }
  }
  return { dist, prev };
}

/* One rule, no fallbacks: a place is on the network when it shares a coordinate with a
   drawn vertex, and then the place *is* that vertex. Everything else has no hop. No
   nearest-vertex welding, no "reached through the building", no parent lookup. */
const LINKED = new Set();
edges.forEach(e => { LINKED.add(e.u); LINKED.add(e.v); });
function hop(n) {
  if (!n) return null;
  return LINKED.has(n.id) ? n.id : null;
}

let origin = null, target = null, last = null;
function pick(id) {
  const node = byId.get(id);
  if (!node || node.kind === 'waypoint') return;
  if (!origin || (origin && target)) { origin = id; target = null; }
  else target = id;
  recompute();
}

function recompute() {
  const route = $('layer-route');
  if (route) route.innerHTML = '';
  const label = id => {
    const n = byId.get(id);
    if (!n) return id;
    const extra = (n.places && n.places.length > 1) ? ' +' + (n.places.length - 1) : '';
    const own = (n.name || n.id) + extra;
    const at = hop(n);
    if (at === n.id) return own;
    if (!at) return own + ' \u2014 not on any path';
    return own + ' (via ' + ((byId.get(at) || {}).name || at) + ')';
  };
  $('from').textContent = origin ? label(origin) : 'click a place';
  $('to').textContent = target ? label(target) : 'click another';
  if (!origin) { $('result').textContent = '\u2014'; renderList([]); return; }
  const oHop = hop(byId.get(origin)), tHop = target ? hop(byId.get(target)) : null;
  if (!oHop) {
    const n = byId.get(origin);
    const near = (n && n.near !== null && n.near !== undefined)
      ? n.near + ' m from the nearest drawn line'
      : 'no drawn line nearby';
    $('result').innerHTML = '<span class="warn">not on any path</span> &mdash; ' + near;
    last = null; renderList(); return;
  }

  last = distances(oHop);
  const metres = tHop ? last.dist.get(tHop) : null;
  $('result').innerHTML = target
    ? (metres === undefined ? '<span class="warn">no route</span>' : Math.round(metres) + ' m')
    : '\u2014';

  if (tHop && metres !== undefined && route) {
    /* Walk back from the target, then read the chain forwards, orienting every edge's
       polyline to the direction we travel it. Each stored polyline runs one fixed way;
       appending them blindly throws a straight chord across every bend traversed
       backwards -- 73 m of phantom line on a 562 m route -- which is what made one
       route look like two. */
    const order = [tHop];
    let cur = tHop;
    while (cur !== oHop) {
      const step = last.prev.get(cur);
      if (!step) break;
      cur = step[0];
      order.push(cur);
    }
    order.reverse();
    const pts = [];
    for (let i = 0; i + 1 < order.length; i++) {
      const a = byId.get(order[i]), b = byId.get(order[i + 1]);
      const step = last.prev.get(order[i + 1]);
      const raw = step && step[1] && step[1].path;
      let line = (raw && raw.length > 1) ? raw.map(p => [p[0], p[1]])
                                         : [[a.lat, a.lng], [b.lat, b.lng]];
      const from = q => Math.hypot(q[0] - a.lat, q[1] - a.lng);
      if (from(line[0]) > from(line[line.length - 1])) line = line.slice().reverse();
      line.forEach(p => pts.push(project(p[0], p[1])));
    }
    if (pts.length > 1) {
      const d = pts.map((p, i) => (i ? 'L' : 'M') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
      // One stroke, one line. A white casing underneath made this read as two parallel
      // lines, which is a decoration nobody asked for and a lie about the geometry.
      route.appendChild(el('path', { d: d, fill: 'none', stroke: 'var(--route)',
                                     'stroke-width': 3.4, 'stroke-linecap': 'round',
                                     'stroke-linejoin': 'round' }));
    }
  }
  renderList();
}

function renderList() {
  const q = ($('q').value || '').toLowerCase();
  const places = nodes.filter(n => n.kind !== 'waypoint');
  const rows = places.map(n => {
    const at = hop(n);
    const d = last && at ? last.dist.get(at) : undefined;
    return { n: n, d: d, at: at };
  }).filter(r => !q || ((r.n.name || '') + ' ' + (r.n.kind || '') + ' ' + (r.n.floor || '')).toLowerCase().includes(q))
    .filter(r => !($('only-broken') && $('only-broken').checked) || (last && r.d === undefined));
  rows.sort((a, b) => {
    const da = a.d === undefined ? Infinity : a.d, db = b.d === undefined ? Infinity : b.d;
    return da - db;
  });
  const list = $('list');
  list.innerHTML = '';
  rows.forEach(r => {
    const div = document.createElement('div');
    div.className = 'item' + (r.n.id === origin || r.n.id === target ? ' on' : '');
    const left = document.createElement('span');
    const dot = document.createElement('i');
    dot.style.background = COLOUR[r.n.kind] || 'var(--muted)';
    left.appendChild(dot);
    const label = document.createElement('span');
    const also = (r.n.places && r.n.places.length > 1) ? ' +' + (r.n.places.length - 1) : '';
    label.textContent = ((r.n.name || r.n.id) + also).slice(0, 42);
    // Several POIs can share one published coordinate; they are one node, so name them all.
    if (r.n.places && r.n.places.length > 1) div.title = r.n.places.join(', ');
    left.appendChild(label);
    const right = document.createElement('em');
    const unreachable = last && r.d === undefined;
    const noPath = unreachable && !r.at;
    right.textContent = last
      ? (noPath ? 'no path' + (r.n.near !== null && r.n.near !== undefined ? ' \u00b7 ' + r.n.near + ' m' : '')
                : unreachable ? 'no route' : Math.round(r.d) + ' m')
      : '';
    if (noPath) right.className = 'nowrap-warn';
    else if (unreachable) right.className = 'unreach';
    div.appendChild(left); div.appendChild(right);
    div.addEventListener('click', () => pick(r.n.id));
    list.appendChild(div);
  });
}

$('clear').addEventListener('click', () => { origin = null; target = null; last = null; recompute(); });
$('fit').addEventListener('click', () => { view = { x: 0, y: 0, w: W, h: H }; applyView(); });
$('q').addEventListener('input', renderList);
$('only-broken').addEventListener('change', renderList);

/* Click a stray stub and the map jumps to it, so the list is a to-do, not a notice. */
function zoomTo(points) {
  const pad = 30;
  const xs = points.map(p => project(p.lat, p.lng).x), ys = points.map(p => project(p.lat, p.lng).y);
  const x = Math.min(...xs) - pad, y = Math.min(...ys) - pad;
  const w = Math.max(...xs) - Math.min(...xs) + pad * 2;
  const h = Math.max(...ys) - Math.min(...ys) + pad * 2;
  view = { x: x - Math.max(0, (h - w) / 2), y: y - Math.max(0, (w - h) / 2),
           w: Math.max(w, h), h: Math.max(w, h) };
  applyView();
}

function renderFragments() {
  const box = $('frags');
  box.innerHTML = '';
  if (!DATA.fragments.length) {
    box.innerHTML = '<div class="row"><b>network</b><span>one piece &mdash; nothing stray</span></div>';
    return;
  }
  const head = document.createElement('div');
  head.className = 'row';
  head.innerHTML = '<b>stray stubs</b><span class="warn">' + DATA.fragments.length +
                   ' not joined</span>';
  box.appendChild(head);
  const note = document.createElement('div');
  note.className = 'sub';
  note.textContent = 'Lines that never share a vertex with the main network (dashed red). ' +
                     'A stub can sit 2 cm from a path and still be separate: lines join at a ' +
                     'vertex, never along a segment. Click one to jump to it.';
  box.appendChild(note);
  DATA.fragments.forEach((f, k) => {
    const div = document.createElement('div');
    div.className = 'item';
    const left = document.createElement('span');
    left.textContent = (f.names[0] || ('stub ' + (k + 1) + ' (' + f.ids.length + ' pts)')).slice(0, 30);
    const right = document.createElement('em');
    right.className = 'unreach';
    right.textContent = f.metres + ' m';
    div.appendChild(left); div.appendChild(right);
    if (f.segment_m !== null && f.segment_m !== undefined && f.segment_m < 1) {
      // 0.04 m from a line and still disconnected is not a gap, it is a missing vertex.
      const why = document.createElement('div');
      why.className = 'sub';
      why.style.margin = '0 0 4px 14px';
      why.innerHTML = 'lies <b>' + f.segment_m + ' m</b> from the <b>' + f.segment_on +
                      '</b> line, but no vertex is there: click that line to add one at the ' +
                      'meeting point, then drag this end onto it';
      div.style.flexWrap = 'wrap';
      div.appendChild(why);
    }
    div.title = 'nearest main junction: ' + f.join;
    div.addEventListener('click', () => {
      const pts = f.ids.map(i => byId.get(i)).filter(Boolean);
      if (f.join_at) pts.push(byId.get(f.join) || { lat: f.join_at[0], lng: f.join_at[1] });
      zoomTo(pts);
    });
    box.appendChild(div);
  });
}

/* pan + zoom */
let drag = null;
$('map').addEventListener('mousedown', ev => { drag = { x: ev.clientX, y: ev.clientY, v: Object.assign({}, view) }; });
window.addEventListener('mouseup', () => { drag = null; });
window.addEventListener('mousemove', ev => {
  if (!drag) return;
  const svg = $('map').getBoundingClientRect();
  view.x = drag.v.x - (ev.clientX - drag.x) * (view.w / svg.width);
  view.y = drag.v.y - (ev.clientY - drag.y) * (view.h / svg.height);
  applyView();
});
$('map').addEventListener('wheel', ev => {
  ev.preventDefault();
  const k = ev.deltaY > 0 ? 1.12 : 0.89;
  const svg = $('map').getBoundingClientRect();
  const fx = view.x + (ev.clientX - svg.left) * (view.w / svg.width);
  const fy = view.y + (ev.clientY - svg.top) * (view.h / svg.height);
  view.w *= k; view.h *= k;
  view.x = fx - (fx - view.x) * k;
  view.y = fy - (fy - view.y) * k;
  applyView();
}, { passive: false });

/* Served or saved? The page can tell, and it matters: a copy opened from disk is a
   photograph of the network and cannot follow your edits. Say so instead of letting
   you wonder why nothing moved. */
const STAMP = "__STAMP__";
const SERVED = /^https?:$/.test(location.protocol);
/* location.reload() can be answered out of the browser's memory or disk cache. A fresh
   query string cannot, so reload through one. */
function hardReload() {
  const url = new URL(location.href);
  url.searchParams.set('t', Date.now().toString());
  location.replace(url.toString());
}

if (SERVED) {
  const dot = $('watch');
  let failures = 0;
  function check() {
    fetch('status', { cache: 'no-store' })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error('status ' + r.status))))
      .then(j => {
        failures = 0;
        if (dot) dot.className = 'dot live';
        if (j.stamp !== STAMP) hardReload();
      })
      .catch(() => {
        failures += 1;
        if (dot && failures >= 3) dot.className = 'dot dead';
      });
  }
  setInterval(check, 1200);
  /* A hidden tab has its timers throttled to about once a minute, which is exactly the
     "I switched back and nothing had changed" case. Check the moment it is seen. */
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  window.addEventListener('focus', check);
  $('built').textContent = 'live \u00b7 built ' + DATA.built;
  $('rebuild').addEventListener('click', () => {
    fetch('rebuild', { cache: 'no-store' }).then(hardReload, hardReload);
  });
} else {
  $('built').textContent = 'snapshot built ' + DATA.built;
  $('rebuild').style.display = 'none';
  $('stale').innerHTML =
    '<div class="banner"><b>This is a saved copy, not the live one.</b><br>' +
    'Edits to the .geojson files will never appear here. Run ' +
    '<code>python tools\\preview.py --serve</code> and open ' +
    '<code>http://127.0.0.1:8770/</code> instead: that page rebuilds itself ' +
    'every time you save.</div>';
}

$('sources').textContent = DATA.sources;
$('stats').innerHTML = DATA.stats;
$('legend').innerHTML = DATA.legend;
renderFragments();
drawBase();
recompute();
</script>
"""


def render(
    poi_path: Path,
    path_path: Path,
    buildings_path: Path | None = None,
) -> tuple[str, list[str]]:
    """Build the page once, from the two authored files and nothing else.

    No building polygons. A polygon makes the builder derive a node at its centre, and
    that node is in no file you can edit -- which is how a "House of Innovation" that
    exists only in campus_buildings.geojson turned up here looking like a mystery. This
    page shows the network you drew, so the only inputs are the two files, and the
    result is checked: every node must be a Point from the POI file or a line vertex.
    """
    builder = load_builder()
    for path in (poi_path, path_path):
        if not path.exists():
            raise SystemExit(f"missing {path}")

    points = features(poi_path, "Point")
    lines = features(path_path, "LineString")

    # Every POI in that file names the building it sits in ("Core A"), and a name only
    # becomes a node when a polygon supplies one. With the polygons gone the name
    # resolves to nothing, and build.py's indoor rule would then leave 278 facilities
    # with no hop at all -- unreachable, and impossible to test. So drop the name here:
    # every POI becomes its own destination, attached to the nearest path you drew.
    # Those attachments are drawn as dotted connectors, never as walkways.
    flat, named = [], 0
    for feature in points:
        copy_of = dict(feature)
        copy_of["properties"] = dict(feature.get("properties") or {})
        if copy_of["properties"].pop("parent", None):
            named += 1
        flat.append(copy_of)
    points = flat
    graph = builder.build_graph(points, lines, [], 0.0, False)   # never weld anything
    # Structures come back, but only as geometry: they are drawn and nothing else. No
    # node, no anchor, no destination -- "routable" on a polygon is a property of a
    # model we no longer have, so it is deliberately not read here.
    shapes = []
    if buildings_path is not None and buildings_path.exists():
        for feature in features(buildings_path, "Polygon"):
            rings = [[[round(c[1], 6), round(c[0], 6)] for c in ring]
                     for ring in (((feature.get("geometry") or {}).get("coordinates")) or [])]
            rings = [ring for ring in rings if len(ring) > 2]
            if not rings:
                continue
            props = feature.get("properties") or {}
            shapes.append({
                "name": props.get("name:en") or props.get("name") or "",
                "display": str(props.get("display") or "").strip(),
                "kind": "building" if props.get("building") else "ground",
                "rings": rings,
            })

    sources = (f"{len(points)} points from {poi_path.name} + "
               f"{len(lines)} lines from {path_path.name}")
    if shapes:
        sources += f" + {len(shapes)} structures drawn ({buildings_path.name}, never routable)"
    sources += " \u00b7 a place is connected only where it shares a coordinate with a drawn vertex"

    nodes = graph["nodes"]
    edges = [e for e in graph["edges"]]
    places = [n for n in nodes if n["kind"] != "waypoint"]
    junction_count = len(nodes) - len(places)

    # Every node has to be traceable to one of the two files. A node that is neither a
    # Point nor a line vertex is something the builder invented, and inventing nodes is
    # precisely what this page must not do -- so check it instead of claiming it.
    authored = set()
    for f in points:
        coords = (f.get("geometry") or {}).get("coordinates")
        if (f.get("geometry") or {}).get("type") == "Point" and coords:
            authored.add((round(coords[1], 6), round(coords[0], 6)))
    for f in lines:
        for c in ((f.get("geometry") or {}).get("coordinates") or []):
            authored.add((round(c[1], 6), round(c[0], 6)))
    invented = [n for n in nodes
                if (round(n["lat"], 6), round(n["lng"], 6)) not in authored]
    kind_of = {n["id"]: n["kind"] for n in nodes}
    by_id = {n["id"]: n for n in nodes}

    # ---- strict joining: nothing connects unless it shares a coordinate
    # A place joins the network only where its coordinate is one a drawn line actually
    # passes through. No tolerance, no projecting a place onto a segment, no geometry the
    # file does not contain. If the coordinate is not there, the place is not connected
    # and the page reports the distance instead of joining it to the nearest thing and
    # calling that connected. build.py's own attachment is discarded here: it welds every
    # place to the nearest walkway vertex whatever the distance.
    def coord(lat: float, lng: float) -> tuple:
        return (round(lat, 6), round(lng, 6))

    drawn_segments, walkway_edges = [], []
    for edge in graph["edges"]:
        if kind_of.get(edge["u"]) != "waypoint" or kind_of.get(edge["v"]) != "waypoint":
            continue
        walkway_edges.append(edge)
        polyline = edge.get("path") or []
        for p, q in zip(polyline, polyline[1:]):
            drawn_segments.append((p[0], p[1], q[0], q[1], edge["u"], edge["v"]))

    def polyline_metres(points: list) -> float:
        return sum(metres(p[0], p[1], q[0], q[1]) for p, q in zip(points, points[1:]))

    node_at = {coord(n["lat"], n["lng"]): n["id"] for n in nodes if n["kind"] == "waypoint"}
    vertex_total = len(node_at)   # junctions in the drawn network, before any merging
    through: dict[tuple, list] = defaultdict(list)
    for index, edge in enumerate(walkway_edges):
        for point in (edge.get("path") or []):
            through[coord(point[0], point[1])].append(index)

    joined, off_line = [], []
    nearest_line: dict[str, float | None] = {}
    nearest_vertex: dict[str, float | None] = {}

    def absorb(node: dict, place: dict) -> None:
        """The place stands on this vertex, so it *is* this vertex: no link, no new node."""
        joined.append(place["id"])
        node.setdefault("places", []).append(place.get("name") or place["id"])
        if not node.get("name"):
            node["name"] = place.get("name") or ""
            node["kind"] = place["kind"]
            for field in ("floor", "service", "building", "name_zh", "accepts"):
                if place.get(field):
                    node[field] = place[field]
            kind_of[node["id"]] = node["kind"]

    splits: dict[tuple, dict] = {}
    for place in places:
        here = coord(place["lat"], place["lng"])
        if here in node_at:
            absorb(by_id[node_at[here]], place)
            continue
        if here in through:
            # the coordinate is on a bend the builder contracted away: put the junction
            # back. It is a vertex of the file, not a point we invented.
            node = splits.get(here)
            if node is None:
                node = {"id": f"V{len(splits) + 1:04d}", "name": "", "kind": "waypoint",
                        "lat": here[0], "lng": here[1], "floor": "", "parent": "",
                        "building": "", "service": "", "source": "vertex", "name_zh": "",
                        "accepts": []}
                splits[here] = node
                nodes.append(node)
                by_id[node["id"]] = node
                kind_of[node["id"]] = "waypoint"
            absorb(node, place)
            continue
        off_line.append(place)
        if drawn_segments:
            nearest_line[place["id"]] = round(min(
                segment_metres(place["lat"], place["lng"], (a[0], a[1]), (a[2], a[3]))
                for a in drawn_segments), 2)
        corners = [(n["lat"], n["lng"]) for n in nodes if n["kind"] == "waypoint"]
        if corners:
            nearest_vertex[place["id"]] = round(min(
                metres(place["lat"], place["lng"], c[0], c[1]) for c in corners), 2)

    per_edge: dict[int, list] = defaultdict(list)
    for here, node in splits.items():
        for index in set(through[here]):
            per_edge[index].append((here, node["id"]))
    edges = []
    for index, edge in enumerate(walkway_edges):
        cuts = per_edge.get(index)
        polyline = [(p[0], p[1]) for p in (edge.get("path") or [])]
        if not cuts:
            edges.append(edge)
            continue
        where = {}
        for i, point in enumerate(polyline):
            where.setdefault(coord(point[0], point[1]), i)
        cuts.sort(key=lambda c: where.get(c[0], 0))
        stops = [(polyline[0], edge["u"])]
        stops += [((here[0], here[1]), nid) for here, nid in cuts]
        stops.append((polyline[-1], edge["v"]))
        for (pa, na), (pb, nb) in zip(stops, stops[1:]):
            piece = polyline[where[coord(*pa)]:where[coord(*pb)] + 1]
            if len(piece) < 2:
                continue
            edges.append({
                "id": "", "u": na, "v": nb,
                "metres": round(polyline_metres(piece), 1), "open": True,
                "covered": bool(edge.get("covered")), "stairs": bool(edge.get("stairs")),
                "path": [[round(p[0], 6), round(p[1], 6)] for p in piece],
            })
    # A joined place has become its vertex. Leaving the original point in the graph would
    # put the same name in the list twice: once on the network, once as an orphan.
    merged_ids = set(joined)
    nodes[:] = [n for n in nodes if n["id"] not in merged_ids]
    by_id = {n["id"]: n for n in nodes}

    inserted = len(splits)
    junction_count = vertex_total + inserted
    merged_vertices = sum(1 for n in nodes if n.get("places"))

    comps = components(nodes, edges)
    linked = {n["id"] for e in edges for n in ({"id": e["u"]}, {"id": e["v"]})}
    # A joined place has no edge of its own -- it *is* the vertex -- so "has an edge"
    # would call all 169 of them unreachable. The honest test is the coordinate test.
    isolated = off_line
    main = set(comps[0]) if comps else set()
    off_main = [n for n in nodes
                if n["id"] in linked and n["id"] not in main and n.get("places")]

    stats = [
        ("places", len(places)),
        ("walkway junctions", junction_count),
        ("links you drew", len(edges)),
        ("networks", len(comps)),
        ("places not on any path", len(isolated)),
        ("places on a floating stub", sum(len(n["places"]) for n in off_main)),
        ("places joined at a shared vertex", len(joined)),
        ("vertices carrying a place", merged_vertices),
        ("junctions inserted to join them", inserted),
        ("nodes not from your two files", len(invented)),
        ("structures drawn, not routable", len(shapes)),
        ("structures with no name", sum(1 for s in shapes if not s["name"])),
        ("structures with a display label", sum(1 for s in shapes if s["display"])),
        ("POIs not in the graph (records + off campus)", len(points) - len(places)),
    ]
    # 30 red lines with no explanation is a puzzle, not a diagnostic. Name the three
    # line styles and count them, so the picture explains itself.
    walkway_edges = [e for e in edges
                     if kind_of.get(e["u"]) == "waypoint" and kind_of.get(e["v"]) == "waypoint"]
    orphan_edges = sum(1 for e in walkway_edges if e["u"] not in main or e["v"] not in main)
    stair_edges = sum(1 for e in walkway_edges
                      if e.get("stairs") and e["u"] in main and e["v"] in main)
    plain_edges = len(walkway_edges) - orphan_edges - stair_edges
    legend_html = "".join(
        f'<div class="key"><i class="{cls}"></i>{text}'
        + (f' &mdash; {count} link{"" if count == 1 else "s"}' if count is not None else "")
        + "</div>"
        for cls, text, count in (
            ("", "walkway you drew", plain_edges),
            ("k-stair", "steps", stair_edges),
            ("k-orphan", "not joined to the network", orphan_edges),
            ("k-structure", "structure (a picture, never a destination)", len(shapes)),
            ("k-route", "the route you are looking at", None),
        )
    )

    stat_html = "".join(
        f'<div class="stat"><span>{name}</span>'
        f'<span class="{ "warn" if name.startswith("places") and value else ""}">{value}</span></div>'
        for name, value in stats
    )
    for label, group in (("&#9888; on no path at all", isolated),
                         ("&#9888; on a floating stub", off_main)):
        if group:
            names = ", ".join((n.get("name") or n["id"])[:22] for n in group[:6])
            stat_html += (f'<div class="stat"><span class="warn">{label}</span>'
                          f'<span>{names}</span></div>')

    # Each stray stub, with the nearest main junction and how far short it falls.
    # "10 m short of W0060" is a repair instruction; "10 stray fragments" is only a symptom.
    lookup = {n["id"]: n for n in nodes}

    # Two faults look identical in a list of stubs but need opposite fixes: a real gap,
    # and a line that stops ON another line, between two of its vertices. Measuring the
    # distance to the nearest segment as well as to the nearest vertex tells them apart.
    main_segments = [(lat1, lng1, lat2, lng2, eu, ev)
                     for lat1, lng1, lat2, lng2, eu, ev in drawn_segments
                     if eu in main and ev in main]

    fragments = []
    for comp in comps[1:]:
        here = [lookup[i] for i in comp if i in lookup]
        gap, near = None, None
        for a in here:
            for b in nodes:
                # A repair target has to be a walkway junction. Telling someone to join
                # a stub to a bus stop would be advice that cannot be followed.
                if b["id"] not in main or b["kind"] != "waypoint":
                    continue
                d = metres(a["lat"], a["lng"], b["lat"], b["lng"])
                if gap is None or d < gap:
                    gap, near = d, b
        seg_gap, seg_on = None, ""
        for a in here:
            for lat1, lng1, lat2, lng2, eu, ev in main_segments:
                sa, sb = (lat1, lng1), (lat2, lng2)
                d = segment_metres(a["lat"], a["lng"], sa, sb)
                if seg_gap is None or d < seg_gap:
                    seg_gap, seg_on = d, f"{eu}-{ev}"
        fragments.append({
            "segment_m": round(seg_gap, 2) if seg_gap is not None else None,
            "segment_on": seg_on,
            "ids": list(comp),
            "names": [n["name"] for n in here if n.get("name")],
            "lat": sum(n["lat"] for n in here) / len(here),
            "lng": sum(n["lng"] for n in here) / len(here),
            "metres": round(gap, 1) if gap is not None else None,
            "join": near["id"] if near else "",
            "join_at": [near["lat"], near["lng"]] if near else None,
        })
    fragments.sort(key=lambda f: f["metres"] if f["metres"] is not None else 1e9)

    data = {
        "sources": sources,
        "built": time.strftime("%d %b %Y %H:%M:%S"),
        "stats": stat_html,
        "legend": legend_html,
        "nodes": [
            {
                "id": n["id"], "name": n.get("name", ""), "kind": n["kind"],
                "lat": n["lat"], "lng": n["lng"], "floor": n.get("floor", ""),
                "parent": n.get("parent", ""), "near": nearest_line.get(n["id"]),
                "places": n.get("places", []),
            }
            for n in nodes
        ],
        "edges": [
            {"u": e["u"], "v": e["v"], "metres": e["metres"],
             "stairs": e.get("stairs", False), "path": e.get("path") or [],
             "orphan": e["u"] not in main or e["v"] not in main}
            for e in edges
        ],
        "fragments": fragments,
        "shapes": shapes,
    }

    html = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False))

    summary = [
        sources,
        f"{len(places)} places, {junction_count} junctions, {len(edges)} links, "
        f"{len(comps)} network(s)",
        f"network sizes: {[len(c) for c in comps][:8]}",
        f"{len(isolated)} place(s) are on no path at all (listed in the page)",
        f"{len(joined)} place(s) share a coordinate with a drawn vertex and are joined there",
        f"{inserted} junction(s) were inserted where a place meets a line, splitting it there",
        f"{len(shapes)} structure(s) drawn as shapes only: no polygon becomes a node",
        "the routable flag on a structure is ignored: nothing is routed to a polygon",
        (f"{len(invented)} node(s) are NOT from your two files" if invented
         else "every node traces back to a point or a line vertex in the two files"),
    ]
    summary.append("no welding, no snapping, no tolerance: the network is exactly what you drew")
    if len(comps) > 1:
        summary.append(f"{len(comps) - 1} stray fragment(s) - drawn dashed red in the page:")
        for comp in comps[1:]:
            inside = set(comp)
            here = [n for n in nodes if n["id"] in inside]
            where = "; ".join(f'{n["lat"]:.6f},{n["lng"]:.6f}' for n in here[:4])
            named = [n.get("name") for n in here if n.get("name")]
            tag = f' [{", ".join(named[:2])}]' if named else ""
            summary.append(f"    {len(here)} junction(s){tag} at {where}")
    if off_main:
        summary.append(f"{len(off_main)} place(s) reached only through a floating stub:")
        for n in off_main[:10]:
            summary.append(f"    {n.get('name') or n['id']}")
    else:
        summary.append("every place is either on the network or has no link to it")
    return html, summary


def input_stamp(paths: tuple[Path | None, ...]) -> str:
    """Cheap identity for a set of files: it changes the moment one is saved."""
    parts = []
    for path in paths:
        if path is None:
            continue
        try:
            info = path.stat()
        except OSError:
            parts.append(f"{path.name}:-")
            continue
        parts.append(f"{path.name}:{info.st_mtime_ns}:{info.st_size}")
    return "|".join(parts)


def write_page(html: str, stamp: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "index.html"
    out.write_text(html.replace("__STAMP__", stamp), encoding="utf-8")
    return out


def serve(inputs: tuple[Path | None, ...], port: int) -> int:
    """Serve the page and rebuild it whenever one of the input files changes.

    A page opened from disk cannot do this job: the browser refuses to read a
    .geojson over file://, and nothing can tell a static file that you saved. So
    something has to both watch the files and hand out the HTML. That is all this
    does -- it holds one cached build and asks the file timestamps whether it is
    still current.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    cache: dict[str, str | None] = {"stamp": None, "html": ""}

    def current_html() -> str:
        key = input_stamp(inputs)
        if cache["stamp"] != key:
            started = time.monotonic()
            html, summary = render(*inputs)
            cache["stamp"] = key
            cache["html"] = html.replace("__STAMP__", key)
            print(f"BUILT in {time.monotonic() - started:.2f} s")
            for line in summary:
                print("  " + line)
        return str(cache["html"])

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 so each request closes: keep-alive only bought a trail of
        # ConnectionResetError tracebacks when the tab navigated away mid-poll.
        protocol_version = "HTTP/1.0"

        def log_message(self, *args) -> None:
            pass  # the tab polls every second; a line each time would bury the report

        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            # self.path carries the query string, and the page reloads through a
            # cache-busting "?t=..." -- comparing it raw made every reload a 404 and
            # made the rebuild button look broken. Route on the bare path.
            route = self.path.split("?", 1)[0]
            if route.startswith("/rebuild"):
                cache["stamp"] = None  # forget the cache, the next "/" rebuilds
                self._send(200, b'{"ok": true}', "application/json")
                return
            if route.startswith("/status"):
                body = json.dumps({"stamp": input_stamp(inputs)}).encode("utf-8")
                self._send(200, body, "application/json")
                return
            if route not in ("/", "/index.html"):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                page = current_html()
            except Exception as exc:  # a half-saved geojson must not kill the server
                cache["stamp"] = None
                print(f"BUILD FAILED: {exc}")
                note = (
                    "<h1 style='font-family:system-ui'>The files do not build</h1>"
                    f"<pre style='color:#b3261e;white-space:pre-wrap'>"
                    f"{html_lib.escape(str(exc))}</pre>"
                    "<p style='font-family:system-ui'>Fix the .geojson and save again: "
                    "the page reloads itself.</p>"
                )
                self._send(500, note.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    for offset in range(6):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port + offset), Handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"ports {port} to {port + 5} are all busy")

    host, actual = server.server_address[0], server.server_address[1]
    print(f"LIVE  http://{host}:{actual}/")
    print("  watching (save any of these and the page reloads itself):")
    for path in inputs:
        if path is None:
            continue
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"    {shown}")
    print("  ctrl-c stops the server")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def watch(inputs: tuple[Path | None, ...]) -> int:
    """No server: rewrite index.html whenever a file changes. Refresh by hand."""
    last: str | None = None
    print("watching for saves; ctrl-c stops. refresh the browser after each build.")
    try:
        while True:
            key = input_stamp(inputs)
            if key != last:
                try:
                    html, summary = render(*inputs)
                    out = write_page(html, key)
                    last = key
                    print(f"WROTE {out.relative_to(ROOT)}  "
                          f"({out.stat().st_size // 1024} KB)")
                    for line in summary:
                        print("  " + line)
                except Exception as exc:
                    print(f"BUILD FAILED, the old page is untouched: {exc}")
            time.sleep(0.7)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HTML preview of nodes + paths")
    parser.add_argument("--pois", default="data/authored/campus_pois1.geojson")
    parser.add_argument("--paths", default="data/authored/campus_paths1.geojson")
    parser.add_argument("--serve", action="store_true",
                        help="serve the page and rebuild it on every save (recommended)")
    parser.add_argument("--watch", action="store_true",
                        help="rewrite the html file on every save instead of serving it")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--buildings", default="data/authored/campus_buildings.geojson",
                        help="structures to DRAW only; no polygon ever becomes a node")
    # No --repair and no tolerance of any kind: a preview that quietly joins loose ends
    # within 3 m is a preview of a different network than the one you drew.
    args = parser.parse_args(argv)

    # A server that only shows its build log when you stop it is no use.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    inputs = (ROOT / args.pois, ROOT / args.paths, ROOT / args.buildings)
    if args.serve:
        return serve(inputs, args.port)
    if args.watch:
        return watch(inputs)

    html, summary = render(*inputs)
    out = write_page(html, input_stamp(inputs))
    print(f"WROTE {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")
    for line in summary:
        print("  " + line)
    print("  open it: start .tmp\\preview\\index.html")
    print("  for live updates run: python tools\\preview.py --serve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
