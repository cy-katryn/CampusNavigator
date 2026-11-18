"""Tables that must agree, and guards that must not outgrow their data.

Every bug of this class found in this project had one shape: a single fact kept in two
places, one of them updated.

  * the narration allowance list never learned about the "nearest" fields, so a correct
    Chinese sentence was rejected as "ungrounded number(s): 3, 07, 30, 20";
  * before that, the same list did not know a numeral can be part of a name ("7-Eleven"),
    and was patched by adding the names to the list;
  * the Chinese service-label table is built from a scrape that calls catering "Catering
    Outlets" and clinics "Clinics", while the facility keys said "canteen" and "clinic" --
    so a Chinese summary printed two English rows, and did so again after a refactor.

Numbers are now read out of the payload itself, so they cannot drift. These tests pin the
remaining relations, so the next occurrence fails here instead of in a demo. This file was
lost once because it existed only in an uncommitted tree, which is the argument for having
it in the repository at all.
"""
from __future__ import annotations

import unittest

from campus_nav import copy, format as fmt, kb, local_names, nlu, places
from campus_nav.config import graph_path
from campus_nav.graph import CampusGraph


class TableAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = CampusGraph.load(graph_path())

    def test_every_service_the_map_publishes_has_a_chinese_label(self) -> None:
        services = {n.service for n in self.graph.nodes.values()
                    if n.kind == "facility" and n.service}
        missing = sorted(s for s in services if not local_names.has_service_label(s))
        self.assertEqual(missing, [], "services with no Chinese label")

    def test_every_service_renders_in_chinese_not_english(self) -> None:
        # The failure this pins: "Catering Outlets . 24" beside the translated rows.
        for node in self.graph.nodes.values():
            if node.kind != "facility" or not node.service:
                continue
            label = local_names.service_label(node.service, "zh-HK")
            self.assertNotEqual(label, node.service, f"{node.service} rendered untranslated")

    def test_every_amenity_word_the_engine_can_answer_with_renders_in_chinese(self) -> None:
        # The model reports "lunch"; the engine decides that is a canteen; the answer must be
        # able to say 飯堂 rather than "lunch". Capability folding covers the whole table.
        for word in places.FACILITY_WORDS:
            label = local_names.service_label(word, "zh-HK")
            self.assertNotEqual(label, word, f"{word!r} would be spoken in English")

    def test_a_word_nothing_can_place_is_left_alone(self) -> None:
        for word in ("zzz", "qwerty", "flibbertigibbet"):
            self.assertEqual(local_names.service_label(word, "zh-HK"), word)

    def test_the_label_table_has_no_entries_nothing_can_look_up(self) -> None:
        # Residue from older datasets used to live here (lift, flagpole, clock, shop...).
        reachable = {s.lower() for n in self.graph.nodes.values() if n.service
                     for s in [n.service]}
        reachable |= set(places.FACILITY_WORDS) | set(places.FACILITY_WORDS.values())
        reachable |= set(nlu._ZH_SERVICES) | set(nlu._ZH_SERVICES.values())
        reachable.add("")
        dead = sorted(set(local_names.SERVICE_LABELS) - reachable)
        self.assertEqual(dead, [], "label entries nothing can look up")

    def test_the_two_day_tables_are_one_list(self) -> None:
        self.assertEqual(fmt.DAYS, kb._DAYS)
        for day in fmt.DAYS:
            self.assertIn(f"hours.day.{day.lower()}", copy.COPY)

    def test_every_message_exists_in_every_language(self) -> None:
        for key, row in copy.COPY.items():
            self.assertEqual(set(row), set(copy.LANGUAGES), f"{key} is missing a language")

    def test_every_chinese_amenity_word_reaches_a_renderable_label(self) -> None:
        for capability in nlu._ZH_SERVICES:
            normalised = places.facility_capability(capability)
            published = local_names.service_label(capability, "zh-HK")
            self.assertNotEqual(published, capability,
                                f"Chinese item {capability!r} has no label")
            self.assertTrue(
                places.CAPABILITY_LABELS.get(normalised) or published != capability,
                f"{capability!r} normalises to {normalised!r}, which nothing can match")


class PayloadGuardTests(unittest.TestCase):
    """The narration guard reads the payload, so a new field cannot outgrow it."""

    def test_every_number_in_the_payload_is_allowed(self) -> None:
        from campus_nav.narrate import NUMBER, Facts
        facts = Facts(kind="places", origin="", destination="", reachable=True,
                      need="canteen", match_count=24, reference="Core P",
                      best={"name": "Communal Student Canteen", "floor": "3/F",
                            "metres": 123, "hours_today": "07:30 - 20:30"},
                      language="zh-HK")
        allowed = facts.grounded_numbers()
        for token in NUMBER.findall(str(facts.as_prompt_json())):
            self.assertIn(float(token), allowed, f"{token} is in the payload but not allowed")

    def test_a_number_outside_the_payload_is_still_refused(self) -> None:
        from campus_nav.narrate import Facts, grounded_numbers
        facts = Facts(kind="places", origin="", destination="", reachable=True,
                      need="canteen", match_count=24, best={"metres": 123})
        self.assertEqual(grounded_numbers("大概 400 米。", facts.grounded_numbers()), ["400"])


if __name__ == "__main__":
    unittest.main()

