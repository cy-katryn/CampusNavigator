/* PolyU Campus Navigator - browser client.
 *
 * Four jobs, in this order of importance:
 *   1. talk to the Python engine (/api/chat) and show what it said
 *   2. draw the campus and the computed route as SVG (no map library, no tiles,
 *      no network -- so the demo still works offline)
 *   3. speak the reply and animate the avatar while it speaks (text to speech)
 *   4. listen to the microphone and type what it heard (speech to text)
 *
 * Everything here is a *view*: the routing, the facts and the honesty live on the
 * server. If this file were deleted the application would still be correct, just
 * harder to look at.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const SVG_NS = 'http://www.w3.org/2000/svg';
const METRES_PER_DEG_LAT = 111320;

const state = {
  graph: null,
  nodes: new Map(),
  view: { x: 0, y: 0, w: 800, h: 600 },
  bounds: null,
  speaking: false,
  listening: false,
  voiceOn: true,
  voice: null,
  basemap: true,            // street tiles underneath (off = plain diagram)
  showLinks: false,         // the walkway network is scaffolding, hidden by default
  showDoors: false,         // 24 doorways is a lot of markers; off until asked for
  // Facility pins start switched OFF. The map opens as a campus map -- footprints and
  // their letters -- and the six chips bring the pins back. They are still every one of
  // them clickable; they are simply not poured over the buildings by default.
  hidden: new Set(['recycle', 'water', 'toilet', 'aed', 'food', 'service']),
  serviceGroups: [],        // every facility pin, for filtering
};

/* ------------------------------------------------------------------ avatar */
/* The face is one inline SVG in index.html. A "state" is nothing but a class
   name on it -- the .avatar.* rules in styles.css decide what that looks like.
   This function is the ONLY place allowed to change it, and the five states are
   mutually exclusive:

     idle        nothing is in flight
     listening   the recogniser is running          (recognition.onstart)
     thinking    a /api/chat request is open        (send(), before the await)
     speaking    the utterance is playing           (utterance.onstart)
     error       the engine could not be reached    (send(), catch) - self-heals
*/
const avatar = $('avatar');
const avatarState = $('avatar-state');
const AVATAR_STATES = ['idle', 'listening', 'thinking', 'speaking', 'error'];
const AVATAR_LABELS = {
  idle: 'idle',
  listening: 'listening…',
  thinking: 'thinking…',
  speaking: 'speaking…',
  error: 'no connection',
};
let avatarRecover = null;

function setAvatar(mode) {
  const next = AVATAR_STATES.indexOf(mode) === -1 ? 'idle' : mode;
  clearTimeout(avatarRecover);
  AVATAR_STATES.forEach((name) => avatar.classList.remove(name));
  avatar.classList.add(next);
  avatarState.textContent = AVATAR_LABELS[next];
  if (next === 'error') {
    // a dropped connection is a flash, not a home: come back on our own
    avatarRecover = setTimeout(() => setAvatar(state.speaking ? 'speaking' : 'idle'), 2600);
  }
}

/* -------------------------------------------------------------------- chat */
function addMessage(role, payload) {
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + role;
  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = role === 'user' ? 'you' : 'navigator';
  wrap.appendChild(who);

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (role === 'bot' && payload) {
    const readable = document.createElement('span');
    // keep the engineer-readable part (segment tables, search stats) visually quieter
    payload.text.split('\n').forEach((line, i) => {
      const span = document.createElement('span');
      if (/^\s*[-…]/.test(line) || /^(Search|Total|Route:)/.test(line) || !line.trim()) {
        span.className = 'seg';
      }
      span.textContent = (i ? '\n' : '') + line;
      readable.appendChild(span);
    });

    if (payload.lead) {
      // Lead with the sentence the bot would speak; keep the calculation one click away.
      const lead = document.createElement('div');
      lead.className = 'lead';
      lead.textContent = payload.lead;
      bubble.appendChild(lead);

      const details = document.createElement('details');
      const summary = document.createElement('summary');
      const route = payload.route;
      const bits = [];
      if (route && route.reachable) {
        bits.push(route.segments.length + ' legs');
        if (route.stats) bits.push(route.stats.algorithm.toUpperCase() + ' expanded ' +
          route.stats.nodes_expanded + ' nodes, ' + route.stats.ms + ' ms');
      }
      if ((payload.facilities || []).length > 1) bits.push(payload.facilities.length + ' places');
      summary.textContent = bits.length ? bits.join(' · ') : 'details';
      details.appendChild(summary);
      details.appendChild(readable);
      bubble.appendChild(details);
    } else {
      bubble.appendChild(readable);
    }

    const tags = document.createElement('div');
    tags.className = 'tags';
    if (payload.narrated) tags.innerHTML += '<span class="tag llm">prose: model · numbers: A*</span>';
    if (payload.parser) {
      tags.innerHTML += '<span class="tag-parser">understood by ' + escapeHtml(payload.parser) + '</span>';
    }
    (payload.notes || []).forEach((n) => {
      tags.innerHTML += '<span class="tag">' + escapeHtml(n) + '</span>';
    });
    if (tags.children.length) bubble.appendChild(tags);

    if ((payload.facilities || []).length) {
      const cards = document.createElement('div');
      cards.className = 'cards';
      payload.facilities.forEach((f) => {
        const card = document.createElement('div');
        card.className = 'card';
        const where = [f.building, f.floor].filter(Boolean).join(', ');
        const bits = [];
        if (where) bits.push(where);
        if (f.metres != null) bits.push(f.metres + ' m');
        if (f.hours_today) bits.push((f.open_now === false ? 'closed · ' : 'open · ') + f.hours_today);
        card.innerHTML = '<b>' + escapeHtml(f.name) + '</b>' +
          (bits.length ? '<div class="meta">' + escapeHtml(bits.join(' · ')) + '</div>' : '');
        card.title = 'Ask for a route to ' + f.name;
        card.addEventListener('click', () => send('how do I get to ' + f.name + '?'));
        cards.appendChild(card);
      });
      bubble.appendChild(cards);
    }
  } else {
    bubble.textContent = payload;
  }

  wrap.appendChild(bubble);
  $('chat').appendChild(wrap);
  $('chat').scrollTop = $('chat').scrollHeight;
  return wrap;
}

