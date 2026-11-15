"""Create the three files this project runs on, from the sources you control.

    python tools/build.py nodes     data/authored/campus_nodes.geojson
    python tools/build.py paths     data/authored/campus_paths.geojson
    python tools/build.py graph     data/processed_data/campus_graph.json

Why three steps
---------------
You edit GeoJSON in geojson.io, so both inputs are GeoJSON:

    campus_nodes.geojson    Points  -- one per place the bot may talk about
    campus_paths.geojson    Lines   -- one per stretch of walkway

The app reads exactly one file, campus_graph.json, which is built from them. The
build is arithmetic, never guesswork:

  * every walkway vertex is kept exactly where you drew it, and a chain of bends
    between two junctions becomes one link that carries the shape of the walkway
  * an edge's length is measured ALONG the drawn line, never estimated, so it is
    never shorter than the straight line and the A* heuristic stays admissible
  * each place attaches to its nearest walkway vertex, and if that vertex is
    further than ATTACH_M the build FAILS and names the place, because a 200 m gap
    between a door and its path means the drawing is wrong, not that the place is
    remote

A line tagged stairs marks its links as steps and the map draws them as rungs. A
line tagged covered or indoor marks its links as covered.

The nodes and paths steps never overwrite a file you may have edited -- pass
--force if you really want the raw extraction back.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus_nav.config import (  # noqa: E402
    CAMPUS_POIS_FILE,
    GRAPH_FILE,
    GREEN_NODES_FILE,
    NODE_FILES,
    NODES_FILE,
    OSM_FILE,
    PATHS_FILE,
    RAW_DIR,
    SHAPES_FILE,
    rel,
)
from campus_nav.graph import CampusGraph, haversine_m  # noqa: E402
from campus_nav.search import search  # noqa: E402
from campus_nav.places import (  # noqa: E402
    CAMPUS_SERVICES,
    GREEN_ACCEPTS,
    feature_height,
    green_aliases,
    shape_kind,
)

HKT = timezone(timedelta(hours=8))
REPAIR_M = 3.0      # a loose walkway end this close to another line is joined to it
ATTACH_M = 120.0    # a place further than this from any walkway is an error
OFF_NETWORK_M = 400.0   # beyond this it is another campus, not a drawing mistake

# Highways a person may walk along. service is a campus driveway: real, but a road,
# so it is opt-in rather than part of the default network.
WALKABLE = ("footway", "steps", "corridor", "path", "pedestrian", "living_street")

# ------------------------------------------------------------------ small helpers
def slug(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_").upper()
    return text or "NODE"


def is_truthy(value) -> bool:
    """The campus map publishes flags as 1 / "true" / "yes" / "y"."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def refuse(path: Path, force: bool) -> bool:
    """Authored files are hand-edited, so a rebuild must be asked for explicitly."""
    if path.exists() and not force:
        print(f"{rel(path)} already exists -- this is the copy you edit by hand.")
        print("  pass --force to overwrite it with a fresh extraction")
        return True
    return False


# --------------------------------------------------------- campus map: every POI
CAMPUS_POIS_RAW = RAW_DIR / "campus_map_pois.json"

# The campus map's own vocabulary, and the only vocabulary this file uses. Each category
# PolyU publishes maps onto exactly one of these six kinds:
#
#     AcademicOffice          -> Faculties, Schools & Departments
#     AdministrativeOffice    -> Administrative & Supporting Office
#     Building                -> Buildings / Cores / Wings / Blocks
#     CoreLocation            -> Main Locations
#     PublicTransport         -> Public Transport
#     everything else         -> Amenities
#
# "Room" (227 bare room codes such as AG057b) is the one category with no home in that
# list. It is left out by default; --with-rooms adds it under Main Locations.
CAMPUS_KIND = {
    "AcademicOffice": "Faculties, Schools & Departments",
    "AdministrativeOffice": "Administrative & Supporting Office",
    "Building": "Buildings / Cores / Wings / Blocks",
    "CoreLocation": "Main Locations",
    "PublicTransport": "Public Transport",
}

# The six amenities the brief names. A record either carries one of these or an empty
# string -- there is no guessing a seventh.
CAMPUS_AMENITY = {
    "Toilet": "toilet",
    "WaterStation": "water station",
    "LactationRoom": "lactation room",
    "PrayerRoom": "prayer room",
    "LectureTheatre": "lecture theatre",
}

SPORT_WORDS = ("sport", "swimming", "fitness", "gym", "court", "stadium")


def campus_kind(category: str) -> str:
    """The file's kind for one published category."""
    if category in CAMPUS_KIND:
        return CAMPUS_KIND[category]
    if category == "Room":
        return "Main Locations"
    return "Amenities"


def campus_amenity(category: str, name: str) -> str:
    """Which of the six amenities this record is, or an empty string."""
    if category in CAMPUS_AMENITY:
        return CAMPUS_AMENITY[category]
    if category == "CoreLocation" and any(word in (name or "").lower() for word in SPORT_WORDS):
        return "sports facilities"
    return ""


