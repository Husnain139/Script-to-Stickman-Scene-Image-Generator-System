from stickman.bootstrap.prompts import anchor_expected, anchor_prompt, mascot_expected, mascot_prompt
from stickman.config_files import load_mascot, load_style


def test_the_anchor_prompt_is_style_scene_and_strict_clause(tmp_path):
    style = load_style(tmp_path)
    prompt = anchor_prompt(style, "two_figures")
    assert prompt.startswith(style.style_text.strip() + "\n\nScene: two stickmen talking; the taller one gestures")
    assert "A light hatched ground shadow.\n\n" in prompt
    assert prompt.endswith(style.strict_clause.strip())
    assert "Reference images" not in prompt  # the anchor is made with no reference images


def test_the_one_figure_anchor_is_one_stickman_waving(tmp_path):
    prompt = anchor_prompt(load_style(tmp_path), "one_figure")
    assert "Scene: one stickman standing and waving with an open palm." in prompt
    assert anchor_expected("one_figure").figures == 1
    assert anchor_expected("two_figures").figures == 2


def test_the_mascot_sheet_prompt_describes_one_full_body_front_view(tmp_path):
    style, mascot = load_style(tmp_path), load_mascot(tmp_path)
    prompt = mascot_prompt(style, mascot)
    assert (f"Character sheet: a single full-body front view of {mascot.identity.strip()}, wearing "
            f"{mascot.default_outfit.strip()}, standing in a neutral pose, centred, arms relaxed.") in prompt
    assert "Reference images: image 0 shows the drawing style only" in prompt
    assert prompt.endswith(style.strict_clause.strip())


def test_a_mascot_without_an_outfit_is_described_by_identity_alone(tmp_path):
    mascot = load_mascot(tmp_path).model_copy(update={"default_outfit": ""})
    prompt = mascot_prompt(load_style(tmp_path), mascot)
    assert f"front view of {mascot.identity.strip()}, standing in a neutral pose" in prompt


def test_qc_expects_one_figure_on_the_mascot_sheet(tmp_path):
    mascot = load_mascot(tmp_path)
    expected = mascot_expected(mascot)
    assert (expected.figures, expected.fewest, expected.cast) == (1, 1, "Everyman: 1")
    assert expected.visual_idea.startswith("a character sheet: one full-body front view of the main character")