function escapeHtml(value) {
  const div = document.createElement('span');
  div.textContent = String(value == null ? '' : value);
  return div.innerHTML;
}

async function send(text) {
  text = (text || '').trim();
  if (!text) return;
  addMessage('user', text);
  $('input').value = '';
  setAvatar('thinking');
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const payload = await res.json();
    if (payload.error) {
      addMessage('bot', { text: 'Server error: ' + payload.error, notes: [], facilities: [] });
      setAvatar('idle');
      return;
    }
    addMessage('bot', payload);
    drawRoute(payload.route);
    drawFacilities(payload.facilities);
    speak(payload.speech);
  } catch (err) {
    addMessage('bot', { text: 'Could not reach the engine: ' + err, notes: [], facilities: [] });
    // the one thing the face knows that the text does not: the engine is gone
    setAvatar('error');
  }
}

/* --------------------------------------------------------------- projection */
function setupProjection(nodes) {
  const lats = nodes.map((n) => n.lat);
  const lngs = nodes.map((n) => n.lng);
  const minLat = Math.min(...lats) - 0.0006;
  const maxLat = Math.max(...lats) + 0.0006;
  const minLng = Math.min(...lngs) - 0.0006;
  const maxLng = Math.max(...lngs) + 0.0006;
  const cos = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
  state.bounds = { minLat, maxLat, minLng, maxLng, cos };
  const w = (maxLng - minLng) * METRES_PER_DEG_LAT * cos;
  const h = (maxLat - minLat) * METRES_PER_DEG_LAT;
  state.view = { x: 0, y: 0, w, h };
  applyView();
}

function project(lat, lng) {
  const b = state.bounds;
  return {
    x: (lng - b.minLng) * METRES_PER_DEG_LAT * b.cos,
    y: (b.maxLat - lat) * METRES_PER_DEG_LAT,
  };
}

function applyView() {
  const v = state.view;
  $('map').setAttribute('viewBox', v.x + ' ' + v.y + ' ' + v.w + ' ' + v.h);
  // Names are expensive: the campus has 51 buildings and 24 doorways, and showing
  // every one of them turns the map into a wall of text with the route lost inside it.
  // So a label appears only when there is room for it AND it is the kind of place worth
  // naming at that zoom. Route labels are exempt: the start and the goal must be
  // readable at any zoom.
  const roofsAreLabeled = $('map').classList.contains('footprints');
  document.querySelectorAll('.node-label:not(.route-label)').forEach((label) => {
    const wanted = label.classList.contains('building-label')
      ? (v.w < 1400 && !roofsAreLabeled)
      : label.classList.contains('always');
    label.style.display = wanted ? '' : 'none';
  });
  updateScale();
  drawTiles();
}

function zoom(factor, cx, cy) {
  const v = state.view;
  const nw = Math.min(Math.max(v.w * factor, 120), (state.bounds.maxLng - state.bounds.minLng) * METRES_PER_DEG_LAT * state.bounds.cos * 3);
  const nh = nw * (v.h / v.w);
  const px = cx == null ? v.x + v.w / 2 : cx;
  const py = cy == null ? v.y + v.h / 2 : cy;
  v.x = px - (px - v.x) * (nw / v.w);
  v.y = py - (py - v.y) * (nh / v.h);
  v.w = nw;
  v.h = nh;
  applyView();
}

/* --------------------------------------------------------------------- map */
/* Which legend colour a facility belongs to. Derived from the service label the
   engine already sends, so adding a facility type on the Python side still lands
   in one of five known buckets rather than silently becoming unstyled. */
function categoryOf(node) {
  const s = (node.service || '').toLowerCase();
  if (s.includes('toilet')) return 'toilet';
  if (s.includes('water')) return 'water';
  if (s === 'aed') return 'aed';
  if (s.includes('recycl') || s.includes('unire') || s.includes('battery')
      || s.includes('glass') || s.includes('weee') || s.includes('vending')
      || s.includes('food waste')) return 'recycle';
  if (s.includes('canteen')) return 'food';
  return 'service';                       // clinic, bank, bookstore, postbox, ...
}

/* PolyU labels its buildings with letters, not names: Core A, Block L, Block VA.
   Pull the letter out of the official name so the block can carry it, the way the
   official campus map does. */
/* Ray casting on a [lat, lng] ring: is this point inside that polygon? */
function pointInRing(lat, lng, ring) {
  let hit = false;
  for (let i = 0; i < ring.length - 1; i += 1) {
    const [y1, x1] = ring[i];
    const [y2, x2] = ring[i + 1];
    if ((y1 > lat) !== (y2 > lat)) {
      if (lng < x1 + ((lat - y1) * (x2 - x1)) / (y2 - y1)) hit = !hit;
    }
  }
  return hit;
}

function shortCode(name) {
  const core = name.match(/^(?:Core|Block|Wing)\s+([A-Za-z0-9]+)/i);
  if (core) return core[1].toUpperCase();
  const alnum = name.replace(/[^A-Za-z0-9]/g, '');
  return alnum.slice(0, 2).toUpperCase() || '·';
}