def campus_places(with_rooms: bool = False) -> list[dict]:
    """Every point the campus map publishes, in this project's field order.

    Position is the published marker -- the pin on the building -- rather than the
    waypoint, which sits out on the walkway and anywhere from 1 m to 156 m away.
    """
    if not CAMPUS_POIS_RAW.exists():
        raise SystemExit(f"missing {rel(CAMPUS_POIS_RAW)}")
    records = json.loads(CAMPUS_POIS_RAW.read_text(encoding="utf-8"))["records"]

    # "Core P" is the campus map's own code; "Core P (Anita Chan Lai Ling Building)" is
    # the name the rest of the world uses. Parent should be the name, so build the
    # translation table from the record set itself rather than inventing one.
    official: dict[str, str] = {}
    for record in records:
        if record.get("category") != "Building":
            continue
        code = (record.get("loc") or "").strip()
        label = ((record.get("name") or {}).get("en_US") or "").strip()
        if code and label:
            official.setdefault(code, label)
            official.setdefault(re.sub(r"\s*\([^)]*\)\s*$", "", code).strip(), label)

    places = []
    used_ids: dict[str, int] = {}
    for record in records:
        category = record.get("category") or ""
        if category == "Room" and not with_rooms:
            continue
        names = record.get("name") or {}
        name_en = (names.get("en_US") or "").strip() or record.get("loc") or category
        name_zh = (names.get("zh_HK") or names.get("zh_CN") or "").strip()
        marker = record.get("marker") or {}
        lat = marker.get("lat") if marker.get("lat") is not None else record.get("lat")
        lng = marker.get("lng") if marker.get("lng") is not None else record.get("lng")
        if lat is None or lng is None:
            continue

        # PolyU's POI ids share a long prefix, so a short slice collides.
        base = "POI_" + str(record.get("poi_id") or record.get("id") or "")[:10]
        used_ids[base] = used_ids.get(base, 0) + 1
        ident = base if used_ids[base] == 1 else f"{base}_{used_ids[base]}"

        aliases = []
        for value in (record.get("loc"), record.get("loc_code")):
            if value and str(value) not in aliases and str(value) != name_en:
                aliases.append(str(value))
        if category == "Toilet" and is_truthy(record.get("toilet_for_disabled")):
            aliases.append("accessible")

        kind = campus_kind(category)
        address = (record.get("addr") or {}).get("en_US", "")
        loc = (record.get("loc") or "").strip()
        # the floor is published in the address ("Room TU502, 5/F, Yip Kit Chuen
        # Building"); when it is not, the loc code sometimes carries it ("Core A (G/F)")
        floor = floor_of(address) or floor_of(loc)
        # a building is not inside another building, so only the rest get a parent
        parent = ""
        if kind != "Buildings / Cores / Wings / Blocks":
            code = re.sub(r"\s*\([^)]*\)\s*$", "", loc).strip()
            parent = official.get(code) or official.get(loc) or code

        places.append({
            "id": ident,
            "name:en": name_en,
            "name:zh": name_zh,
            "kind": kind,
            "amenities": campus_amenity(category, name_en),
            "floor": floor,
            "parent": parent,
            "source": "campus-map",
            "aliases": ";".join(aliases),
            # kept for the graph builder, not written to the file
            "_lat": round(float(lat), 6),
            "_lng": round(float(lng), 6),
            "_category": category,
        })
    return places


def cmd_campus(args) -> int:
    """Write every campus-map POI to data/authored/campus_pois.geojson."""
    if CAMPUS_POIS_FILE.exists() and not args.force:
        print(f"{rel(CAMPUS_POIS_FILE)} already exists -- that is the copy you edit by hand")
        print("  pass --force to overwrite it with a fresh extraction")
        return 0
    places = campus_places(args.with_rooms)
    features = []
    for place in places:
        props = {key: value for key, value in place.items() if not key.startswith("_")}
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point",
                         "coordinates": [place["_lng"], place["_lat"]]},
        })
    write_json(CAMPUS_POIS_FILE, {"type": "FeatureCollection", "features": features})

    print(f"WROTE {rel(CAMPUS_POIS_FILE)}  ({len(features)} points)")
    print()
    print("by kind:")
    for name, count in collections.Counter(
        (f["properties"] or {}).get("kind") for f in features
    ).most_common():
        print(f"    {count:4d}  {name}")
    print()
    print("by amenities:")
    for name, count in collections.Counter(
        (f["properties"] or {}).get("amenities") or "(none)" for f in features
    ).most_common():
        print(f"    {count:4d}  {name}")
    print()
    off = collections.Counter(
        (place["_category"], place.get("id", "")[:0] or "") for place in places
    )
    outside = sum(
        1 for place in places
        if place["_category"] in ("Building", "CoreLocation", "AcademicOffice",
                                  "AdministrativeOffice", "PublicTransport")
        and False
    )
    total = len(features)
    with_floor = sum(1 for f in features if (f["properties"] or {}).get("floor"))
    with_parent = sum(1 for f in features if (f["properties"] or {}).get("parent"))
    no_parent = sum(
        1 for f in features
        if not (f["properties"] or {}).get("parent")
        and (f["properties"] or {}).get("kind") != "Buildings / Cores / Wings / Blocks"
    )
    print(f"  floor filled : {with_floor}/{total}")
    print(f"  parent filled: {with_parent}/{total}  ({no_parent} non-building record(s) "
          f"have no building to belong to)")
    print("fields written, in this order: id, name:en, name:zh, kind, amenities, floor, "
          "parent, source, aliases")
    print("  next: python tools/build.py graph")
    return 0


# ------------------------------------------------------------ green facilities
GREEN_RAW = RAW_DIR / "green_facilities.json"

# GreenMap publishes the full category name; a node reading "UniRe(c) (University /
# Universal Recycling Facilities) · Block Z" is unreadable in a list.
GREEN_SHORT = {
    "Recyclables Collection": "Recyclables",
    "UniRe© (University / Universal Recycling Facilities)": "UniRe recycling",
    "Glass Bottle Collection": "Glass bottle recycling",
    "Rechargeable Battery Collection": "Battery recycling",
    "Waste Electrical and Electronic Equipment Collection": "WEEE recycling",
    "Reverse Vending Machine": "Reverse vending",
    "Canteen Food Waste Collection": "Canteen food waste",
    "Pantry Food Waste Collection": "Pantry food waste",
}

CODE_OF_NAME = re.compile(r"^(?:Core|Block|Wing)\s+([A-Za-z0-9]+)", re.I)
FLOOR = re.compile(r"\b(\d{1,2}\s*/\s*F|[PGBL]\s*/\s*F|LG\s*\d\s*/\s*F)\b", re.I)


def floor_of(text: str) -> str:
    """Just the floor token: 'Core B - G/F, next to the guard booth' -> 'G/F'."""
    match = FLOOR.search(text or "")
    return match.group(1).upper().replace(" ", "") if match else ""


def code_to_official(pois: list[dict]) -> dict[str, str]:
    """'Block N' -> 'N': the code GreenMap publishes instead of a building name."""
    table: dict[str, str] = {}
    for poi in pois:
        if poi.get("category") != "Building":
            continue
        name = (poi.get("name") or {}).get("en_US") or ""
        match = CODE_OF_NAME.match(name) or CODE_OF_NAME.match(poi.get("loc") or "")
        if match:
            table.setdefault(match.group(1).upper(), name)
    return table


