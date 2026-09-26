from stickman.plan.models import Plan
from stickman.plan.picking import COMPARE_CATEGORIES, Pick, complexity, night_or_fire, pick_compare_units

FIGURES = {"mascot": 1, "caveman_group": 3, "historian": 1}


def unit(unit_id, start, end, part=None, **fields):
    data = {
        "id": unit_id, "part": part, "start": start, "end": end, "source_text": "one two three four",
        "corrected_text": "one two three four", "visual_idea": f"Idea {unit_id}", "visual_type": "literal",
        "shot": "wide", "time_of_day": "day", "characters": [], "mood": None, "setting": [], "props": [],
        "composition": "centred", "energy_marks": [], "softened": False, "softened_reason": None,
        "seed": None, "image_prompt": f"prompt for {unit_id}", "prompt_locked": False,
    }
    return {**data, **fields}


def who(*refs):
    return [{"ref": ref, "action": "standing", "emotion": "calm"} for ref in refs]


def plan_of(*scenes):
    """One scene per argument: a unit dict, or a list of two for a split scene."""
    built, time = [], 0.0
    for number, units in enumerate(scenes, 1):
        units = units if isinstance(units, list) else [units]
        built.append({
            "id": f"{number:03d}", "lines": [number], "start": units[0]["start"], "end": units[-1]["end"],
            "source_text": "one two three four", "corrected_text": "one two three four",
            "split": {"status": "split", "cut_after_word": 2, "candidates": [2]} if len(units) == 2 else {"status": "none"},
            "units": units,
        })
        time = units[-1]["end"]
    return Plan.model_validate({
        "project": "demo", "aspect": "16:9", "style_version": 1,
        "image_model": "@cf/black-forest-labs/flux-2-klein-4b", "duration_end": time, "pace_wps": 2.5,
        "cast": [{"id": "mascot"},
                 {"id": "caveman_group", "name": "Caveman group", "figures": 3, "description": "cavemen", "library_ref": None},
                 {"id": "historian", "name": "Historian", "figures": 1, "description": "a historian", "library_ref": None}],
        "scenes": built,
    })


def six_kinds():
    return plan_of(
        unit("001", 0, 2, characters=who("mascot"), props=["cup"]),                          # 3
        unit("002", 2, 4, characters=who("historian")),                                       # 2
        unit("003", 4, 6, characters=who("caveman_group"), time_of_day="night", props=["campfire"]),  # 7
        unit("004", 6, 8, characters=who("mascot"), visual_type="metaphor"),                  # 2
        [unit("005a", 8, 9, "1 of 2", characters=who("mascot")), unit("005b", 9, 10, "2 of 2", characters=who("mascot"))],
        unit("006", 10, 12, characters=who("caveman_group", "historian"), props=["a", "b", "c"],
             setting=["x", "y"], energy_marks=["motion"]),                                    # 14
    )


def test_complexity_counts_figures_twice_plus_props_setting_and_energy():
    plan = six_kinds()
    assert [complexity(u, FIGURES) for u in plan.units()] == [3, 2, 7, 2, 2, 2, 14]


def test_night_or_fire_covers_night_and_fire_or_flame_words():
    plan = plan_of(
        unit("001", 0, 1, time_of_day="night"),
        unit("002", 1, 2, setting=["Flame lines around a pot"]),
        unit("003", 2, 3, props=["firelight on the wall"]),
        unit("004", 3, 4, props=["a cup"], setting=["a hill"]),
    )
    assert [night_or_fire(u) for u in plan.units()] == [True, True, True, False]


def test_one_unit_per_category_in_order():
    picks = pick_compare_units(six_kinds(), FIGURES)
    assert picks == [
        Pick("mascot", "001"), Pick("extras", "002"), Pick("night_or_fire", "003"),
        Pick("metaphor", "004"), Pick("split_part", "005a"), Pick("most_complex", "006"),
    ]
    assert [p.category for p in picks] == list(COMPARE_CATEGORIES)


def test_a_category_with_no_match_takes_the_next_most_complex_unit():
    plan = plan_of(
        unit("001", 0, 2, characters=who("mascot")),                                           # 2
        unit("002", 2, 4, characters=who("historian"), props=["a"]),                            # 3
        unit("003", 4, 6, characters=who("caveman_group")),                                     # 6
        unit("004", 6, 8, characters=who("mascot"), props=["a", "b"]),                          # 4
        unit("005", 8, 10, characters=who("mascot"), props=["a", "b", "c", "d"]),               # 6
        unit("006", 10, 12, characters=who("mascot")),                                          # 2
        unit("007", 12, 14, characters=who("mascot")),                                          # 2
    )
    picks = pick_compare_units(plan, FIGURES)
    # no night/fire unit: the most complex not yet picked stands in, the earlier of the two 6s
    assert picks[2] == Pick("night_or_fire", "003", filled=True)
    assert picks[3] == Pick("metaphor", "005", filled=True)
    assert picks[4] == Pick("split_part", "004", filled=True)
    assert picks[5] == Pick("most_complex", "006")


def test_picks_stop_when_the_units_run_out():
    plan = plan_of(unit("001", 0, 2, characters=who("mascot")), unit("002", 2, 4, characters=who("historian")))
    assert [p.unit_id for p in pick_compare_units(plan, FIGURES)] == ["001", "002"]


def test_the_night_pick_is_the_most_complex_night_unit_not_the_first():
    plan = plan_of(
        unit("001", 0, 2, characters=who("mascot")),                                    # 2
        unit("002", 2, 4, characters=who("historian")),                                 # 2
        unit("003", 4, 6, time_of_day="night"),                                         # 0
        unit("004", 6, 8, time_of_day="night", characters=who("caveman_group")),        # 6
    )
    assert pick_compare_units(plan, FIGURES)[2] == Pick("night_or_fire", "004")