/* Cores A-H, J-N, P-T, U-Z form four readable bands on campus -- the same grouping
   the lettering already implies, used here as a colour. */
function zoneOf(code) {
  const letter = (code || 'A')[0].toUpperCase();
  if (letter <= 'H') return 1;
  if (letter <= 'N') return 2;
  if (letter <= 'T') return 3;
  return 4;
}

const ALL_CATS = ['recycle', 'water', 'toilet', 'aed', 'food', 'service'];

function syncLayerChips() {
  document.querySelectorAll('#legend-cats .legend-chip').forEach((chip) => {
    chip.classList.toggle('off', state.hidden.has(chip.dataset.cat));
  });
  const links = $('links-toggle');
  if (links) links.classList.toggle('off', !state.showLinks);
  const doors = $('doors-toggle');
  if (doors) doors.classList.toggle('off', !state.showDoors);

}

function applyFilters() {
  state.serviceGroups.forEach(({ el: group, cat }) => {
    group.classList.toggle('dim', state.hidden.has(cat));
  });
}

/* A scale bar turned from real metres: state.view is metres wide, so the number of
   screen pixels per metre is just the panel width divided by the view width. */
function updateScale() {
  const bar = $('map-scale');
  const svg = $('map');
  if (!bar || !svg || !state.bounds) return;
  const rect = svg.getBoundingClientRect();
  if (!rect.width) return;
  const metresPerPx = state.view.w / rect.width;
  const target = metresPerPx * 72;
  const pow = Math.pow(10, Math.floor(Math.log10(target)));
  const nice = [1, 2, 5, 10].map((m) => m * pow).find((v) => v >= target) || target;
  bar.querySelector('b').style.width = (nice / metresPerPx).toFixed(1) + 'px';
  bar.querySelector('span').textContent =
    nice >= 1000 ? (nice / 1000).toFixed(nice % 1000 ? 1 : 0) + ' km' : Math.round(nice) + ' m';
}

/* ---------------------------------------------------------------- basemap tiles
   A real street map underneath, so the campus sits on roads and blocks instead of a
   blank page. OpenStreetMap tiles are free and only need attribution; Google's are
   neither free nor cacheable, which is why PolyU's own page cannot be reused either.
   Nothing is bundled: the browser fetches the few tiles the view needs, and if it is
   offline the diagram underneath still works. */
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const MAX_TILES = 42;

function unproject(x, y) {
  const b = state.bounds;
  return {
    lng: b.minLng + x / (METRES_PER_DEG_LAT * b.cos),
    lat: b.maxLat - y / METRES_PER_DEG_LAT,
  };
}