def green_places() -> list[dict]:
    """GreenMap@PolyU's recycling and food-waste points, as graph places.

    Only what the two published datasets actually support: a point with coordinates on
    the main campus, in a category whose name states what it accepts. The building it
    names is carried through so the point can be grouped under that building later --
    and where GreenMap publishes only a block code ("N"), the code is translated to the
    official building name using the campus map's own POI list.
    """
    if not GREEN_RAW.exists():
        raise SystemExit(f"missing {rel(GREEN_RAW)}")
    codes = code_to_official(
        json.loads((RAW_DIR / "campus_map_pois.json").read_text(encoding="utf-8"))["records"]
        if (RAW_DIR / "campus_map_pois.json").exists() else []
    )
    places = []
    seen = set()
    for record in json.loads(GREEN_RAW.read_text(encoding="utf-8"))["records"]:
        if not record.get("has_coords"):
            continue
        if not (record.get("campus") or "").startswith("Main"):
            continue
        category = record.get("type")
        if category not in GREEN_ACCEPTS:
            continue
        floor = floor_of(record.get("address", ""))
        key = (category, record.get("building"), floor,
               round(float(record["lat"]), 6), round(float(record["lng"]), 6))
        if key in seen:
            continue
        seen.add(key)
        host_raw = (record.get("building") or record.get("campus") or "campus").strip()
        host = codes.get(host_raw.upper(), host_raw)
        label = GREEN_SHORT.get(category, category)
        places.append({
            "id": f"SVC_{record['uid']}",
            "name": f"{label} · {host}" + (f" · {floor}" if floor else ""),
            "lat": round(float(record["lat"]), 6),
            "lng": round(float(record["lng"]), 6),
            "kind": "service",
            "building": record.get("building") or "",
            "floor": floor,
            "service": category,
            "source": "greenmap",
            "ref": str(record.get("uid", "")),
            "aliases": [category.lower(), *green_aliases(category)],
        })
    return places


def cmd_green(args) -> int:
    """Write the GreenMap points as their own node file, and report the collisions.

    They are kept apart from campus_nodes.geojson because they come from a different
    dataset with a different update cycle, and because a disputed position should stay
    visible in the file rather than being merged away.
    """
    if GREEN_NODES_FILE.exists() and not args.force:
        print(f"{rel(GREEN_NODES_FILE)} already exists -- that is the copy you edit by hand")
        print("  pass --force to overwrite it with a fresh extraction")
        return 0
    places = green_places()
    features = []
    for place in places:
        props = {k: v for k, v in place.items() if k not in ("lat", "lng")}
        props["aliases"] = ";".join(place.get("aliases") or [])
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point", "coordinates": [place["lng"], place["lat"]]},
        })
    write_json(GREEN_NODES_FILE, {"type": "FeatureCollection", "features": features})
    print(f"WROTE {rel(GREEN_NODES_FILE)}  ({len(features)} GreenMap points)")

    # What needs looking at. Two questions, and only two, because a list of every
    # point within 25 m of every other point is not a report, it is noise:
    #   1. does this point fall inside a building, so it can be grouped under it?
    #   2. is it sitting on top of a point from the other file?
    structures = structure_index(read_building_features())
    outside = []
    for feature in features:
        props = feature.get("properties") or {}
        lng, lat = feature["geometry"]["coordinates"][:2]
        if containing_structure(lng, lat, structures) is None:
            outside.append(props)
    inside = len(features) - len(outside)
    print(f"  {inside} of {len(features)} fall inside a building and will be grouped under it")
    if outside:
        print(f"  {len(outside)} do not -- they will read as bare names:")
        for props in outside[: args.limit]:
            print(f"    {str(props.get('id')):16s} {str(props.get('name'))[:52]}")

    if NODES_FILE.exists():
        existing = [
            ((feature.get("properties") or {}), feature["geometry"]["coordinates"])
            for feature in read_authored(NODES_FILE, "Point")
        ]
        clashes = []
        for feature in features:
            props = feature.get("properties") or {}
            lng, lat = feature["geometry"]["coordinates"][:2]
            for oprops, (olng, olat) in existing:
                distance = haversine_m(float(lat), float(lng), float(olat), float(olng))
                if distance <= 2.5:
                    clashes.append((distance, props, oprops))
        clashes.sort(key=lambda row: row[0])
        print(f"  {len(clashes)} GreenMap point(s) share a spot (<=2.5 m) with a "
              f"campus_nodes.geojson point:")
        for distance, mine, theirs in clashes[: args.limit]:
            print(f"    {distance:4.1f} m  {str(mine.get('name'))[:34]:34s} "
                  f"<-> {str(theirs.get('name') or theirs.get('id'))[:34]}")
        if not clashes:
            print("    none")
    print("  next: python tools/build.py graph")
    return 0


# --------------------------------------------------- reading OSM-tagged features
# Display order: the English name first, because the app speaks English. OpenStreetMap
# often puts the bilingual form in "name" ("Z座 Block Z"), which reads badly in a
# reply, so that form becomes an alias instead -- Chinese queries still match it.
NAME_FIELDS = ("name:en", "name", "official_name", "addr:housename", "brand", "operator")

# An entrance with no name of its own still has to be tellable from its neighbours.
ENTRANCE_LABELS = {
    "main": "Main Entrance",
    "yes": "Entrance",
    "secondary": "Side Entrance",
    "service": "Service Entrance",
    "emergency": "Emergency Exit",
    "exit": "Exit",
    "staircase": "Stair Entrance",
}

# OpenStreetMap tag -> the label a reply uses. Only what the tag itself guarantees;
# anything not listed stays a plain "place".
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


# The campus-map file speaks its own vocabulary ("Faculties, Schools & Departments",
# "Amenities", ...). The graph needs a role, so this is the one place the two meet.
CAMPUS_ROLES = {
    "faculties, schools & departments": ("service", "Faculty / Department"),
    "administrative & supporting office": ("service", "Office"),
    "main locations": ("place", ""),
    "public transport": ("service", "Bus Stop"),
    "amenities": ("service", ""),
    "buildings / cores / wings / blocks": ("reference", ""),
}


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

    aliases = props.get("aliases")
    if isinstance(aliases, str):
        aliases = [a for a in aliases.split(";") if a]
    aliases = list(aliases or [])
    for field in ("name:zh", "name:en", "alt_name", "brand", "operator"):
        value = str(props.get(field) or "").strip()
        if value and value != name and value not in aliases:
            aliases.append(value)

    return {
        "id": str(props.get("id") or f"N{lat},{lng}"),
        "name": name,
        "lat": lat,
        "lng": lng,
        "kind": kind,
        "building": str(props.get("building") or ""),
        "parent": str(props.get("parent") or ""),
        "floor": str(props.get("level") or props.get("floor") or ""),
        "service": service,
        "source": str(props.get("source") or "osm"),
        "ref": str(props.get("ref") or props.get("id") or ""),
        "aliases": aliases,
        "accepts": accepts_for(service),
    }


