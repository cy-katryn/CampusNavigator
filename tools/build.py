"""Build data/processed_data/campus_graph.json from the authored GeoJSON.

    data/authored/campus_pois1.geojson    Points  -> every destination
    data/authored/campus_paths1.geojson   Lines   -> the walkways
    data/processed_data/campus_graph.json         <- written here, read by the app

    python tools/build.py graph      # then restart the web app
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_nav.config import (  # noqa: E402
    GRAPH_FILE,
    NODES_FILE,
    PATHS_FILE,
    SHAPES_FILE,
    rel,
)
from campus_nav.graph import CampusGraph, haversine_m  # noqa: E402
from campus_nav.places import (  # noqa: E402
    CAMPUS_SERVICES,
    GREEN_ACCEPTS,
    feature_height,
    label_key,
    shape_kind,
)

HKT = timezone(timedelta(hours=8))

REPAIR_M = 3.0      # a loose walkway end this close to another line is joined to it

ATTACH_M = 120.0    # a place further than this from any walkway is an error

OFF_NETWORK_M = 400.0   # beyond this it is another campus, not a drawing mistake

NAME_FIELDS = ("name:en", "name", "official_name", "addr:housename", "brand", "operator")

ENTRANCE_LABELS = {
    "main": "Main Entrance",
    "yes": "Entrance",
    "secondary": "Side Entrance",
    "service": "Service Entrance",
    "emergency": "Emergency Exit",
    "exit": "Exit",
    "staircase": "Stair Entrance",
}

OSM_SERVICE = {
    "amenity=toilets": "Toilet",
    "amenity=drinking_water": "Water Station",
    "amenity=clinic": "Clinic",
    "healthcare=clinic": "Clinic",
    "amenity=restaurant": "Canteen",
    "amenity=fast_food": "Canteen",
    "amenity=cafe": "Cafe",
    "amenity=atm": "Bank",
    "amenity=bank": "Bank",
    "amenity=library": "Library",
    "amenity=theatre": "Theatre",
    "amenity=recycling": "Recycling",
    "shop=books": "Bookstore",
    "shop=convenience": "Shop",
    "shop=bakery": "Shop",
    "shop=kiosk": "Shop",
    "office=security": "Security Office",
    # Infrastructure that people do look for, and which has no name of its own. Without
    # these the map labels them with their raw tag: "highway=elevator (Block Z)".
    "highway=elevator": "Lift",
    "amenity=parking": "Car Park",
    "amenity=parking_entrance": "Car Park Entrance",
    "amenity=clock": "Clock",
    "amenity=theatre": "Theatre",
    "amenity=fitness_centre": "Gym",
    "man_made=flagpole": "Flagpole",
    "railway=service_station": "MTR Service Station",
    "office=research": "Research Office",
    "club=yes": "Club Room",
}

CAMPUS_ROLES = {
    "faculties, schools & departments": ("service", "Faculty / Department"),
    "administrative & supporting office": ("service", "Office"),
    "main locations": ("place", ""),
    "public transport": ("service", "Bus Stop"),
    "amenities": ("service", ""),
    # A block is a place you can ask for by name ("Block Y (Lee Shau Kee Building)"),
    # and 45 of these 52 anchors sit exactly on a drawn path vertex, so they are ordinary
    # destinations. "place" rather than "building": the map already draws the polygon.
    "buildings / cores / wings / blocks": ("place", "Building"),
}

DECOR_TAGS = (
    "amenity=bench", "amenity=waste_basket", "amenity=vending_machine",
    "natural=tree", "leisure=outdoor_seating", "leisure=fitness_station",
    "highway=crossing", "barrier=yes", "barrier=gate", "barrier=bollard",
    "barrier=lift_gate", "barrier=cycle_barrier",
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def accepts_for(service: str) -> list[str]:
    """What a service point accepts, from the one table in campus_nav/places.py.

    Accepts either the published category ("Toilet") or the label we print
    ("Accessible Toilet"), because an OSM-derived node only has the label.
    """
    if service in GREEN_ACCEPTS:
        return list(GREEN_ACCEPTS[service])
    if service in CAMPUS_SERVICES:
        return list(CAMPUS_SERVICES[service][1])
    for label, accepts in CAMPUS_SERVICES.values():
        if label == service:
            return list(accepts)
    return []


def osm_service(props: dict) -> str:
    for pair, label in OSM_SERVICE.items():
        field, _, value = pair.partition("=")
        if str(props.get(field) or "") == value:
            return label
    return ""


def osm_name(props: dict) -> str:
    for field in NAME_FIELDS:
        value = str(props.get(field) or "").strip()
        if value:
            return value
    return ""


def describe_tags(props: dict) -> str:
    """The one tag that says what this thing is, for a message."""
    for field in ("amenity", "shop", "entrance", "healthcare", "office", "leisure",
                  "tourism", "railway", "highway", "building", "man_made", "barrier"):
        if props.get(field):
            return f"{field}={props[field]}"
    return "no identifying tag"


def place_from_feature(feature: dict) -> dict:
    """One authored Point -> one graph place.

    Two conventions are accepted: the app's own properties (kind / service / aliases
    / source / ref, which the PolyU extracts produced) and raw OpenStreetMap tags
    (amenity=..., shop=..., entrance=...), which is what an Overpass export gives.
    """
    props = dict(feature.get("properties") or {})
    lng, lat = (feature["geometry"]["coordinates"] + [0, 0])[:2]
    lat, lng = round(float(lat), 6), round(float(lng), 6)
    entrance = str(props.get("entrance") or "")
    service = str(props.get("service") or "") or osm_service(props)
    campus = CAMPUS_ROLES.get(str(props.get("kind") or "").strip().lower())
    if campus:
        # campus-map vocabulary: the kind is the role, the amenities field the label
        kind = campus[0]
        service = str(props.get("amenities") or "").strip().title() or campus[1]
    elif props.get("kind"):
        kind = str(props["kind"])
    elif entrance:
        kind = "entrance"
    elif service:
        kind = "service"
    else:
        kind = "place"
    name = osm_name(props)
    if not name:
        name = service or ENTRANCE_LABELS.get(entrance, "Entrance") if kind == "entrance" \
            else (service or "Unnamed place")

    # A place has exactly two names: the English one and the Chinese one. There is no
    # third list of invented search words -- a node answers to its name, its Chinese
    # name and its id, and that is what the dialogue matches against.
    # The authored files carry the two scripts separately: name:zh-Hant and
    # name:zh-Hans. The graph keeps the traditional one, which is the form the place
    # is known by on campus and the base the simplified form is derived from.
    # name:zh is gone: the authored files carry the two scripts separately, and so
    # does the graph. name_zh is what the place is called on campus; name_zh_cn is the
    # same name in simplified characters, taken from the file rather than converted at
    # run time from a table that had to be maintained by hand.
    name_zh = str(props.get("name:zh-Hant") or props.get("name:zh") or "").strip()
    name_zh_cn = str(props.get("name:zh-Hans") or "").strip()

    return {
        "id": str(props.get("id") or f"N{lat},{lng}"),
        "name": name,
        "name_zh": name_zh,
        "name_zh_cn": name_zh_cn,
        "lat": lat,
        "lng": lng,
        "kind": kind,
        "building": str(props.get("building") or ""),
        "parent": str(props.get("parent") or ""),
        "floor": str(props.get("level") or props.get("floor") or ""),
        "service": service,
        "source": str(props.get("source") or "osm"),
        "ref": str(props.get("ref") or props.get("id") or ""),
        "accepts": accepts_for(service),
    }


def structure_index(building_features: list[dict]) -> list[dict]:
    """Every polygon, and the anchor place it will become.

    The polygon is what the map extrudes. The anchor is the point a route ends at,
    and -- for anything standing inside the polygon -- the parent.
    """
    structures: list[dict] = []
    used: set[str] = set()
    for feature in building_features:
        geometry = feature.get("geometry") or {}
        props = dict(feature.get("properties") or {})
        if geometry.get("type") != "Polygon":
            continue
        ring = (geometry.get("coordinates") or [[]])[0]
        if len(ring) < 3:
            continue
        kind = shape_kind(props)
        name = osm_name(props)
        # A polygon can be drawn without being a destination. "routable": "no" is this
        # project's own switch (not an OSM tag): the block still appears on the map,
        # but nothing can be asked to route to it. It is what makes building parts,
        # which describe the volume and not the entrance, safe to keep.
        routable = str(props.get("routable") or "").strip().lower() not in {
            "no", "false", "0", "n",
        }
        base = slug(name) if name else "B" + slug(str(props.get("id") or ""))[:16]
        node_id, n = base, 2
        while node_id in used:
            node_id = f"{base}_{n}"
            n += 1
        used.add(node_id)
        structures.append({
            "id": node_id,
            "name": name,
            "kind": kind,
            "routable": routable,
            "props": props,
            "ring": ring,
            "lat": round(sum(point[1] for point in ring) / len(ring), 6),
            "lng": round(sum(point[0] for point in ring) / len(ring), 6),
        })
    return structures


def slug(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_").upper()
    return text or "NODE"


def is_decor(props: dict) -> bool:
    # An authored place says what it is: "kind" / "service" is a decision already made
    # in the file, and nothing here may overrule it. Without this, every GreenMap point
    # looks like furniture because its properties are the app's vocabulary rather than
    # raw OSM tags.
    if props.get("kind") or props.get("service"):
        return False
    for pair in DECOR_TAGS:
        field, _, value = pair.partition("=")
        if str(props.get(field) or "") == value:
            return True
    return not any(
        props.get(field)
        for field in ("amenity", "shop", "entrance", "healthcare", "office", "leisure",
                      "tourism", "railway", "man_made", "club", "highway")
    )


def read_authored(path: Path, geometry: str) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"missing {rel(path)}\n"
            f"  create it: python tools/build.py {'nodes' if geometry == 'Point' else 'paths'}"
        )
    text = path.read_text(encoding="utf-8-sig")  # tolerate a BOM from a Windows editor
    if not text.strip():
        raise SystemExit(
            f"{rel(path)} is empty (0 bytes).\n"
            "  A save almost certainly got interrupted. geojson.io keeps your work in"
            "\n  the browser tab until you reload it, so pressing Save again there is"
            "\n  the fastest fix; otherwise restore a copy before editing further."
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"{rel(path)} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}\n"
            "  An interrupted save or a hand edit is the usual cause."
        ) from None
    features = [
        f for f in raw.get("features") or []
        if ((f.get("geometry") or {}).get("type") or "").startswith(geometry)
    ]
    if not features:
        raise SystemExit(f"{rel(path)} has no {geometry} features")
    return features


def read_node_features() -> list[dict]:
    """Every authored place, from every node file that exists.

    Splitting the places across files is allowed -- one file per source, verified one
    at a time -- but the graph needs them together.
    """
    present = [NODES_FILE] if NODES_FILE.exists() else []
    if not present:
        raise SystemExit(
            "no node file. Expected one of:\n  "
            + rel(NODES_FILE)
            + "\n  draw it in geojson.io, or extract one: python tools/build.py nodes"
        )
    features: list[dict] = []
    for path in present:
        features.extend(read_authored(path, "Point"))
    return features


def read_building_features() -> list[dict]:
    if not SHAPES_FILE.exists():
        raise SystemExit(
            f"missing {rel(SHAPES_FILE)}\n"
            "  this is the structures file: the polygons the map extrudes, and the"
            "\n  places routes can end at"
        )
    return read_authored(SHAPES_FILE, "Polygon")


def vertex_key(lat: float, lng: float) -> tuple[float, float]:
    """A walkway vertex, at about 0.1 m resolution.

    Shared OSM nodes carry identical coordinates, so keying on the coordinate is
    what joins two ways that meet. Nothing else is ever merged: a vertex stays a
    vertex, and every edge length is the exact distance between the two points it
    joins. (An earlier version merged nearby vertices, which chained: a footway
    with nodes every 5 m collapsed into one 100 m blob and every length in the
    graph became a lie. CampusGraph.validate() caught it -- 292 violations.)
    """
    return (round(lat, 6), round(lng, 6))


PARENS = re.compile(r"^(.*?)\s*[(（]([^()（）]+)[)）]$")


def building_name_index(places: list[dict]) -> dict[str, list[str]]:
    """Building label -> its two script forms, for the labels a place stands in.

    A place names the building it stands in ("Block Y (Lee Shau Kee Building)"), and a
    Chinese reply has to say that name. The graph itself can answer most of that from
    the place names it already holds; this index exists only for the labels whose place
    is not in the graph at all -- a bus station or a wing the campus map lists but does
    not route to. Keeping it to the labels actually used keeps it small.
    """
    by_name: dict[str, list[str]] = {}
    for place in places:
        name = place.get("name") or ""
        pair = [place.get("name_zh") or "", place.get("name_zh_cn") or ""]
        if not name or not any(pair):
            continue
        by_name.setdefault(label_key(name), pair)
        qualified = PARENS.fullmatch(name)
        if not qualified:
            continue
        chinese = PARENS.fullmatch(pair[0]) if pair[0] else None
        chinese_cn = PARENS.fullmatch(pair[1]) if pair[1] else None
        for group in (1, 2):
            part = qualified[group].strip()
            if part:
                by_name.setdefault(label_key(part), [
                    (chinese[group].strip() if chinese else ""),
                    (chinese_cn[group].strip() if chinese_cn else ""),
                ])

    wanted = {label_key(place.get("building") or "") for place in places if place.get("building")}
    return {key: by_name[key] for key in sorted(wanted) if key in by_name}


def build_graph(node_features: list[dict], path_features: list[dict],
                repair_m: float = REPAIR_M, keep_decor: bool = False,
                strict: bool = False) -> dict:
    """Build the routing graph from the points file and the paths file.

    strict=True joins a place to the network only where its coordinate is one the drawn
    lines already pass through -- no welding to the nearest vertex, no tolerance. A place
    off the network is then reported, not silently tied to the closest thing, because a
    link the file does not contain is an invented distance.

    No polygons are involved: a structure is drawn by the map and is never a node.
    """
    places = []
    decor = []
    silent = []
    off_network: list[str] = []
    for feature in node_features:
        props = dict(feature.get("properties") or {})
        if not keep_decor and is_decor(props):
            decor.append(f"{props.get('id')}  [{describe_tags(props)}]")
            continue
        # "routable": "no" works on points too: the place stays on the map but is not a
        # destination, which is how a doorway or a decorative marker is kept out.
        if str(props.get("routable") or "").strip().lower() in {"no", "false", "0", "n"}:
            silent.append(f"{props.get('id')}  [{describe_tags(props)}]")
            continue
        place = place_from_feature(feature)
        # The authored file names the building a place stands in. That name is kept
        # for the map and the reply, but never as a graph link: a structure is a
        # picture, not a node, so there is nothing for it to point at. The graph
        # field is cleared so nothing tries to resolve a name as an id.
        if not place.get("building") and place.get("parent"):
            place["building"] = place["parent"]
        place.pop("parent", None)
        places.append(place)

    # Every place names the building it stands in, and a reply in Chinese has to say
    # that name. Resolving it here is what lets the app stop opening the published
    # scrape at start-up for a translation it could have been given.
    #
    # "Block V (Jockey Club Innovation Tower)" is also known as each of its two halves,
    # and the campus map uses both forms. Both are indexed, from the name the place
    # already carries, so no second look-up table has to exist anywhere.
    building_names = building_name_index(places)

    if decor:
        print(f"  {len(decor)} point(s) are furniture, not destinations: kept in the "
              f"file, left out of the graph (--keep-decor to include)")
        for row in decor[:6]:
            print(f"      {row}")
    if silent:
        print(f"  {len(silent)} point(s) carry routable=no: kept in the file, not destinations")

    ids = [place["id"] for place in places]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SystemExit(
            f"{len(duplicates)} duplicate place id(s), e.g. {', '.join(duplicates[:5])}"
            "\n  an id must appear once: two places with the same id means one of them"
            " silently disappears"
        )
    if any(place["kind"] == "waypoint" for place in places):
        raise SystemExit(
            f"{rel(NODES_FILE)} must hold places only, not waypoints")

    # ---- the walkway network, exactly as drawn
    coords: dict[tuple[float, float], tuple[float, float]] = {}
    adj: dict[tuple[float, float], set[tuple[float, float]]] = {}
    link: dict[frozenset, dict] = {}

    def add_link(a, b, stairs: bool, covered: bool) -> None:
        if a == b:
            return
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
        pair = frozenset((a, b))
        if pair in link:
            link[pair]["stairs"] = link[pair]["stairs"] or stairs
            link[pair]["covered"] = link[pair]["covered"] or covered
            return
        link[pair] = {"metres": haversine_m(*coords[a], *coords[b]),
                      "stairs": stairs, "covered": covered}

    for feature in path_features:
        geometry = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        lines = geometry.get("coordinates") or []
        if geometry.get("type") == "LineString":
            lines = [lines]
        stairs = bool(props.get("stairs")) or props.get("highway") == "steps"
        covered = bool(props.get("covered")) or props.get("highway") == "corridor"
        for line in lines:
            keys = []
            for point in line:
                if len(point) < 2:
                    continue
                key = vertex_key(float(point[1]), float(point[0]))
                coords.setdefault(key, (float(point[1]), float(point[0])))
                keys.append(key)
            for a, b in zip(keys, keys[1:]):
                add_link(a, b, stairs, covered)

    if not coords:
        raise SystemExit(f"{rel(PATHS_FILE)} drew no walkways")

    # A way that stops a couple of metres short of another is a data gap rather than
    # a decision, so join the loose ends. Nothing else is touched.
    repairs = 0
    for key in [k for k in coords if len(adj.get(k, ())) == 1]:
        best, best_d = None, repair_m
        for other in coords:
            if other == key or other in adj.get(key, ()):
                continue
            distance = haversine_m(*coords[key], *coords[other])
            if distance < best_d:
                best, best_d = other, distance
        if best is not None:
            add_link(key, best, False, False)
            repairs += 1

    # ---- every place attaches to its nearest walkway vertex
    attached: dict[tuple[float, float], list[dict]] = {}
    problems = []
    unjoined: list[str] = []
    indoor: list[dict] = []
    for place in places:
        # A facility inside a building is not a point on the pavement: you reach it by
        # walking to the building and going in. Giving it its own hop would draw a
        # straight line through a wall and invent a distance nobody has surveyed.
        # Doorways are exempt -- a doorway IS the threshold you walk up to.
        if place.get("parent") and place["kind"] in ("service", "place"):
            indoor.append(place)
            continue
        best, best_d = min(
            ((key, haversine_m(place["lat"], place["lng"], *coords[key])) for key in coords),
            key=lambda pair: pair[1],
        )
        if strict:
            # Joining is a coordinate match or nothing. A place 4 cm from a line is not on
            # that line: a line has no vertex there, so there is nothing to join to.
            here = vertex_key(place["lat"], place["lng"])
            if here in coords:
                attached.setdefault(here, []).append(place)
            else:
                unjoined.append(
                    f"{place['id']} ({str(place.get('name') or 'unnamed')[:36]}) "
                    f"{best_d:.1f} m from the nearest vertex")
            continue
        if best_d > ATTACH_M:
            # Two very different situations, and they must not be confused. A place a
            # couple of hundred metres off means the drawing is wrong. A place 1.6 km
            # off is West Kowloon or the Hung Hom halls: the campus map publishes them,
            # they are genuinely in the file, and this walkway network does not reach
            # them. Skip those and say so; refuse to build on the first kind.
            # The campus map covers several venues by design (SHTM, the halls, West
            # Kowloon), so one of its records standing off the network is expected and
            # is simply reported. Any other source standing this far off means the
            # drawing is wrong, and that still refuses to build.
            if str(place.get("source") or "") == "campus-map" or best_d > OFF_NETWORK_M:
                off_network.append(f"{place['id']} ({place['name'][:34]}) {best_d:.0f} m")
            else:
                problems.append(f"{place['id']} ({place['name'] or 'unnamed'}) is "
                                f"{best_d:.0f} m from the nearest walkway")
            continue
        attached.setdefault(best, []).append(place)
    if problems:
        raise SystemExit(
            f"{len(problems)} place(s) are not on the walkway network:\n  "
            + "\n  ".join(problems[:12])
            + "\n  draw a path to them, move them, or delete them in "
            + rel(NODES_FILE)
        )

    if unjoined:
        print(f"  {len(unjoined)} place(s) share no coordinate with a drawn vertex, so "
              f"nothing joins them to the network:")
        for row in unjoined[:12]:
            print(f"      {row}")
        if len(unjoined) > 12:
            print(f"      ... {len(unjoined) - 12} more")

    if off_network:
        print(f"  {len(off_network)} place(s) are published by PolyU but beyond this "
              f"campus's walkways, so they are not in the graph:")
        for row in off_network[:6]:
            print(f"      {row}")

    # ---- contract chains: a vertex that is neither a junction nor a place's leaf
    # is just a bend, and a bend does not need to be a node
    keep = {key for key in coords if len(adj.get(key, ())) != 2} | set(attached)
    used: set[frozenset] = set()
    chains: list[tuple] = []

    def walk(start, first):
        pair = frozenset((start, first))
        used.add(pair)
        chain = [start, first]
        metres = link[pair]["metres"]
        stairs, covered = link[pair]["stairs"], link[pair]["covered"]
        prev, cur = start, first
        while cur not in keep:
            options = [n for n in adj[cur] if n != prev]
            if not options:
                break
            step = options[0]
            pair = frozenset((cur, step))
            if pair in used:
                break
            used.add(pair)
            metres += link[pair]["metres"]
            stairs = stairs or link[pair]["stairs"]
            covered = covered and link[pair]["covered"]
            chain.append(step)
            prev, cur = cur, step
            if cur == start:
                break
        return chain, metres, stairs, covered

    for start in sorted(keep):
        for first in sorted(adj.get(start, ())):
            if frozenset((start, first)) not in used:
                chains.append(walk(start, first))
    for pair in sorted(link, key=lambda p: sorted(p)):
        if pair not in used:
            a, b = sorted(pair)
            chains.append(walk(a, b))

    endpoints = sorted({p for chain, *_ in chains for p in (chain[0], chain[-1])})
    wp_id = {key: f"W{index:04d}" for index, key in enumerate(endpoints, 1)}
    nodes = [
        {"id": wp_id[key], "name": "", "lat": round(coords[key][0], 6),
         "lng": round(coords[key][1], 6), "kind": "waypoint"}
        for key in endpoints
    ]

    edges = []
    for chain, metres, stairs, covered in chains:
        edges.append({
            "id": "", "u": wp_id[chain[0]], "v": wp_id[chain[-1]],
            "metres": round(metres, 1), "open": True,
            "covered": covered, "stairs": stairs,
            "path": [[round(coords[p][0], 6), round(coords[p][1], 6)] for p in chain],
        })
    # indoor places are nodes with no walkway edge: their route is their building's
    for place in indoor:
        nodes.append(dict(place))
    for key, group in attached.items():
        for place in group:
            nodes.append(dict(place))
            edges.append({
                "id": "", "u": wp_id[key], "v": place["id"],
                "metres": round(max(haversine_m(*coords[key], place["lat"], place["lng"]),
                                  0.1 if strict else 1.0), 1),
                "open": True, "covered": False, "stairs": False,
                "path": [[round(coords[key][0], 6), round(coords[key][1], 6)],
                         [place["lat"], place["lng"]]],
            })

    edge_list = []
    for index, edge in enumerate(sorted(edges, key=lambda e: (e["u"], e["v"])), 1):
        edge["id"] = f"E{index:04d}"
        straight = haversine_m(edge["path"][0][0], edge["path"][0][1],
                               edge["path"][-1][0], edge["path"][-1][1])
        edge["metres"] = round(max(edge["metres"], straight), 1)
        edge_list.append(edge)

    return {
        "meta": {
            "title": "Campus routing graph, built from authored nodes and walkways",
            "generated_at": datetime.now(HKT).strftime("%Y-%m-%dT%H:%M:%S%z"),
            "inputs": [rel(NODES_FILE), rel(PATHS_FILE)],
            # Structures are drawn by the map but never enter the graph: no polygon
            # becomes a node, so nothing can be routed to a building.
            "inputs_drawn_only": [rel(SHAPES_FILE)],
            "real_data": [
                "walkway geometry: (c) OpenStreetMap contributors, ODbL, edited by us",
                "place positions and names: PolyU campus map and GreenMap@PolyU",
            ],
            "derived": [
                "edge length = measured along the drawn walkway",
                f"loose ends within {repair_m} m of another walkway were joined ({repairs})",
                "chains of bends were contracted so a node is a junction or a place",
                f"a place standing outside attaches to its nearest walkway vertex, "
                f"at most {ATTACH_M} m",
                f"{len(indoor)} place(s) stand inside a building and are reached "
                f"through it, on the floor the source publishes",
            ],
            # The display names of the buildings places stand in, both scripts. Derived
            # here from the place names, so the app renders them without a scrape.
            "building_names": building_names,
            "counts": {
                "nodes": len(nodes),
                "waypoints": len(endpoints),
                "places": len(places),
                "edges": len(edge_list),
                "indoor": len(indoor),
                "stairs": sum(1 for e in edge_list if e["stairs"]),
                "covered": sum(1 for e in edge_list if e["covered"]),
            },
        },
        "nodes": nodes,
        "edges": edge_list,
    }


def connectivity_report(graph: dict) -> list[str]:
    """Say which places cannot be reached, and exactly which gap to draw.

    A place in a separate component answers "no route" to every question, which is
    the one kind of wrong answer this project must not give quietly. So the build
    prints the gap, and the coordinates are a LineString you can paste into
    geojson.io to close it.
    """
    coords = {n["id"]: (n["lat"], n["lng"]) for n in graph["nodes"]}
    kind = {n["id"]: n for n in graph["nodes"]}
    adj: dict[str, set[str]] = {nid: set() for nid in coords}
    for edge in graph["edges"]:
        adj[edge["u"]].add(edge["v"])
        adj[edge["v"]].add(edge["u"])

    seen: set[str] = set()
    comps: list[set[str]] = []
    for nid in coords:
        # A place with no walkway edge at all is an indoor facility: it is reached
        # through its building, so it is not a component of its own and must not be
        # reported as stranded.
        if nid in seen or not adj[nid]:
            continue
        stack, comp = [nid], set()
        while stack:
            cur = stack.pop()
            if cur in comp:
                continue
            comp.add(cur)
            stack.extend(adj[cur] - comp)
        seen |= comp
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    if len(comps) == 1:
        return []

    main = comps[0]
    lines = [f"{len(comps) - 1} place(s) sit on a network of their own"]
    for comp in comps[1:]:
        stranded = [kind[nid] for nid in comp if kind[nid]["kind"] != "waypoint"]
        if not stranded:
            continue
        distance, a, b = min(
            ((haversine_m(*coords[a], *coords[b]), a, b) for a in comp for b in main),
            key=lambda trio: trio[0],
        )
        names = ", ".join((n["name"] or n["id"])[:30] for n in stranded[:4])
        more = f" and {len(stranded) - 4} more" if len(stranded) > 4 else ""
        lines.append(f"  unreachable: {names}{more}")
        lines.append(f"  closest gap {distance:.1f} m -- paste this into geojson.io:")
        lines.append("    " + json.dumps({
            "type": "LineString",
            "coordinates": [[round(coords[b][1], 6), round(coords[b][0], 6)],
                            [round(coords[a][1], 6), round(coords[a][0], 6)]],
        }))
    return lines


def cmd_graph(args) -> int:
    nodes = read_node_features()
    paths = read_authored(PATHS_FILE, "LineString")
    # Every routable thing in this graph is a point from the node files: a polygon is a
    # picture on the map, not a destination, so no shapes are read here at all.
    graph = build_graph(nodes, paths, args.repair, args.keep_decor,
                        strict=not args.weld)
    write_json(GRAPH_FILE, graph)
    counts = graph["meta"]["counts"]
    print(f"WROTE {rel(GRAPH_FILE)}")
    print(f"  {counts['places']} places, {counts['waypoints']} walkway vertices, "
          f"{counts['edges']} links")
    if counts["stairs"]:
        print(f"  {counts['stairs']} of those links are flights of steps")
    for line in connectivity_report(graph):
        print(line)
    print("  the app is reading this file -- restart it to pick up the change")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build data/processed_data/campus_graph.json from the authored GeoJSON")
    sub = parser.add_subparsers(dest="command", required=True)

    graph = sub.add_parser("graph", help="rebuild campus_graph.json from the authored files")
    graph.add_argument("--repair", type=float, default=0.0,
                       help="join a loose walkway end this close to another line; 0 (the "
                            "default) keeps every end exactly where you drew it")
    graph.add_argument("--weld", action="store_true",
                       help="ALSO tie places up to 120 m from the network to their nearest "
                            "vertex (the old behaviour). Off by default, so a place joins "
                            "only where it shares a coordinate with a drawn vertex.")
    graph.add_argument("--keep-decor", action="store_true",
                       help="also route to benches, trees and other street furniture")
    graph.set_defaults(func=cmd_graph)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