function tileXOf(lng, z) { return (lng + 180) / 360 * 2 ** z; }
function tileYOf(lat, z) {
  const rad = lat * Math.PI / 180;
  return (1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2 * 2 ** z;
}
function latAtTileY(y, z) {
  return Math.atan(Math.sinh(Math.PI * (1 - 2 * y / 2 ** z))) * 180 / Math.PI;
}
function lngAtTileX(x, z) { return x / 2 ** z * 360 - 180; }

/* Pick the zoom whose tiles are about one screen pixel each. */
function tileZoom() {
  const rect = $('map').getBoundingClientRect();
  if (!rect.width || !state.bounds) return 17;
  const metresPerPixel = state.view.w / rect.width;
  const lat = (state.bounds.minLat + state.bounds.maxLat) / 2;
  const worldMetres = 156543.03392 * Math.cos(lat * Math.PI / 180);
  return Math.max(15, Math.min(19, Math.round(Math.log2(worldMetres / metresPerPixel))));
}

function drawTiles() {
  const layer = $('layer-tiles');
  if (!layer) return;
  layer.innerHTML = '';
  if (!state.basemap || !state.bounds) return;

  const z = tileZoom();
  const nw = unproject(state.view.x, state.view.y);
  const se = unproject(state.view.x + state.view.w, state.view.y + state.view.h);
  const x0 = Math.floor(tileXOf(nw.lng, z));
  const x1 = Math.floor(tileXOf(se.lng, z));
  const y0 = Math.floor(tileYOf(nw.lat, z));
  const y1 = Math.floor(tileYOf(se.lat, z));
  if ((x1 - x0 + 1) * (y1 - y0 + 1) > MAX_TILES) return;   // too far out: skip

  for (let x = x0; x <= x1; x += 1) {
    for (let y = y0; y <= y1; y += 1) {
      const top = latAtTileY(y, z);
      const bottom = latAtTileY(y + 1, z);
      const left = lngAtTileX(x, z);
      const right = lngAtTileX(x + 1, z);
      const a = project(top, left);
      const b = project(bottom, right);
      const image = el('image', {
        href: TILE_URL.replace('{z}', z).replace('{x}', x).replace('{y}', y),
        x: a.x, y: a.y, width: b.x - a.x, height: b.y - a.y,
        preserveAspectRatio: 'none', class: 'tile',
      });
      layer.appendChild(image);
    }
  }
}

/* --------------------------------------------------------- 2.5D building blocks
   How an illustrated campus map is made, in one paragraph: take each building's real
   footprint polygon, copy it straight up the screen by a height, and fill the ribbon
   between the two copies -- that ribbon is the wall, the lifted copy is the roof.
   Draw every wall first, then the roofs, and sort the buildings north to south so the
   ones nearest the viewer cover the ones behind. No textures, no 3D engine. */
function drawFootprints() {
  const layer = $('layer-footprints');
  if (!layer) return;
  layer.innerHTML = '';
  let rings = (state.footprints || []).slice();
  const active = rings.length > 0;          // draw over the basemap too
  $('map').classList.toggle('footprints', active);
  if (!active) return;

  const maxHeight = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue('--fp-height')
  ) || 20;                                            // metres, from the palette block

  // drawn paths first, so blocks sit on top of them
  rings.filter((r) => r.line).forEach((item) => {
    const pts = item.ring.map(([lat, lng]) => project(lat, lng));
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
    layer.appendChild(el('path', { d, class: 'fp-path fp-path-' + (item.kind || 'path') }));
  });
  rings = rings.filter((r) => !r.line);

  const buildings = [...state.nodes.values()].filter((n) => n.kind === 'building');

  const blocks = rings.map((item) => {
    const coords = item.ring.map(([lat, lng]) => project(lat, lng));
    const north = Math.max(...item.ring.map((p) => p[0]));
    // which campus zone does this footprint belong to? nearest lettered building
    let zone = 1;
    let owner = null;
    let ownerDistance = Infinity;
    const centre = item.ring.reduce((a, p) => [a[0] + p[0] / item.ring.length,
                                               a[1] + p[1] / item.ring.length], [0, 0]);
    buildings.forEach((b) => {
      // deg^2 is fine for "nearest": ~110 km/deg, so 1 deg^2 ≈ 1.2e10 m^2
      const d = (b.lat - centre[0]) ** 2 + (b.lng - centre[1]) ** 2;
      if (d < ownerDistance) { ownerDistance = d; owner = b; }
    });
    if (owner) {
      zone = zoneOf(item.display || shortCode(owner.name));
      ownerDistance = Math.sqrt(ownerDistance) * 111320;   // metres
    }
    // Which letter belongs on this roof? The one the data gives: "display" if present,
    // else the block code in "ref". Nothing else. Deriving a code from the name is what
    // put "LE" on the Lee Shau Kee Building (whose block is Y) and "JO" on the Innovation
    // Tower (whose block is V), and it invented codes for the eleven unnamed slabs too.
    const badge = item.display || '';
    // a hand-drawn shape may carry its own height; otherwise vary it deterministically
    // by id (OSM rarely tags height): 55-100% of the palette maximum
    const height = item.height || maxHeight * (0.55 + hash01(item.id, 3) * 0.45);
    // "tone": "white" in the file is the one way a block may differ from the rest.
    const tone = item.tone ? ' tone-' + item.tone : '';
    return { coords, north, height, zone, owner, ownerDistance, tone, badge,
             kind: item.kind || 'building' };
  }).sort((a, b) => b.north - a.north);               // north first, south last on top

  blocks.forEach((block) => {
    const { coords, height } = block;

    // a flat feature (pool, lawn, court) is painted once, with no walls and no
    // height -- there is nothing to extrude, and faking one would be a lie
    if (block.kind && block.kind !== 'building') {
      const flat = coords.map((p) => p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(' ');
      layer.appendChild(el('polygon', { points: flat, class: 'fp-flat fp-flat-' + block.kind }));
      return;
    }

    // 1. a soft shadow on the ground, offset down-right: grounds the block
    const shadow = coords.map((p) => (p.x + 5).toFixed(1) + ',' + (p.y + 5).toFixed(1)).join(' ');
    layer.appendChild(el('polygon', { points: shadow, class: 'fp-shadow' }));

    // 2. the walls
    for (let i = 0; i < coords.length; i += 1) {
      const p1 = coords[i];
      const p2 = coords[(i + 1) % coords.length];
      const d = 'M' + p1.x.toFixed(1) + ' ' + p1.y.toFixed(1)
        + 'L' + p2.x.toFixed(1) + ' ' + p2.y.toFixed(1)
        + 'L' + p2.x.toFixed(1) + ' ' + (p2.y - height).toFixed(1)
        + 'L' + p1.x.toFixed(1) + ' ' + (p1.y - height).toFixed(1) + 'Z';
      const facing = p2.x < p1.x ? 'fp-wall-dark' : 'fp-wall';
      layer.appendChild(el('path', { d, class: facing + block.tone }));
    }
    const roof = coords.map((p) => p.x.toFixed(1) + ',' + (p.y - height).toFixed(1)).join(' ');
    layer.appendChild(el('polygon', { points: roof, class: 'fp-roof' + block.tone }));

    // 3. a parapet: the roof inset towards its own centre, so the outer ring reads as
    //    a raised edge instead of a flat card
    const cx = coords.reduce((a, p) => a + p.x, 0) / coords.length;
    const cy = coords.reduce((a, p) => a + p.y, 0) / coords.length;
    const inner = coords
      .map((p) => (cx + (p.x - cx) * 0.82).toFixed(1) + ',' + (cy + (p.y - cy) * 0.82 - height).toFixed(1))
      .join(' ');
    layer.appendChild(el('polygon', { points: inner, class: 'fp-roof-inner' + block.tone }));

    // 4. the building's letter, on the roof it actually belongs to. The campus-map
    //    pins are block-level anchors (up to ~50 m off the real footprint), so the
    //    badge is placed at the block's centre rather than the node's position.
    if (block.badge) {
      const code = el('text', {
        x: cx.toFixed(1), y: (cy - height + 4).toFixed(1), class: 'roof-code',
      });
      code.textContent = block.badge;
      layer.appendChild(code);
    }
  });
}

/* The shape of one link: its real walkway geometry when the graph carries it,
   otherwise the straight line between the two places we do know. */
/* Deterministic pseudo-random in [0,1) from a string -- so the decorative trees
   land in the same place on every reload instead of shimmering. */
function hash01(text, salt) {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 1000) / 1000;
}

function edgeShape(edge, a, b) {
  const points = (edge.path && edge.path.length >= 2)
    ? edge.path.map((p) => project(p[0], p[1]))
    : [project(a.lat, a.lng), project(b.lat, b.lng)];
  return points.map((p, i) => (i ? 'L' : 'M') + p.x.toFixed(2) + ' ' + p.y.toFixed(2)).join(' ');
}

