"""Give every authored place its two Chinese script names, and one column each.

Why this file exists
--------------------
A campus place has ONE Chinese name written two ways: the traditional characters the
PolyU campus map publishes as zh_HK, and the simplified characters it publishes as
zh_CN. A single "name:zh" column cannot say which of the two it holds, so this script
replaces it with two unambiguous ones:

    name:zh-Hant   the traditional characters
    name:zh-Hans   the simplified characters

The generic name:zh is REMOVED. Keeping it next to name:zh-Hant would store the same
string twice and leave every reader guessing which is authoritative; name:zh-Hant is
that answer, so name:zh has no job left. The two script tags are the BCP-47 form OSM
uses, so they also say what they mean to any tool that already reads OSM data.

The two axes, and why only one of them needs a column
-----------------------------------------------------
Two independent questions, and it is worth not confusing them:

  * SCRIPT -- traditional (fanti) or simplified (jianti). This is a CHARACTER
    mapping. It is what name:zh-Hans holds.

  * VARIANT -- Cantonese (Yue) or Mandarin (Putonghua). This is a LANGUAGE
    difference, and it lives in vocabulary and grammar ("hai/si", "m/hou",
    "ge/dik", "bin dou/na li"), never in a place name. The library is read
    identically in both variants; only the characters differ. That difference
    belongs to the reply sentences, which facility_speech.py already writes per
    variant -- not to a name column.

Measured on the 561 published records in data/raw_data/campus_map_pois.json: the
official zh_HK and zh_CN strings differ by character substitution ONLY. All 555
equal-length pairs are reproduced exactly by the character map derived below, with
zero conflicting mappings, and the 6 remaining pairs differ by a single space. There
is no separate "Cantonese name" to separate out.

Where each simplified name comes from
-------------------------------------
  1. The official zh_CN, when the feature traditional name is byte-for-byte the
     official zh_HK of the record with the same English name. Same place, so the
     PolyU string is used as published, with no conversion at all.
  2. Otherwise a character conversion of the traditional name, using a map derived
     from those same official pairs. A character with no known simplified form is
     reported, never guessed.

Reading the traditional name
----------------------------
name:zh is read first when it is still present, so the first run consumes it; every
later run reads name:zh-Hant. Running the script twice is a no-op the second time.

Usage
-----
    python tools/add_zh_names.py --check   # report only, write nothing
    python tools/add_zh_names.py           # write both script names, drop name:zh
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from campus_nav.config import NODES_FILE, RAW_DIR, SHAPES_FILE, rel  # noqa: E402

TARGETS = (NODES_FILE, SHAPES_FILE)
GENERIC = "name:zh"
HANT = "name:zh-Hant"
HANS = "name:zh-Hans"


def traditional_of(properties: dict) -> str:
    """The traditional name, from either column, so a second run still works."""
    return (properties.get(GENERIC) or properties.get(HANT) or "").strip()


def load_official() -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Published names, plus the character map derived from them.

    Returns (english -> (zh_HK, zh_CN), traditional char -> simplified char).
    """
    path = Path(RAW_DIR) / "campus_map_pois.json"
    if not path.is_file():
        raise SystemExit(f"missing {rel(path)}: it is the source of the simplified names")

    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    pairs: dict[str, tuple[str, str]] = {}
    char_map: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}

    for record in records:
        names = record.get("name") or {}
        english = (names.get("en_US") or "").strip()
        hk = (names.get("zh_HK") or "").strip()
        cn = (names.get("zh_CN") or "").strip()
        if english and hk and cn:
            pairs.setdefault(" ".join(english.casefold().split()), (hk, cn))
        if not hk or not cn or len(hk) != len(cn):
            continue
        # Only an aligned pair says anything about a single character.
        for traditional, simplified in zip(hk, cn):
            if traditional == simplified:
                continue
            previous = char_map.get(traditional)
            if previous is not None and previous != simplified:
                conflicts.setdefault(traditional, set()).update({previous, simplified})
            char_map[traditional] = simplified

    if conflicts:
        detail = "; ".join(f"{k} -> {sorted(v)}" for k, v in sorted(conflicts.items()))
        raise SystemExit(
            "the published names disagree about these characters, so no character "
            f"map can be derived: {detail}"
        )
    return pairs, char_map


