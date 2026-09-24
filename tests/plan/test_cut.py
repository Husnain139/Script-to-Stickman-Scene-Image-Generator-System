import asyncio

from stickman.ingest.models import TimedLine
from stickman.plan.cut import CutResult, check_cut, cut_candidates, cut_user_message
from stickman.settings import SplitSettings
from stickman.split.scenes import Scene

SCENE6 = Scene(
    6,
    (TimedLine(6, 21.0, 29.0, "Anthropologists studying the Zhuansi in the Kalahari recorded what people talk about by daylight versus by firelight."),),
    (6,),
)
SHORT = Scene(7, (TimedLine(7, 29.0, 31.0, "Daytime talk is logistics."),), (7,))


def test_user_message_numbers_the_words():
    assert cut_user_message([SCENE6]).splitlines() == [
        "id: 006",
        "duration: 8.0 s",
        "words: 1:Anthropologists 2:studying 3:the 4:Zhuansi 5:in 6:the 7:Kalahari 8:recorded 9:what "
        "10:people 11:talk 12:about 13:by 14:daylight 15:versus 16:by 17:firelight.",
    ]


def test_checks_catch_out_of_range_and_duplicate_candidates():
    errors = check_cut(CutResult.model_validate({"scenes": [{"id": "006", "candidates": [0, 7, 7]}]}), [SCENE6])
    assert "scenes[0].candidates: 0 is outside 1..16" in errors
    assert "scenes[0].candidates: duplicates in [0, 7, 7]" in errors
    errors = check_cut(CutResult.model_validate({"scenes": [{"id": "006", "candidates": [17]}]}), [SCENE6])
    assert errors == ["scenes[0].candidates: 17 is outside 1..16"]


def test_checks_catch_wrong_scene_ids():
    wrong = CutResult.model_validate({"scenes": [{"id": "005", "candidates": [3]}]})
    assert check_cut(wrong, [SCENE6])[0].startswith("scenes: ids must be exactly ['006']")


def test_only_split_candidates_are_sent(fake_chat, stage_runner):
    chat = fake_chat(['{"scenes": [{"id": "006", "candidates": [7, 12]}]}'])
    assert asyncio.run(cut_candidates(stage_runner(chat), [SCENE6, SHORT], SplitSettings())) == {6: [7, 12]}
    system, user = chat.calls[0]["messages"]
    assert system["content"].startswith("Each scene below is too long for one picture")
    assert "id: 006" in user["content"] and "id: 007" not in user["content"]


def test_no_candidate_scenes_means_no_call(fake_chat, stage_runner):
    chat = fake_chat([])
    assert asyncio.run(cut_candidates(stage_runner(chat), [SHORT], SplitSettings())) == {}
    assert chat.calls == []