function el(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function drawGraph() {
  const { nodes, edges } = state.graph;
  state.nodes = new Map(nodes.map((n) => [n.id, n]));
  setupProjection(nodes);

  const edgeLayer = $('layer-edges');
  edgeLayer.innerHTML = '';
  edges.forEach((e) => {
    const a = state.nodes.get(e.u);
    const b = state.nodes.get(e.v);
    if (!a || !b) return;
    const d = edgeShape(e, a, b);
    edgeLayer.appendChild(el('path', { d, class: 'edge-casing' }));
    const line = el('path', { d, class: 'edge' });
    const tip = el('title', {});
    tip.textContent = e.id + ' · ' + Math.round(e.metres) + ' m'
      + (e.stairs ? ' · steps' : '') + (e.covered ? ' · covered' : '');
    line.appendChild(tip);
    edgeLayer.appendChild(line);
    if (e.stairs) drawStairs(edgeLayer, e, a, b);
  });

  const nodeLayer = $('layer-nodes');
  nodeLayer.innerHTML = '';
  state.serviceGroups = [];
  nodes.forEach((n) => {
    // a waypoint is a junction in the walkway network, not a place: it is the end
    // point of a link, never something to draw or offer as a destination
    if (n.kind === 'waypoint') return;
    // doorways are off until asked for
    if (n.kind === 'entrance' && !state.showDoors) return;
    const p = project(n.lat, n.lng);
    const g = el('g', { class: 'node ' + n.kind });
    if (n.kind === 'service') {
      const cat = categoryOf(n);
      g.classList.add('cat-' + cat);
      state.serviceGroups.push({ el: g, cat });
      g.appendChild(el('circle', { cx: p.x, cy: p.y, r: 7 }));
    } else if (n.kind === 'place') {
      // a square of its own, filed under the "clinic · other" chip so that chip means
      // "every destination that is not a facility", and the default stays clean
      g.classList.add('cat-service');
      state.serviceGroups.push({ el: g, cat: 'service' });
      g.appendChild(el('rect', { x: p.x - 7, y: p.y - 7, width: 14, height: 14, rx: 3 }));
    } else {
      const size = n.kind === 'entrance' ? 13 : (n.kind === 'place' ? 14 : 18);
      // a shadowed face under the block: the cheapest honest nod to 2.5D, since a
      // shadow is decoration and claims nothing about the building's real height
      if (n.kind !== 'entrance' && n.kind !== 'place') {
        g.appendChild(el('rect', {
          x: p.x - size / 2 + 3.5, y: p.y - size / 2 + 3.5,
          width: size, height: size, rx: 3, class: 'node-shadow',
        }));
        for (let i = 0; i < 3; i += 1) {
          const angle = hash01(n.id, i) * Math.PI * 2;
          const radius = 13 + hash01(n.id, i + 10) * 11;
          nodeLayer.appendChild(el('circle', {
            cx: p.x + Math.cos(angle) * radius,
            cy: p.y + Math.sin(angle) * radius,
            r: 2 + hash01(n.id, i + 20) * 1.6,
            class: 'tree',
          }));
        }
      }
      g.appendChild(el('rect', {
        x: p.x - size / 2, y: p.y - size / 2, width: size, height: size, rx: 3,
      }));
      if (n.kind === 'building') {
        const code = el('text', { x: p.x, y: p.y + 3.1, class: 'node-code' });
        code.textContent = shortCode(n.name);
        g.appendChild(code);
      }
    }
    const tip = el('title', {});
    tip.textContent = n.name + (n.service ? ' · ' + n.service : '');
    g.appendChild(tip);

    if (n.kind === 'building') {
      const label = el('text', {
        x: p.x + 11, y: p.y + 4,
        class: 'node-label building-label',
      });
      label.textContent = n.name.length > 26 ? n.name.slice(0, 25) + '…' : n.name;
      label.style.display = 'none';
      nodeLayer.appendChild(label);
    }

    g.addEventListener('click', (ev) => {
      ev.stopPropagation();
      $('input').value = 'how do I get to ' + n.name + '?';
      $('input').focus();
    });
    g.addEventListener('mouseenter', () => showTip(n.name + (n.floor ? ' · ' + n.floor : '')));
    g.addEventListener('mouseleave', hideTip);
    nodeLayer.appendChild(g);
  });
  // Every redraw rebuilds the facility groups, so the category filter has to be
  // re-applied here. Without this, toggling any layer called drawGraph() and every
  // facility came back, ignoring the chips -- which is why switching doorways on
  // seemed to switch everything on.
  applyFilters();
  drawFootprints();
  applyView();
}

function showTip(text) {
  $('map-tip').textContent = text;
  $('map-tip').classList.add('show');
}
function hideTip() { $('map-tip').classList.remove('show'); }

function drawRoute(route) {
  const layer = $('layer-route');
  const badge = $('route-badge');
  layer.innerHTML = '';
  if (badge) badge.hidden = true;
  const navigating = Boolean(route && route.path && route.path.length >= 2);
  // Navigating shows the route and nothing else: revealing all 370 walkway links as
  // well would bury the one line the user actually has to follow. The network stays
  // on its own chip, so it is still there if it is ever wanted.
  $('map').classList.toggle('no-links', !state.showLinks);
  if (!navigating) return;

  if (badge) {
    badge.hidden = false;
    badge.textContent = route.reachable
      ? Math.round(route.metres) + ' m · ' + route.segments.length + ' legs'
      : 'no route with those closures';
    badge.classList.toggle('failed', !route.reachable);
  }
  const shaped = [];
  (route.segments || []).forEach((seg) => {
    if (seg.path && seg.path.length >= 2) {
      seg.path.forEach(([lat, lng]) => shaped.push(project(lat, lng)));
    }
  });
  const points = route.path.map((p) => project(p.lat, p.lng));
  const drawPoints = shaped.length >= 2 ? shaped : points;
  const d = drawPoints.map((p, i) => (i ? 'L' : 'M') + p.x.toFixed(2) + ' ' + p.y.toFixed(2)).join(' ');
  layer.appendChild(el('path', { d, class: 'route-casing' }));
  layer.appendChild(el('path', { d, class: 'route-line' }));

  route.path.forEach((p, i) => {
    const xy = points[i];
    layer.appendChild(el('circle', { cx: xy.x, cy: xy.y, r: i === 0 ? 9 : 6, class: 'route-dot' }));
    const pin = el('circle', { cx: xy.x, cy: xy.y, r: i === 0 ? 4 : 3, class: 'route-pin' });
    layer.appendChild(pin);
    if (i === 0 || i === route.path.length - 1) {
      const label = el('text', {
        x: xy.x + 12, y: xy.y - 10, class: 'node-label route-label',
      });
      label.textContent = (i === 0 ? 'START · ' : 'GOAL · ') + p.name;
      layer.appendChild(label);
    }
  });
  fitTo(points);
}

function drawFacilities(facilities) {
  const layer = $('layer-facilities');
  layer.innerHTML = '';
  (facilities || []).forEach((f) => {
    if (f.lat == null || f.lng == null) return;
    const p = project(f.lat, f.lng);
    layer.appendChild(el('circle', { cx: p.x, cy: p.y, r: 26, class: 'fac-pin pulse' }));
  });
}

function fitTo(points) {
  if (!points.length) return;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const pad = 70;
  const x = Math.min(...xs) - pad;
  const y = Math.min(...ys) - pad;
  const w = Math.max(Math.max(...xs) + pad - x, 200);
  const h = Math.max(Math.max(...ys) + pad - y, 160);
  state.view = { x, y, w, h };
  applyView();
}

/* ------------------------------------------------------- speech: synthesis */
function pickVoice() {
  const select = $('lang');
  const wanted = (select && select.value) || 'en-US';
  const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  return voices.find((v) => v.lang === wanted) ||
         voices.find((v) => v.lang && v.lang.slice(0, 2) === wanted.slice(0, 2)) ||
         voices.find((v) => /^en/i.test(v.lang)) || null;
}

function speak(text) {
  if (!state.voiceOn || !text || !window.speechSynthesis) {
    // nothing is going to be said -- do not leave the face stuck on "thinking"
    setAvatar(state.listening ? 'listening' : 'idle');
    return;
  }
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  const voice = pickVoice();
  if (voice) utter.voice = voice;
  utter.rate = 1.0;
  utter.pitch = 1.0;
  utter.onstart = () => { state.speaking = true; setAvatar('speaking'); };
  utter.onend = () => { state.speaking = false; setAvatar(state.listening ? 'listening' : 'idle'); };
  utter.onerror = () => { state.speaking = false; setAvatar('idle'); };
  window.speechSynthesis.speak(utter);
}

/* ---------------------------------------------------- speech: recognition */
function setupRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    $('mic').disabled = true;
    $('voice-hint').textContent =
      'Speech input needs a browser with the Web Speech API (Chrome or Edge). ' +
      'Text input and spoken replies still work.';
    return;
  }
  const rec = new Recognition();
  rec.lang = $('lang').value;
  rec.interimResults = true;
  rec.continuous = false;

  rec.onstart = () => {
    state.listening = true;
    $('mic').classList.add('recording');
    setAvatar('listening');
  };
  rec.onresult = (event) => {
    let interim = '';
    let final = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) final += chunk;
      else interim += chunk;
    }
    $('input').value = final || interim;
    if (final) {
      rec.stop();
      send(final);
    }
  };
  rec.onerror = (event) => {
    $('voice-hint').textContent = 'Microphone error: ' + event.error +
      (event.error === 'not-allowed' ? ' (allow microphone access in the browser)' : '');
  };
  rec.onend = () => {
    state.listening = false;
    $('mic').classList.remove('recording');
    if (!state.speaking) setAvatar('idle');
  };
  return rec;
}