# A structure is one drawn polygon, with the place id its anchor will get.
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


def inside_ring(lng: float, lat: float, ring: list) -> bool:
    """Ray casting: is this point inside the polygon?

    A structure's own children stand inside it, so the polygon is the parent link --
    derived from the geometry rather than typed in by hand.
    """
    hit = False
    for index in range(len(ring) - 1):
        x1, y1 = ring[index][0], ring[index][1]
        x2, y2 = ring[index + 1][0], ring[index + 1][1]
        if (y1 > lat) != (y2 > lat):
            crossing_x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lng < crossing_x:
                hit = not hit
    return hit


def containing_structure(lng: float, lat: float, structures: list[dict]) -> dict | None:
    """The building a point stands in. Ground features are not parents."""
    for structure in structures:
        if structure["kind"] != "building":
            continue
        if inside_ring(lng, lat, structure["ring"]):
            return structure
    return None


def building_places(structures: list[dict]) -> list[dict]:
    """One place per structure, at its centre, so a building can be a destination.

    Pools, lawns and pitches get no anchor: you do not walk *to* a lawn.
    """
    places = []
    skipped_flat = sum(1 for structure in structures if structure["kind"] != "building")
    skipped_manual = sum(
        1 for structure in structures
        if structure["kind"] == "building" and not structure["routable"]
    )
    for structure in structures:
        if structure["kind"] != "building" or not structure["routable"]:
            continue
        props = structure["props"]
        aliases = [
            str(props.get(field) or "").strip()
            for field in ("name:en", "name:zh", "alt_name")
            if str(props.get(field) or "").strip()
        ]
        places.append({
            "id": structure["id"],
            "name": structure["name"] or "Unnamed structure",
            "lat": structure["lat"],
            "lng": structure["lng"],
            "kind": "building",
            "building": structure["name"],
            "floor": "",
            "service": "",
            "parent": "",
            "source": "osm",
            "ref": str(props.get("id") or ""),
            "aliases": [alias for alias in aliases if alias != structure["name"]],
            "accepts": [],
        })
    if skipped_flat:
        print(f"  {skipped_flat} shape(s) are water, ground or a pitch: drawn, not routed")
    if skipped_manual:
        print(f"  {skipped_manual} structure(s) carry routable=no: drawn, not a destination")
    return places


# Things that are not somewhere you walk *to*. They are still in the file -- they are
# real objects and a named one (a clock, a flagpole) is a good meeting point -- but
# they are excluded from the routing graph unless --keep-decor is passed.
DECOR_TAGS = (
    "amenity=bench", "amenity=waste_basket", "amenity=vending_machine",
    "natural=tree", "leisure=outdoor_seating", "leisure=fitness_station",
    "highway=crossing", "barrier=yes", "barrier=gate", "barrier=bollard",
    "barrier=lift_gate", "barrier=cycle_barrier",
)


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


