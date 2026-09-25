import pytest
from PIL import Image

from stickman.render.images import (
    HISTORY_DIR,
    ImageDecodeError,
    decode_image,
    encode_png,
    history_files,
    history_name,
    image_stem,
    read_metadata,
    sniff,
)


def test_formats_are_told_apart_by_their_magic_bytes(jpeg):
    assert sniff(jpeg) == "jpeg"
    assert sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "png"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff(b"<html>") is None


def test_an_api_jpeg_is_saved_as_png_with_its_metadata(tmp_path, jpeg):
    metadata = {"v": 1, "prompt_sent": "Scene: a stickman — waving."}
    data = encode_png(decode_image(jpeg), metadata)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    path = tmp_path / "001_v1.png"
    path.write_bytes(data)
    assert read_metadata(path) == metadata
    with Image.open(path) as saved:
        assert saved.size == (64, 36)


@pytest.mark.parametrize("data", [b"<html>oops</html>", b"\xff\xd8\xff" + b"\x00" * 20])
def test_bytes_that_are_not_an_image_raise(data):
    with pytest.raises(ImageDecodeError):
        decode_image(data)


def test_a_truncated_jpeg_raises(jpeg):
    with pytest.raises(ImageDecodeError):
        decode_image(jpeg[: len(jpeg) // 2])


def test_a_file_without_metadata_has_none(tmp_path):
    plain = tmp_path / "plain.png"
    Image.new("RGB", (4, 4), "white").save(plain, format="PNG")
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"junk")
    assert read_metadata(plain) is None
    assert read_metadata(junk) is None
    assert read_metadata(tmp_path / "missing.png") is None


@pytest.mark.parametrize(
    ("start", "stem"),
    [(0.0, "001_00-00.0"), (21.0, "001_00-21.0"), (24.294, "001_00-24.3"), (59.96, "001_01-00.0"), (133.852, "001_02-13.9")],
)
def test_image_file_names_carry_the_start_time(start, stem):
    assert image_stem("001", start) == stem


def test_history_files_are_listed_by_unit_and_version(tmp_path):
    assert history_name("006a", 3) == "006a_v3.png"
    folder = tmp_path / HISTORY_DIR
    folder.mkdir(parents=True)
    for name in ("006a_v1.png", "006a_v3.png", "001_v2.png", "006a_v2.png.tmp", "notes.txt", "006a_v0.png"):
        (folder / name).write_bytes(b"x")
    found = history_files(tmp_path)
    assert {unit: sorted(files) for unit, files in found.items()} == {"006a": [1, 3], "001": [2]}
    assert found["006a"][3] == folder / "006a_v3.png"
    assert history_files(tmp_path / "no-project") == {}
