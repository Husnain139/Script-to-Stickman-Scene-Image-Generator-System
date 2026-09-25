from stickman.config_files import StyleConfig, load_mascot
from stickman.plan.cast import CastInfo, cast_infos
from stickman.plan.models import CastMember, CharacterRef, MascotEntry, UnitDesign
from stickman.prompt.builder import ReferenceAvailability, build_prompt, reference_slots

STYLE = StyleConfig(style_version=1, style_text="STYLE TEXT.", strict_clause="NO TEXT EVER.")
MASCOT_DESCRIPTION = (
    "the main character with three hair strokes, usually wearing a tie "
    "(these may be hidden or left out when the scene calls for it)"
)
CAST = {
    "mascot": CastInfo("mascot", "Everyman", 1, MASCOT_DESCRIPTION),
    "caveman_group": CastInfo("caveman_group", "Caveman group", 3, "three cavemen in fur loincloths."),
    "historian": CastInfo("historian", "Historian", 1, "a stickman historian with round glasses"),
}


def ref(name):
    return CharacterRef(ref=name, action="standing", emotion="calm")


def design(**overrides):
    fields = dict(
        visual_idea="Cavemen tell stories by the fire.",
        visual_type="literal",
        shot="medium",
        time_of_day="night",
        characters=[
            CharacterRef(ref="caveman_group", action="sitting around the fire", emotion="happy"),
            CharacterRef(ref="mascot", action="watching from the side", emotion="amazed"),
        ],
        setting=["flat ground line", "one small rock"],
        props=["campfire with flame lines"],
        composition="fire centred, figures around it",
        energy_marks=["motion"],
    )
    fields.update(overrides)
    return UnitDesign(**fields)


def test_the_prompt_follows_the_template():
    assert build_prompt(design(), style=STYLE, cast=CAST) == (
        "STYLE TEXT.\n"
        "\n"
        "Scene: Cavemen tell stories by the fire.\n"
        "Characters:\n"
        "- three cavemen in fur loincloths — sitting around the fire, with a happy expression.\n"
        f"- {MASCOT_DESCRIPTION} — watching from the side, with an amazed expression.\n"
        "Setting: flat ground line, one small rock.\n"
        "Props: campfire with flame lines.\n"
        "A small crescent moon and a few stars; the background stays white.\n"
        "Composition: medium shot. Fire centred, figures around it.\n"
        "Add simple cartoon motion lines for energy.\n"
        "\n"
        "NO TEXT EVER."
    )


def test_a_day_scene_with_nothing_optional():
    prompt = build_prompt(
        design(time_of_day="day", setting=[], props=[], characters=[], energy_marks=[]), style=STYLE, cast=CAST
    )
    assert "Characters: none.\n" in prompt
    assert "Setting: minimal, just a simple ground line.\n" in prompt
    assert "Props: none.\n" in prompt
    assert "A small sun in an upper corner.\n" in prompt
    assert "energy" not in prompt


def test_unspecified_time_adds_no_sky_line():
    prompt = build_prompt(design(time_of_day="unspecified"), style=STYLE, cast=CAST)
    assert "sun" not in prompt and "moon" not in prompt


def test_energy_marks_are_joined_in_words():
    prompt = build_prompt(design(energy_marks=["motion", "surprise", "wind"]), style=STYLE, cast=CAST)
    assert "Add simple cartoon motion, surprise and wind lines for energy." in prompt


def test_the_strict_clause_is_always_last():
    for references in (None, [], ["mascot"]):
        assert build_prompt(design(), style=STYLE, cast=CAST, references=references).endswith("\n\nNO TEXT EVER.")


def test_the_reference_section_names_each_slot():
    prompt = build_prompt(design(), style=STYLE, cast=CAST, references=["mascot", "caveman_group"])
    assert (
        "\n\nReference images: image 0 shows the drawing style only — match its line weight and look, "
        "not its content, and do not copy its figures. Image 1 shows Everyman: draw this character with "
        "exactly the same head and hair (and the same clothing when visible), but in the pose described above. "
        "Image 2 shows Caveman group: draw this character with exactly the same head and hair (and the same "
        "clothing when visible), but in the pose described above.\n\nNO TEXT EVER."
    ) in prompt


def test_anchor_only_and_no_reference_sections():
    assert "not its content, and do not copy its figures.\n\nNO TEXT EVER." in build_prompt(
        design(), style=STYLE, cast=CAST, references=[]
    )
    assert "Reference images" not in build_prompt(design(), style=STYLE, cast=CAST, references=None)


def test_slot_1_is_the_mascot_when_present():
    characters = [ref("caveman_group"), ref("mascot"), ref("historian")]
    assert reference_slots(characters, {"mascot", "caveman_group", "historian"}) == ["mascot", "caveman_group", "historian"]


def test_members_without_a_sheet_are_text_only():
    characters = [ref("caveman_group"), ref("mascot"), ref("historian")]
    assert reference_slots(characters, {"historian"}) == ["historian"]
    assert reference_slots(characters, set()) == []


def test_availability_gives_slots_only_with_an_anchor():
    characters = [ref("mascot")]
    assert ReferenceAvailability().for_unit(characters) is None
    assert ReferenceAvailability(anchor=True, sheets=frozenset({"mascot"})).for_unit(characters) == ["mascot"]


def test_cast_infos_include_the_mascot_from_config(tmp_path):
    mascot = load_mascot(tmp_path)
    cast = [MascotEntry(id="mascot"), CastMember(id="historian", name="Historian", figures=1, description="a historian")]
    table = cast_infos(cast, mascot)
    assert table["mascot"] == CastInfo("mascot", "Everyman", 1, mascot.description)
    assert table["historian"] == CastInfo("historian", "Historian", 1, "a historian")