/* ---------------------------------------------------------------- map search */
/* Type a place, arrow through the hits, Enter to route. Searches the same node list
   the map is drawn from, so anything visible on the map is findable here. */
function setupSearch() {
  const input = $('place-search');
  const box = $('search-results');
  if (!input || !box || !state.graph) return;

  const places = state.graph.nodes.filter((n) => n.kind !== 'waypoint');
  let hits = [];
  let active = -1;

  const close = () => { box.hidden = true; active = -1; };

  const choose = (index) => {
    const hit = hits[index];
    if (!hit) return;
    close();
    input.value = '';
    focusPlace(hit);
    send('how do I get to ' + hit.name + '?');
  };

  const render = () => {
    box.innerHTML = '';
    if (!hits.length) {
      const empty = document.createElement('div');
      empty.className = 'search-empty';
      empty.textContent = 'No place matches that name.';
      box.appendChild(empty);
      box.hidden = false;
      return;
    }
    hits.forEach((hit, index) => {
      const row = document.createElement('div');
      row.className = 'search-hit' + (index === active ? ' active' : '');
      const dot = document.createElement('i');
      if (hit.kind === 'service' && hit.service) dot.style.background = 'var(--cat-' + categoryOf(hit) + ')';
      const name = document.createElement('span');
      name.textContent = hit.name;
      const where = document.createElement('span');
      where.className = 'where';
      where.textContent = hit.kind === 'service' ? (hit.service || 'facility') : (hit.kind === 'entrance' ? 'entrance' : 'building');
      row.append(dot, name, where);
      row.addEventListener('mousedown', (ev) => { ev.preventDefault(); choose(index); });
      box.appendChild(row);
    });
    box.hidden = false;
  };

  const search = (query) => {
    const needle = query.trim().toLowerCase();
    if (needle.length < 2) { close(); return; }
    hits = places
      .filter((n) => n.name.toLowerCase().includes(needle)
        || (n.service || '').toLowerCase().includes(needle))
      .sort((a, b) => {
        const ai = a.name.toLowerCase().indexOf(needle);
        const bi = b.name.toLowerCase().indexOf(needle);
        return ai - bi || a.name.length - b.name.length;
      })
      .slice(0, 7);
    active = hits.length ? 0 : -1;
    render();
  };

  input.addEventListener('input', () => search(input.value));
  input.addEventListener('focus', () => { if (input.value.trim().length >= 2) search(input.value); });
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown' && hits.length) {
      active = (active + 1) % hits.length; render(); ev.preventDefault();
    } else if (ev.key === 'ArrowUp' && hits.length) {
      active = (active - 1 + hits.length) % hits.length; render(); ev.preventDefault();
    } else if (ev.key === 'Enter') {
      choose(active >= 0 ? active : 0); ev.preventDefault();
    } else if (ev.key === 'Escape') {
      close();
    }
  });
  document.addEventListener('click', (ev) => {
    if (!ev.target.closest('.map-search')) close();
  });
}

