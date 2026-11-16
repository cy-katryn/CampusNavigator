"""The one vocabulary shared by the data pipeline and the dialogue layer.

This file answers three questions, in exactly one place:

  1. which GreenMap categories become routable places, and what is each one
     allowed to claim it accepts?                  -> GREEN_ACCEPTS
  2. which campus-map categories become routable places, and under what label?
                                                   -> CAMPUS_SERVICES, ROUTABLE_POI_CATEGORIES
  3. how does a user's everyday wording ("glass bottles", "e-waste", "step-free")
     map onto those canonical categories?          -> ITEM_TO_CATEGORY, canonical_category

Why the file exists
-------------------
Those tables used to be copy-pasted into `experiments/build_provisional_graph.py`
and `campus_nav/dialogue.py`. The copies drifted: the pipeline's key was corrupted
to "UniReÂ©" while the dialogue's was "UniReÂ©", so the match failed and **all 28
UniRe recycling points were silently dropped from the map**. One table, imported
twice, cannot drift.

The rule the tables obey
------------------------
Only what the published category name guarantees. "Glass Bottle Collection" accepts
glass bottles; it does not imply it accepts plastic. Anything not listed stays
unknown, and the knowledge base is required to say so.
"""
from __future__ import annotations

# --------------------------------------------------------------------- GreenMap
# GreenMap category -> the canonical item categories it accepts. Only what the
# category name itself guarantees; nothing inferred.
GREEN_ACCEPTS: dict[str, tuple[str, ...]] = {
    "Recyclables Collection": ("paper", "metal", "plastic"),
    "UniRe© (University / Universal Recycling Facilities)": ("paper", "metal", "plastic"),
    "Glass Bottle Collection": ("glass_bottle",),
    "Rechargeable Battery Collection": ("battery",),
    "Waste Electrical and Electronic Equipment Collection": ("weee",),
    "Reverse Vending Machine": ("plastic", "metal"),
    "Canteen Food Waste Collection": ("food_waste",),
    "Pantry Food Waste Collection": ("food_waste",),
}

# ------------------------------------------------------------------- campus map
# Campus-map category -> (label the bot uses, canonical capabilities).
# An empty capability tuple means "this place exists and is navigable, but the bot
# makes no claim about what it offers".
CAMPUS_SERVICES: dict[str, tuple[str, tuple[str, ...]]] = {
    "WaterStation": ("Water Station", ("water_refill",)),
    # The campus map publishes the whole institution, not just its amenities. These
    # are places you would ask for by name ("where is the Academic Registry?"), so they
    # belong on the map; the capability tuple stays empty because the category name
    # says nothing about what the office offers.
    "AcademicOffice": ("Faculty / Department", ()),
    "AdministrativeOffice": ("Office", ()),
    "PublicTransport": ("Bus Stop", ()),
    "LectureTheatre": ("Lecture Theatre", ()),
    "Building": ("Building", ()),
    "CoreLocation": ("Facility", ()),
    "Toilet": ("Accessible Toilet", ("toilet",)),   # only where the POI says accessible
    "AED": ("AED", ("defibrillator",)),
    "Canteen": ("Canteen", ()),
    "Clinic": ("Clinic", ()),
    "LactationRoom": ("Lactation Room", ()),
    "PrayerRoom": ("Prayer Room", ()),
    "Bank": ("Bank", ()),
    "Bookstore": ("Bookstore", ()),
    "Postbox": ("Postbox", ()),
}

# Which campus-map POI categories are allowed onto the routing graph at all.
ROUTABLE_POI_CATEGORIES: frozenset[str] = frozenset({
    "Building", "CoreLocation", "Canteen", "Toilet", "WaterStation",
    "PublicTransport", "LectureTheatre", "Clinic", "AED", "LactationRoom",
    "PrayerRoom", "Bank", "Bookstore", "Postbox",
})


# ----------------------------------------------------------------- user wording
# Everyday item wording -> canonical category. Tolerating plurals is handled by
# canonical_category(); this table only has to list the base forms.
ITEM_TO_CATEGORY: dict[str, str] = {
    "battery": "battery", "batteries": "battery", "power bank": "battery",
    "glass bottle": "glass_bottle", "bottle": "glass_bottle", "glass": "glass_bottle",
    "plastic": "plastic", "plastic bottle": "plastic",
    "paper": "paper", "cardboard": "paper",
    "metal": "metal", "can": "metal", "tin": "metal",
    "fluorescent": "lamp", "lamp": "lamp", "tube": "lamp", "bulb": "lamp",
    "weee": "weee", "appliance": "weee", "air conditioner": "weee",
    "laptop": "computer", "computer": "computer", "phone": "computer",
    "food waste": "food_waste", "leftovers": "food_waste",
}