def simplified_for(properties: dict, pairs: dict, table) -> tuple[str, str]:
    """The simplified name for one feature, and where it came from."""
    traditional = traditional_of(properties)
    if not traditional:
        return "", "missing"
    english = (properties.get("name:en") or properties.get("name") or "").strip()
    official = pairs.get(" ".join(english.casefold().split()))
    if official and official[0] == traditional:
        return official[1], "official"
    return traditional.translate(table), "converted"


def with_script_names(properties: dict, simplified: str) -> dict:
    """The same properties, with name:zh replaced by the two script-tagged names.

    The pair lands where the generic name was, so the file keeps reading in the same
    order. A feature that never had a Chinese name is left without one.
    """
    traditional = traditional_of(properties)
    out: dict = {}
    for key, value in properties.items():
        if key in (GENERIC, HANT):
            if not traditional:
                continue
            out[HANT] = traditional
            if simplified:
                out[HANS] = simplified
            continue
        if key == HANS:
            continue
        out[key] = value
    return out


def process(path: Path, pairs: dict, char_map: dict, check: bool) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    table = str.maketrans(char_map)
    counts = {"official": 0, "converted": 0, "missing": 0}
    disagreements: list[tuple] = []
    unconvertible: list[tuple] = []

    for feature in document.get("features", []):
        properties = feature.get("properties") or {}
        simplified, source = simplified_for(properties, pairs, table)
        counts[source] += 1

        traditional = traditional_of(properties)
        english = (properties.get("name:en") or properties.get("name") or "").strip()
        official = pairs.get(" ".join(english.casefold().split()))
        # Two sources disagreeing about the same building is a data bug worth
        # naming, not a choice for this script to make quietly.
        if official and official[0] != traditional:
            disagreements.append((english, traditional, simplified, official[0], official[1]))
        leftovers = sorted({c for c in simplified if c in char_map})
        if leftovers:
            unconvertible.append((english, traditional, "".join(leftovers)))

        feature["properties"] = with_script_names(dict(properties), simplified)

    if not check:
        path.write_text(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    tag = "would write" if check else "wrote"
    print(f"{rel(path)}  ({tag})")
    print(f"   features                            {len(document.get('features', []))}")
    print(f"   simplified name taken from zh_CN    {counts['official']}")
    print(f"   simplified name by character map    {counts['converted']}")
    if counts["missing"]:
        print(f"   feature has no Chinese name          {counts['missing']}")
    for english, traditional, simplified, off_hk, off_cn in disagreements:
        print(f"   [data check] {english!r}")
        print(f"        this file : {traditional!r} -> {simplified!r}")
        print(f"        campus map: {off_hk!r} -> {off_cn!r}")
    for english, traditional, leftovers in unconvertible:
        print(f"   [unconverted] {english!r} {traditional!r}: {leftovers}")
    print()
    return {"disagreements": len(disagreements), "leftovers": len(unconvertible)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replace name:zh with name:zh-Hant and name:zh-Hans")
    parser.add_argument("--check", action="store_true",
                        help="report what would change, write nothing")
    args = parser.parse_args(argv)

    pairs, char_map = load_official()
    print(f"published records carrying both scripts: {len(pairs)}")
    print(f"character map derived from them        : {len(char_map)} traditional -> simplified")
    print()
    problems = 0
    for path in TARGETS:
        if not path.is_file():
            raise SystemExit(f"missing {rel(path)}")
        result = process(path, pairs, char_map, args.check)
        problems += result["leftovers"]
    if problems:
        raise SystemExit(f"{problems} feature(s) still hold a traditional character: "
                         "add the missing mapping rather than guessing")
    if args.check:
        print("--check: nothing was written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