/* Pan and zoom so one place fills the view -- what a map app does when you pick
   a result from its search box. */
function focusPlace(node) {
  const p = project(node.lat, node.lng);
  const span = 420;
  state.view = { x: p.x - span / 2, y: p.y - span / 2, w: span, h: span * 0.72 };
  applyView();
}

/* ------------------------------------------------------------------- stairs
   A flight of steps is drawn as rungs instead of a plain line, because "walk 40 m
   and you will meet steps" is different advice from "walk 40 m". Rungs sit
   perpendicular to the link, one every few pixels along its length, so the shape
   survives zooming: the spacing is in screen pixels, not in metres. */
function drawStairs(layer, edge, a, b) {
  const points = (edge.path && edge.path.length >= 2)
    ? edge.path.map((p) => project(p[0], p[1]))
    : [project(a.lat, a.lng), project(b.lat, b.lng)];
  const spacing = 5.5;
  const half = 3.4;
  for (let i = 1; i < points.length; i += 1) {
    const p0 = points[i - 1];
    const p1 = points[i];
    const dx = p1.x - p0.x;
    const dy = p1.y - p0.y;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    for (let d = spacing / 2; d < len; d += spacing) {
      const x = p0.x + (dx * d) / len;
      const y = p0.y + (dy * d) / len;
      layer.appendChild(el('line', {
        x1: (x - nx * half).toFixed(1), y1: (y - ny * half).toFixed(1),
        x2: (x + nx * half).toFixed(1), y2: (y + ny * half).toFixed(1),
        class: 'edge-stairs',
      }));
    }
  }
}

/* -------------------------------------------------------------- model status */
function refreshStatus(status) {
  const pill = $('pill-mode');
  const connected = Boolean(status.model);
  pill.textContent = connected
    ? 'model: ' + status.model.split('/').pop().replace(':free', '')
    : 'no model connected';
  pill.className = 'pill ' + (connected ? 'ok' : 'warn');
  pill.title = connected
    ? status.mode + '\nkey: present'
    : status.mode + '\nUse the gear to add an OpenRouter key; every feature still works without one';

  const counts = $('info-counts');
  if (counts && status.knowledge) {
    counts.textContent =
      'Loaded: ' + status.nodes + ' places, ' + status.edges + ' links, ' +
      status.knowledge.documents + ' knowledge records (' +
      status.knowledge.with_opening_hours + ' with published hours).';
  }
}

function openSettings(status) {
  $('settings-modal').hidden = false;
  $('settings-status').textContent = status && status.api_key_present
    ? 'A key is already stored. Leave the field blank to keep it.'
    : 'No key stored yet — the app runs on the deterministic parser.';
  $('settings-status').className = 'modal-status';
  $('settings-key').value = '';
  $('settings-model').value = '';
  $('settings-key').focus();
}

async function saveSettings() {
  const key = $('settings-key').value.trim();
  const model = $('settings-model').value.trim();
  if (!key && !model) {
    $('settings-status').textContent = 'Nothing to save — both fields are empty.';
    $('settings-status').className = 'modal-status err';
    return;
  }
  $('settings-status').textContent = 'Saving…';
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key || null, model: model || null }),
    });
    const payload = await res.json();
    if (!res.ok || payload.error) {
      $('settings-status').textContent = payload.error || 'Could not save.';
      $('settings-status').className = 'modal-status err';
      return;
    }
    refreshStatus(payload.status);
    $('settings-status').textContent =
      'Saved to ' + payload.env + '. The model is ' +
      (payload.status.model ? 'connected: ' + payload.status.model : 'still not connected.');
    $('settings-status').className = 'modal-status ok';
    setTimeout(() => { $('settings-modal').hidden = true; }, 900);
  } catch (err) {
    $('settings-status').textContent = 'Could not reach the engine: ' + err;
    $('settings-status').className = 'modal-status err';
  }
}