# ------------------------------------------------------------------------- paths
def read_osm(path: Path):
    """Parse the Overpass XML you downloaded: node coordinates and ways."""
    if not path.exists():
        raise SystemExit(f"missing {rel(path)}\n  download the campus area from overpass-turbo.eu")
    root = ET.parse(path).getroot()
    coords: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        try:
            coords[node.get("id")] = (float(node.get("lat")), float(node.get("lon")))
        except (TypeError, ValueError):
            continue
    ways = []
    for way in root.findall("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        refs = [ref.get("ref") for ref in way.findall("nd")]
        ways.append({"id": way.get("id"), "tags": tags, "refs": refs})
    return coords, ways


def is_walkable(tags: dict, highways: tuple[str, ...]) -> bool:
    return (tags.get("highway") or "") in highways


def path_properties(tags: dict) -> dict:
    """The three facts a walkway carries into the graph."""
    highway = tags.get("highway") or ""
    return {
        "highway": highway,
        "name": tags.get("name") or "",
        "stairs": highway == "steps" or tags.get("conveying") == "steps",
        "covered": tags.get("covered") == "yes" or tags.get("indoor") == "yes"
                   or highway == "corridor",
    }


def paths_from_osm(path: Path, highways: tuple[str, ...]) -> list[dict]:
    """Walkways from the raw Overpass XML."""
    coords, ways = read_osm(path)
    features = []
    for way in ways:
        if not is_walkable(way["tags"], highways):
            continue
        points = [coords[ref] for ref in way["refs"] if ref in coords]
        if len(points) < 2:
            continue
        features.append({
            "type": "Feature",
            "properties": {"osm_id": way["id"], **path_properties(way["tags"])},
            "geometry": {
                "type": "LineString",
                "coordinates": [[lng, lat] for lat, lng in points],
            },
        })
    return features


def paths_from_geojson(path: Path, highways: tuple[str, ...]) -> list[dict]:
    """Walkways from a GeoJSON download instead of the XML.

    Overpass-turbo's GeoJSON export keeps the OSM tags as properties, so highway,
    covered and indoor survive the trip and the extraction is the same one.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    features = []
    for feature in raw.get("features") or []:
        geometry = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        highway = str(props.get("highway") or "")
        if highway not in highways:
            continue
        lines = geometry.get("coordinates") or []
        if geometry.get("type") == "LineString":
            lines = [lines]
        elif geometry.get("type") != "MultiLineString":
            continue
        tags = {key: props.get(key) for key in ("name", "covered", "indoor", "conveying")}
        tags["highway"] = highway
        for line in lines:
            points = [[float(p[0]), float(p[1])] for p in line if len(p) >= 2]
            if len(points) < 2:
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "osm_id": str(props.get("@id") or props.get("id") or ""),
                    **path_properties(tags),
                },
                "geometry": {"type": "LineString", "coordinates": points},
            })
    return features


def cmd_paths(args) -> int:
    if refuse(PATHS_FILE, args.force):
        return 0
    source = Path(args.source)
    if not source.exists():
        raise SystemExit(
            f"missing {rel(source)}\n"
            "  download the campus area from overpass-turbo.eu -- raw OSM (.osm) or\n"
            "  GeoJSON both work; pass it with --source"
        )
    highways = tuple(h.strip() for h in args.highways.split(",") if h.strip())
    if source.suffix.lower() in (".geojson", ".json"):
        features = paths_from_geojson(source, highways)
    else:
        features = paths_from_osm(source, highways)
    write_json(PATHS_FILE, {"type": "FeatureCollection", "features": features})
    steps = sum(1 for f in features if f["properties"]["stairs"])
    print(f"WROTE {rel(PATHS_FILE)}  ({len(features)} walkways, {steps} of them steps)")
    print(f"  read from {rel(source)}; highways kept: {', '.join(highways)}")
    print("  open it in geojson.io, delete what is not a walkway, then: python tools/build.py graph")
    return 0


# ------------------------------------------------------------------------- graph
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


def read_node_features() -> list[dict]:
    """Every authored place, from every node file that exists.

    Splitting the places across files is allowed -- one file per source, verified one
    at a time -- but the graph needs them together.
    """
    present = [path for path in NODE_FILES if path.exists()]
    if not present:
        raise SystemExit(
            "no node file. Expected one of:\n  "
            + "\n  ".join(rel(path) for path in NODE_FILES)
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


def read_authored(path: Path, geometry: str) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"missing {rel(path)}\n"
            f"  create it: python tools/build.py {'nodes' if geometry == 'Point' else 'paths'}"
        )
    text = path.read_text(encoding="utf-8")
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


def build_graph(node_features: list[dict], path_features: list[dict],
                building_features: list[dict] = (), repair_m: float = REPAIR_M,
                keep_decor: bool = False) -> dict:
    structures = structure_index(list(building_features))

    places = []
    decor = []
    silent = []
    reference = []
    off_network: list[str] = []
    for feature in node_features:
        props = dict(feature.get("properties") or {})
        if not keep_decor and is_decor(props):
            decor.append(f"{props.get('id')}  [{describe_tags(props)}]")
            continue
        # "routable": "no" works on points too: the door stays on the map, but it is
        # not a destination. A building already has one anchor; twenty-five anonymous
        # doorway nodes on top of that only make "entrance" ambiguous.
        if CAMPUS_ROLES.get(str(props.get("kind") or "").strip().lower(), ("",))[0] == "reference":
            reference.append(f"{props.get('id')}  {props.get('name:en') or ''}")
            continue
        if str(props.get("routable") or "").strip().lower() in {"no", "false", "0", "n"}:
            silent.append(f"{props.get('id')}  [{describe_tags(props)}]")
            continue
        place = place_from_feature(feature)
        # An explicit "parent" wins over geometry. Doors sit ON a building's edge, so
        # they land outside the polygon and have nothing to be grouped under unless
        # the file says so. The value is the building's NAME (its name:en), not an id,
        # because the ids are derived and nobody should have to know them.
        explicit = str(props.get("parent") or "").strip()
        parent = None
        if explicit:
            parent = next(
                (s for s in structures
                 if s["kind"] == "building" and s["name"]
                 and s["name"].lower() == explicit.lower()),
                None,
            )
            if parent is None:
                # The file may name a building with PolyU's vocabulary ("Block L (Pao
                # Yue-Kong Library)") where the polygon uses OpenStreetMap's ("Pao
                # Yue-kong Library"). Try the loose form before giving up, then fall
                # back to geometry -- and keep the written name as information.
                bare = re.sub(r"^(?:core|block|wing)s?\s+[A-Za-z0-9]+\s*", "", explicit,
                              flags=re.I).strip(" ()").lower()
                parent = next(
                    (s for s in structures
                     if s["kind"] == "building" and s["name"]
                     and bare and bare in s["name"].lower()),
                    None,
                )
                if parent is None:
                    parent = containing_structure(place["lng"], place["lat"], structures)
                if parent is None:
                    parent = explicit  # a name, no polygon: reported below
        else:
            parent = containing_structure(place["lng"], place["lat"], structures)
        # Anything standing in a building is grouped under it -- doorways included.
        # That grouping is what makes a repeated name harmless: "Entrance" said five
        # times is ambiguous, "Entrance (Shaw Amenities Building)" is not.
        if isinstance(parent, str):
            # a written parent that matches no polygon: keep the words, drop the link
            place["building"] = parent
            parent = None
        if parent and place["kind"] in ("service", "place", "entrance"):
            # A polygon can be drawn without being a destination (routable=no), so the
            # parent link must point at a node that exists in the graph. When the
            # containing polygon is not routable, attach to the routable polygon with
            # the same name -- the same building, just the part you can arrive at.
            anchor = parent
            if not parent["routable"]:
                anchor = next(
                    (s for s in structures
                     if s["routable"] and s["kind"] == "building"
                     and s["name"] and s["name"] == parent["name"]),
                    parent,
                )
            place["parent"] = anchor["id"] if anchor["routable"] else ""
            place["building"] = parent["name"]
            if not props.get("name") and not props.get("name:en"):
                # "Toilet" said thirteen times is not a name, and neither is the
                # fifteenth "Entrance". Grouping says which building it belongs to.
                label = place["name"] if place["name"] != "Unnamed place" \
                    else (place["service"] or describe_tags(props))
                place["name"] = f"{label} ({parent['name'] or 'a building'})"
        places.append(place)
    places.extend(building_places(structures))

    if decor:
        print(f"  {len(decor)} point(s) are furniture, not destinations: kept in the "
              f"file, left out of the graph (--keep-decor to include)")
        for row in decor[:6]:
            print(f"      {row}")
    if silent:
        print(f"  {len(silent)} point(s) carry routable=no: kept in the file, not destinations")
    if reference:
        print(f"  {len(reference)} campus-map building record(s) are reference only: the "
              f"polygon is the place, so these are not duplicated as destinations")

    ids = [place["id"] for place in places]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SystemExit(
            f"{len(duplicates)} duplicate place id(s), e.g. {', '.join(duplicates[:5])}"
            "\n  an id must appear once: two places with the same id means one of them"
            " silently disappears"
        )
    if any(place["kind"] == "waypoint" for place in places):
        raise SystemExit(f"{rel(NODES_FILE)} must hold places only, not waypoints")

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
            + f"\n  draw a path to them, move them, or delete them in {rel(NODES_FILE)}"
        )

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
                "metres": round(max(haversine_m(*coords[key], place["lat"], place["lng"]), 1.0), 1),
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
            "inputs": [rel(NODES_FILE), rel(SHAPES_FILE), rel(PATHS_FILE)],
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


# --------------------------------------------------------------------- missing links
def cmd_gaps(args) -> int:
    """Where the walkway network does not join up, ranked by how much it costs.

    A pruned network is full of near-misses: two walkways that pass a few metres apart
    and never touch. Nothing is wrong with either line, but a route has to go all the
    way round instead of stepping across. This lists them, and prices each one.

    It reports the *place*, not the OSM way: the fix is a short line drawn in
    geojson.io between two points that already exist, which is easier than finding the
    original way in the export.
    """
    graph = CampusGraph.load(GRAPH_FILE)
    waypoints = [node for node in graph.nodes.values() if node.kind == "waypoint"]
    linked = set()
    for edge in graph.edges:
        linked.add((min(edge.u, edge.v), max(edge.u, edge.v)))

    pairs = []
    for index, a in enumerate(waypoints):
        for b in waypoints[index + 1:]:
            if (min(a.id, b.id), max(a.id, b.id)) in linked:
                continue
            straight = haversine_m(a.lat, a.lng, b.lat, b.lng)
            if straight > args.snap:
                continue
            current = search(graph, a.id, b.id, algorithm="astar")
            if not current.reachable:
                pairs.append((float("inf"), straight, a, b))
                continue
            pairs.append((current.total_metres - straight, straight, a, b))

    near = [row for row in pairs if row[0] > args.min_saving]
    near.sort(key=lambda row: -row[0] if row[0] != float("inf") else float("-inf"))
    print(f"{len(waypoints)} walkway points, {len(pairs)} pair(s) closer than "
          f"{args.snap:.0f} m without a link between them")
    print(f"{len(near)} of those cost more than {args.min_saving:.0f} m of detour:")
    print()
    for saving, straight, a, b in near[: args.limit]:
        gain = "no route at all today" if saving == float("inf") else f"{saving:5.0f} m wasted"
        print(f"   {gain:22s} link of {straight:5.1f} m")
        print(f"      {a.id} {a.lat:.6f},{a.lng:.6f}")
        print(f"      {b.id} {b.lat:.6f},{b.lng:.6f}")
    if len(near) > args.limit:
        print(f"   ...and {len(near) - args.limit} more (raise --limit)")
    print()
    print("Nothing was changed. Each block above is one short line to draw in")
    print("geojson.io, from the first coordinate to the second. Save, then:")
    print("  python tools/build.py graph")
    return 0


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


# ---------------------------------------------------------------- duplicates
# Words that say nothing about WHICH place this is: every second node is "of" a
# building, on a floor, or next to a door.
NOISE = frozenset({
    "the", "and", "for", "floor", "building", "block", "core", "level",
    "next", "near", "station", "point", "area", "room", "wing", "campus",
})


def name_tokens(text) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
    return {word for word in words if word not in NOISE}


def load_places_for_report() -> list[dict]:
    places = []
    for feature in read_node_features():
        props = feature.get("properties") or {}
        lng, lat = (feature["geometry"]["coordinates"] + [0, 0])[:2]
        places.append({
            "id": str(props.get("id") or ""),
            "name": str(props.get("name") or ""),
            "kind": str(props.get("kind") or ""),
            "service": str(props.get("service") or ""),
            "source": str(props.get("source") or ""),
            "lat": float(lat),
            "lng": float(lng),
        })
    return places


def cmd_overlaps(args) -> int:
    """Explain why places appear to sit on top of each other, and change nothing.

    Three different things look like "duplicate nodes", and only one of them is a
    problem. Separating them is the whole point of this command:

      1. STACKED      several places published at the same coordinate. The campus
                      map gives one anchor per building, so every POI inside it
                      lands on the same point. Real, but unplaceable as-is: the
                      dataset never says where inside the building the AED is.
      2. INSIDE       a GreenMap facility a few metres from the building it names.
                      Expected -- it is a bin inside that building, not a copy of it.
      3. SUSPECT      the same service label, overlapping names, and a real
                      distance between them. This is the only list worth deleting.
    """
    places = load_places_for_report()

    # 1. stacked coordinates
    buckets: dict[tuple[float, float], list[dict]] = {}
    for place in places:
        buckets.setdefault((round(place["lat"], 6), round(place["lng"], 6)), []).append(place)
    stacks = sorted((group for group in buckets.values() if len(group) > 1),
                    key=len, reverse=True)
    stacked_nodes = sum(len(group) for group in stacks)
    print(f"1. STACKED -- {stacked_nodes} of {len(places)} places share a coordinate "
          f"with at least one other ({len(stacks)} points)")
    for group in stacks[: args.limit]:
        print(f"   {group[0]['lat']:.6f},{group[0]['lng']:.6f}  {len(group)} places")
        for place in group[:6]:
            print(f"       {place['id']:24s} {place['name'][:46]:46s} {place['kind']}")
        if len(group) > 6:
            print(f"       ...and {len(group) - 6} more at the same point")
    if len(stacks) > args.limit:
        print(f"   ...and {len(stacks) - args.limit} more stacked points")
    print("   -> to fix: move each one inside its building's footprint in geojson.io,")
    print("      or leave it and accept that the pin means 'in this building'")
    print()

    # 2. a facility inside the building it names, and 3. the only real suspects
    inside = []
    suspects = []
    for index, a in enumerate(places):
        for b in places[index + 1:]:
            if a["source"] == b["source"]:
                continue
            distance = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
            if distance > args.within:
                continue
            shared = name_tokens(a["name"]) & name_tokens(b["name"])
            if not shared:
                continue
            if a["kind"] == "building" or b["kind"] == "building":
                inside.append((distance, a, b, sorted(shared)))
            elif a["service"] and a["service"] == b["service"]:
                suspects.append((distance, a, b, sorted(shared)))

    print(f"2. INSIDE -- {len(inside)} facility/building pair(s) within "
          f"{args.within:.0f} m. This is a bin inside its building, not a duplicate; "
          f"no action needed")
    for distance, a, b, shared in inside[: min(args.limit, 6)]:
        print(f"   {distance:5.1f} m  {', '.join(shared)}")
    print()

    print(f"3. SUSPECT -- {len(suspects)} pair(s) worth deleting")
    if not suspects:
        print("   none: no two places from different sources carry the same service")
        print("   label and the same name")
    for distance, a, b, shared in suspects[: args.limit]:
        print(f"   {distance:5.1f} m  [{a['service']}] {', '.join(shared)}")
        print(f"       {a['id']:24s} {a['name'][:44]:44s} {a['source']}")
        print(f"       {b['id']:24s} {b['name'][:44]:44s} {b['source']}")
    print()
    print("Nothing was changed. Edit the file that owns the wrong place, then:")
    print("  python tools/build.py graph")
    return 0


# ---------------------------------------------------------------------- names
# A name is what the bot says out loud and what a user has to type. A name that is
# missing, shared with nine other places, or half in Chinese is therefore a real
# defect, not a cosmetic one. This command lists them and changes nothing.
CJK = re.compile(r"[\u3400-\u9fff]")


def cmd_names(args) -> int:
    features = [(feature, "node") for feature in read_node_features()]
    if SHAPES_FILE.exists():
        features += [(feature, "shape") for feature in read_building_features()]

    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for feature, which in features:
        props = feature.get("properties") or {}
        ident = str(props.get("id") or "?")
        name = str(props.get("name") or "").strip()
        english = str(props.get("name:en") or "").strip()
        display = osm_name(props)
        if not display:
            buckets["NO NAME AT ALL"].append(f"{ident}  [{describe_tags(props)}]")
        elif not english and not display.isascii():
            buckets["NO ENGLISH NAME"].append(f"{ident}  {display}")
        elif name and english and name != english and CJK.search(name):
            buckets["BILINGUAL name field"].append(f"{ident}  {name}   ->  shown as {english}")
        elif which == "node" and display in {"Entrance", "Unnamed place"}:
            buckets["GENERIC name"].append(f"{ident}  {display}  [{describe_tags(props)}]")
        elif which == "shape" and display == "Unnamed structure":
            buckets["GENERIC name"].append(f"{ident}  {display}  [{describe_tags(props)}]")

    shared = collections.Counter(
        osm_name(feature.get("properties") or {}) for feature, _ in features
    )
    for name, count in shared.most_common():
        if name and count > 1:
            buckets["SHARED name"].append(f"{count:2d} places are called  {name}")

    total = sum(len(rows) for rows in buckets.values())
    if not total:
        print(f"{len(features)} places, every one with a name of its own")
        return 0

    print(f"{total} naming problem(s) across {len(features)} places")
    print()
    for bucket in ("NO NAME AT ALL", "NO ENGLISH NAME", "SHARED name", "GENERIC name",
                   "BILINGUAL name field"):
        rows = buckets.get(bucket, [])
        if not rows:
            continue
        print(f"{bucket} -- {len(rows)}")
        for row in rows[: args.limit]:
            print(f"   {row}")
        if len(rows) > args.limit:
            print(f"   ...and {len(rows) - args.limit} more (raise --limit)")
        print()
    print("BILINGUAL is the mild one: the app now shows name:en and keeps the bilingual")
    print("form as an alias, so it matches Chinese input. The rest need an edit in")
    print("geojson.io -- add or change the feature's name property, then:")
    print("  python tools/build.py graph")
    return 0


# --------------------------------------------------------------------- audit
def relation_parents() -> dict[str, list[tuple[str, dict]]]:
    """way id -> the relations it belongs to, from the raw download.

    A multipolygon building arrives as a relation AND as its member ways. Keeping both
    draws the building twice and gives it two destinations, so the file needs to know
    which of its polygons is a member of something bigger.
    """
    if not OSM_FILE.exists():
        return {}
    parents: dict[str, list[tuple[str, dict]]] = collections.defaultdict(list)
    root = ET.parse(OSM_FILE).getroot()
    for relation in root.findall("relation"):
        tags = {tag.get("k"): tag.get("v") for tag in relation.findall("tag")}
        for member in relation.findall("member"):
            if member.get("type") == "way":
                parents["way/" + str(member.get("ref"))].append((relation.get("id"), tags))
    return parents


def polygon_area_m2(ring: list) -> float:
    lat0 = ring[0][1]
    kx = 111320 * math.cos(math.radians(lat0))
    total = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index][0] * kx, ring[index][1] * 110574
        x2, y2 = ring[index + 1][0] * kx, ring[index + 1][1] * 110574
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def cmd_audit(args) -> int:
    """What is wrong with the structures file, and nothing else.

    The file is the geometry the map extrudes AND the set of places a route can end
    at, so a polygon that is not really a building costs twice: it is drawn as a
    concrete block, and it becomes a destination nobody can be asked to walk to.
    """
    features = read_building_features()
    parents = relation_parents()

    rows: list[dict] = []
    for feature in features:
        props = dict(feature.get("properties") or {})
        ring = (feature.get("geometry") or {}).get("coordinates", [[]])[0]
        rows.append({
            "id": str(props.get("id") or "?"),
            "name": osm_name(props),
            "name_zh": str(props.get("name:zh") or ""),
            "kind": shape_kind(props),
            "part": bool(props.get("building:part")),
            "routable": str(props.get("routable") or "").strip().lower() not in {
                "no", "false", "0", "n",
            },
            "height": feature_height(props),
            "area": polygon_area_m2(ring) if len(ring) > 2 else 0.0,
            "parents": parents.get(str(props.get("id")), []),
            "props": props,
        })

    buildings = [row for row in rows if row["kind"] == "building"]
    flat = [row for row in rows if row["kind"] != "building"]
    print(f"{len(rows)} polygons: {len(buildings)} structures, {len(flat)} painted flat")
    print()

    problems = 0

    parts = [row for row in buildings if row["part"] and row["routable"]]
    settled = [row for row in buildings if row["part"] and not row["routable"]]
    if parts:
        problems += len(parts)
        print(f"DELETE or routable=no -- {len(parts)} building part(s)")
        print("   A part refines one building's shape. The renderer does not use parts,")
        print("   so a routable part is drawn as its own block AND becomes a destination")
        print("   nobody can ask for. Either delete it, or set routable = no.")
        for row in parts[: args.limit]:
            print(f"   {row['id']:18s} {row['area']:6.0f} m2  height={row['height']}  "
                  f"{row['name'] or '(unnamed)'}")
        print()
    if settled:
        print(f"OK -- {len(settled)} building part(s) carry routable=no: drawn, not destinations")
        print()

    members = [row for row in buildings if row["parents"]]
    present = {row["id"] for row in rows}
    interesting = []
    for row in members:
        for relation_id, tags in row["parents"]:
            # Only a problem when the relation is ALSO a feature in this file: that is
            # the same building drawn twice. A relation that is not in the file is just
            # a grouping, and its members are the only geometry there is.
            if f"relation/{relation_id}" in present:
                interesting.append((row, relation_id, tags))
                break
    if interesting:
        problems += len(interesting)
        print(f"CHECK -- {len(interesting)} polygon(s) are also drawn by a relation")
        print("   Keep the relation and delete the member way, or the building appears twice.")
        for row, relation_id, tags in interesting[: args.limit]:
            label = tags.get("name:en") or tags.get("name") or "(unnamed relation)"
            print(f"   {row['id']:18s} belongs to relation/{relation_id}  {label}")
            print(f"        its own name: {row['name'] or '(none)'}")
        print()

    by_name = collections.defaultdict(list)
    for row in buildings:
        if row["name"]:
            by_name[row["name"].lower()].append(row)
    # A name shared by one destination plus its own parts is correct, not a defect.
    shared = {
        name: group for name, group in by_name.items()
        if len([row for row in group if row["routable"]]) > 1
    }
    if shared:
        problems += sum(len([r for r in g if r["routable"]]) - 1 for g in shared.values())
        print(f"MERGE or RENAME -- {len(shared)} name(s) used by more than one structure")
        for name, group in shared.items():
            anchors = [row for row in group if row["routable"]]
            print(f"   {len(group)}x {group[0]['name']}  ({len(anchors)} of them "
                  f"{'is a destination' if len(anchors) == 1 else 'are destinations'})")
            for row in group:
                tag = row["props"].get("building") or row["props"].get("leisure") or "?"
                mark = "" if row["routable"] else "   routable=no"
                print(f"        {row['id']:18s} {row['area']:6.0f} m2  {tag}{mark}")
        print("   One destination is fine: the others are parts of the same building and")
        print("   should carry routable = no. Two or more destinations with one name is")
        print("   ambiguous -- a reply would have to ask which one you meant.")
        print()

    # Only a destination needs a name. A polygon carrying routable=no is scenery, and
    # scenery is allowed to be anonymous.
    unnamed = [row for row in buildings if not row["name"] and row["routable"]]
    if unnamed:
        problems += len(unnamed)
        print(f"NAME -- {len(unnamed)} routable structure(s) with no name")
        print("   An unnamed structure is offered as \"Unnamed structure\", which is not")
        print("   something a person can ask for. Name it, or delete it.")
        for row in unnamed[: args.limit]:
            tag = row["props"].get("building") or "?"
            print(f"   {row['id']:18s} {row['area']:6.0f} m2  building={tag}")
        print()

    no_height = [row for row in buildings if not row["height"]]
    if no_height:
        print(f"HEIGHT -- {len(no_height)} structure(s) with neither height nor "
              f"building:levels")
        print("   These get a made-up height between 55 % and 100 % of the palette")
        print("   maximum. Filling building:levels (or height in metres) makes the")
        print("   skyline real instead of decorative. Not a defect, a missing fact.")
        print()

    no_chinese = [row for row in buildings if row["name"] and not row["name_zh"]]
    if no_chinese:
        print(f"CHINESE -- {len(no_chinese)} named structure(s) with no name:zh")
        print("   Chinese input will not find them. Add name:zh if the Chinese name is")
        print("   published; never invent one.")
        print()

    if flat:
        print(f"INFO -- {len(flat)} polygon(s) are painted flat, not extruded")
        counts = collections.Counter(row["kind"] for row in flat)
        print(f"   {dict(counts)}")
        print("   Correct for squares, lawns, pitches and pools. If one of these is")
        print("   really a building, it is missing its building tag.")
        print()

    if not problems:
        print("nothing to fix")
    return 0


def cmd_graph(args) -> int:
    nodes = read_node_features()
    buildings = read_building_features()
    paths = read_authored(PATHS_FILE, "LineString")
    graph = build_graph(nodes, paths, buildings, args.repair, args.keep_decor)
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
    parser = argparse.ArgumentParser(description="Build the campus data files")
    sub = parser.add_subparsers(dest="command", required=True)

    gaps = sub.add_parser("gaps", help="walkable ways the paths file is missing")
    gaps.add_argument("--snap", type=float, default=8.0,
                      help="how close a way end must be to an existing vertex")
    gaps.add_argument("--min-saving", type=float, default=10.0,
                      help="only report ways worth at least this many metres")
    gaps.add_argument("--limit", type=int, default=15, help="rows to print")
    gaps.set_defaults(func=cmd_gaps)

    campus = sub.add_parser("campus", help="extract PolyU campus-map amenities")
    campus.add_argument("--force", action="store_true", help="overwrite the authored file")
    campus.add_argument("--within", type=float, default=30.0,
                        help="report repeats of the same service this close")
    campus.add_argument("--limit", type=int, default=12, help="repeats to print")
    campus.add_argument("--with-rooms", action="store_true",
                        help="also include the 227 bare room codes")
    campus.set_defaults(func=cmd_campus)

    green = sub.add_parser("green", help="extract the GreenMap points as a node file")
    green.add_argument("--force", action="store_true", help="overwrite the authored file")
    green.add_argument("--within", type=float, default=25.0,
                       help="report points this close to an existing node")
    green.add_argument("--limit", type=int, default=12, help="collisions to print")
    green.set_defaults(func=cmd_green)

    paths = sub.add_parser("paths", help="extract the walkways from the Overpass download")
    paths.add_argument("--force", action="store_true", help="overwrite the authored file")
    paths.add_argument("--source", default=str(OSM_FILE),
                       help="the .osm or .geojson file downloaded from Overpass")
    paths.add_argument("--highways", default=",".join(WALKABLE),
                       help="comma separated highway values to keep (add service for driveways)")
    paths.set_defaults(func=cmd_paths)

    audit = sub.add_parser("audit", help="what is wrong with the structures file")
    audit.add_argument("--limit", type=int, default=12, help="rows per problem")
    audit.set_defaults(func=cmd_audit)

    names = sub.add_parser("names", help="list place names that will read badly")
    names.add_argument("--limit", type=int, default=15, help="rows per problem")
    names.set_defaults(func=cmd_names)

    overlaps = sub.add_parser("overlaps",
                              help="list places that look like the same place twice")
    overlaps.add_argument("--within", type=float, default=45.0,
                          help="only compare places this many metres apart or closer")
    overlaps.add_argument("--limit", type=int, default=40, help="how many pairs to print")
    overlaps.set_defaults(func=cmd_overlaps)

    graph = sub.add_parser("graph", help="build campus_graph.json from the authored files")
    graph.add_argument("--repair", type=float, default=REPAIR_M,
                       help="join a loose walkway end this close to another line")
    graph.add_argument("--keep-decor", action="store_true",
                       help="also route to benches, trees and other street furniture")
    graph.set_defaults(func=cmd_graph)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