# Facility wording -> the capability it asks for. A capability is either one of the
# canonical item categories above (a bin that accepts water_refill/toilet is odd,
# but it keeps ONE lookup path for "which places offer X") or a service label.
FACILITY_WORDS: dict[str, str] = {
    "aed": "defibrillator", "defibrillators": "defibrillator",
    "defibrillator": "defibrillator", "heart starter": "defibrillator",
    "toilet": "toilet", "toilets": "toilet", "washroom": "toilet",
    "restroom": "toilet", "bathroom": "toilet", "loo": "toilet",
    "water": "water_refill", "water station": "water_refill",
    "refill": "water_refill", "drinking water": "water_refill",
    "fountain": "water_refill", "water fountain": "water_refill",
    "canteen": "canteen", "cafeteria": "canteen", "restaurant": "canteen",
    "clinic": "clinic", "bank": "bank", "atm": "bank",
    "postbox": "postbox", "post box": "postbox", "letter box": "postbox",
    "bookstore": "bookstore", "bookshop": "bookstore",
    "lactation": "lactation room", "nursing room": "lactation room",
    "prayer": "prayer room", "prayer room": "prayer room",
}

# Wording that means "a facility", not "the building it sits in".
SERVICE_WORDS: tuple[str, ...] = (
    "recycl", "bin", "battery", "batteries", "glass", "water", "refill",
    "weee", "vending", "rvm", "food waste", "unire", "aed", "defibrillator",
    "toilet", "washroom", "restroom", "canteen", "clinic", "postbox",
    "lactation", "prayer", "atm",
)


def label_key(text: str) -> str:
    """The form two name lists are joined on: case, spacing and dotted initials folded.

    The builder resolves a building label against the place names in the authored file,
    and the display layer resolves the same label again when it renders a card. Both
    call this, because a join key that differs by one space fails silently -- the same
    way the UniRe key in this module's docstring once did.
    """
    return " ".join((text or "").casefold().replace(".", " ").split())


def canonical_category(raw: str) -> str:
    """Map everyday item wording to a canonical category, tolerating plurals.

    "glass bottles" -> "glass_bottle": try the phrase, then singular forms, then
    the last noun ("bottles" -> "bottle").
    """
    text = (raw or "").strip().lower()
    if not text:
        return ""
    candidates = [text, text.rstrip("s"), text.rstrip("es")]
    if text.endswith("ies"):
        candidates.append(text[:-3] + "y")
    for cand in candidates:
        if cand in ITEM_TO_CATEGORY:
            return ITEM_TO_CATEGORY[cand]
    words = text.split()
    for word in reversed(words):
        for cand in (word, word.rstrip("s"), word.rstrip("es")):
            if cand in ITEM_TO_CATEGORY:
                return ITEM_TO_CATEGORY[cand]
    return text


def facility_capability(word: str) -> str:
    """'washroom' -> 'toilet'; an unknown word is returned unchanged (lowercased)."""
    text = (word or "").strip().lower()
    return FACILITY_WORDS.get(text, FACILITY_WORDS.get(text.rstrip("s"), text))


# ------------------------------------------------------------- drawn geometry
# The "place" column decides how a drawn shape is painted:
#
#   empty            -> a building, extruded
#   "square"         -> flat, painted as a plaza
#   "garden"         -> flat, painted as green
#   "pool"           -> flat, painted as water
#
# Anything else non-empty is still FLAT -- an unknown word must never turn a surface into
# a tower -- and is painted as a plaza.
_PLACE_KIND = {
    "square": "plaza",
    "garden": "green",
    "pool": "water",
}

FLOOR_HEIGHT_M = 3.2


def shape_kind(props: dict, is_line: bool = False) -> str:
    """What a drawn feature is: a block to extrude, or a flat surface."""
    props = props or {}
    if props.get("kind"):
        return str(props["kind"])
    if is_line:
        return "path"
    place = str(props.get("place") or "").strip().lower()
    if not place:
        return "building"
    return _PLACE_KIND.get(place, "plaza")


def feature_height(props: dict) -> float | None:
    """Metres of extrusion, from a height tag or a storey count, or None.

    None is not a failure: the renderer then uses its own hashed height, which
    claims nothing about the real building.
    """
    props = props or {}
    for key in ("height", "building:height"):
        raw = props.get(key)
        if raw not in (None, ""):
            try:
                return float(str(raw).split()[0])
            except (TypeError, ValueError):
                pass
    levels = props.get("building:levels")
    if levels not in (None, ""):
        try:
            return round(float(str(levels).split(";")[0]) * FLOOR_HEIGHT_M, 1)
        except (TypeError, ValueError):
            pass
    return None


def human_category(category: str) -> str:
    """Canonical category -> the phrase used in a reply."""
    return {
        "glass_bottle": "glass bottles", "battery": "batteries",
        "weee": "electrical and electronic equipment", "food_waste": "food waste",
        "water_refill": "drinking water", "toilet": "accessible toilets",
        "defibrillator": "AEDs (defibrillators)", "paper": "paper",
        "metal": "metal cans", "plastic": "plastics", "lamp": "fluorescent tubes",
        "computer": "computers and phones",
    }.get(category, category.replace("_", " "))