/* ------------------------------------------------------------------- wiring */
async function boot() {
  // one pill: is a model connected, and which one?
  try {
    refreshStatus(await (await fetch('/api/status')).json());
  } catch (err) {
    $('pill-mode').textContent = 'engine offline';
    $('pill-mode').className = 'pill warn';
  }

  try {
    state.graph = await (await fetch('/api/graph')).json();
    state.footprints = state.graph.footprints || [];
    // the map opens the way the official campus map does: buildings and their
    // letters, nothing else. Facility layers are opt-in through the ◎ button.
    state.hidden = new Set(ALL_CATS);
    $('map').classList.toggle('basemap', state.basemap);
    $('map').classList.toggle('no-links', !state.showLinks);
    drawGraph();
    applyFilters();
    syncLayerChips();
  } catch (err) {
    $('pill-mode').title = 'the map could not be loaded';
  }

  addMessage('bot', {
    text: 'Hi! I am PolyU Campus Navigator Chatbot. I am here to help you navigate the PolyU. ' +
      'Whether you need walking directions, green facilities, or essential amenities, ' +
      'I have your back! ',
    notes: [],
    facilities: [],
  });

  const rec = setupRecognition();
  const mic = $('mic');
  mic.addEventListener('click', () => {
    if (!rec) return;
    window.speechSynthesis && window.speechSynthesis.cancel();
    rec.lang = $('lang').value;
    try { rec.start(); } catch (err) { /* already running */ }
  });

  $('speaker').addEventListener('click', (ev) => {
    state.voiceOn = !state.voiceOn;
    ev.currentTarget.classList.toggle('on', state.voiceOn);
    if (!state.voiceOn && window.speechSynthesis) window.speechSynthesis.cancel();
  });

  $('composer').addEventListener('submit', (ev) => {
    ev.preventDefault();
    send($('input').value);
  });

  // legend chips hide a whole facility category, like a layer toggle on a map site
  document.querySelectorAll('#legend-cats .legend-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const cat = chip.dataset.cat;
      if (state.hidden.has(cat)) state.hidden.delete(cat);
      else state.hidden.add(cat);
      chip.classList.toggle('off', state.hidden.has(cat));
      applyFilters();
    });
  });

  window.addEventListener('resize', updateScale);

  setupSearch();

  $('layers-all').addEventListener('click', () => {
    state.hidden.clear();
    applyFilters();
    syncLayerChips();
  });
  $('layers-none').addEventListener('click', () => {
    state.hidden = new Set(ALL_CATS);
    applyFilters();
    syncLayerChips();
  });

  $('basemap-btn').addEventListener('click', (ev) => {
    state.basemap = !state.basemap;
    ev.currentTarget.classList.toggle('active', state.basemap);
    $('map').classList.toggle('basemap', state.basemap);
    ev.currentTarget.title = state.basemap
      ? 'Basemap: street map (click for the illustrated campus)'
      : 'Basemap: illustrated campus (click for the street map)';
    drawTiles();
    drawFootprints();
  });

  $('links-toggle').addEventListener('click', (ev) => {
    state.showLinks = !state.showLinks;
    ev.currentTarget.classList.toggle('off', !state.showLinks);
    $('map').classList.toggle('no-links', !state.showLinks);
  });

  $('doors-toggle').addEventListener('click', (ev) => {
    state.showDoors = !state.showDoors;
    ev.currentTarget.classList.toggle('off', !state.showDoors);
    drawGraph();
  });

  // settings dialog: writes .env through /api/settings
  $('settings-btn').addEventListener('click', async () => {
    let status = null;
    try { status = await (await fetch('/api/status')).json(); } catch (err) { /* offline */ }
    openSettings(status);
  });
  $('settings-close').addEventListener('click', () => { $('settings-modal').hidden = true; });
  $('settings-cancel').addEventListener('click', () => { $('settings-modal').hidden = true; });
  $('settings-save').addEventListener('click', saveSettings);
  $('settings-modal').addEventListener('click', (ev) => {
    if (ev.target === $('settings-modal')) $('settings-modal').hidden = true;
  });
  $('settings-key').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') saveSettings();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') $('settings-modal').hidden = true;
  });

  $('reset').addEventListener('click', async () => {
    await fetch('/api/reset', { method: 'POST' });
    $('chat').innerHTML = '';
    $('layer-route').innerHTML = '';
    $('layer-facilities').innerHTML = '';
    $('map').classList.toggle('no-links', !state.showLinks);
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    setAvatar('idle');
    addMessage('bot', { text: 'Conversation cleared. Where would you like to go?', notes: [], facilities: [] });
  });

  // pan
  const svg = $('map');
  let drag = null;
  svg.addEventListener('mousedown', (ev) => {
    drag = { x: ev.clientX, y: ev.clientY, vx: state.view.x, vy: state.view.y };
    svg.classList.add('dragging');
  });
  window.addEventListener('mouseup', () => { drag = null; svg.classList.remove('dragging'); });
  window.addEventListener('mousemove', (ev) => {
    if (!drag) return;
    const rect = svg.getBoundingClientRect();
    const scale = state.view.w / rect.width;
    state.view.x = drag.vx - (ev.clientX - drag.x) * scale;
    state.view.y = drag.vy - (ev.clientY - drag.y) * scale;
    applyView();
  });

  // zoom
  svg.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const rect = svg.getBoundingClientRect();
    const cx = state.view.x + (ev.clientX - rect.left) * (state.view.w / rect.width);
    const cy = state.view.y + (ev.clientY - rect.top) * (state.view.h / rect.height);
    zoom(ev.deltaY > 0 ? 1.12 : 0.89, cx, cy);
  }, { passive: false });

  $('zoom-in').addEventListener('click', () => zoom(0.8));
  $('zoom-out').addEventListener('click', () => zoom(1.25));
  $('zoom-fit').addEventListener('click', () => {
    if (state.graph) { setupProjection(state.graph.nodes); drawRoute(null); }
  });

  if (window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = () => { state.voice = pickVoice(); };
  }
}

boot();
