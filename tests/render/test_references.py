import hashlib
from datetime import date

import pytest
from PIL import Image

from stickman.config_files import MascotConfig
from stickman.library import LibraryCharacter
from stickman.plan.models import CastMember, MascotEntry
from stickman.render.references import ReferenceFiles, reference_paths
from stickman.settings import ConfigError

MASCOT = MascotConfig(name="Everyman", identity="a stickman", sheet="library/mascot/sheet_v1.png",
                      ref="library/mascot/ref_v1.png", seed=7, style_version=1)
CAVEMEN = LibraryCharacter(id="cavemen_v1", name="Caveman group", figures=3, description="three cavemen",
                           style_version=1, model="@cf/black-forest-labs/flux-2-klein-9b", sheet="sheet.png",
                           ref="ref.png", approved=date(2026, 9, 22))
CAST = [MascotEntry(id="mascot"), CastMember(id="caveman_group", name="Caveman group", figures=3,
                                              description="three cavemen", library_ref="cavemen_v1")]


def png(path, size=(512, 384), kind="PNG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format=kind)
    return path


def test_slot_0_is_the_anchor_then_one_file_per_slot(tmp_path):
    paths = reference_paths(tmp_path, ["mascot", "caveman_group"], style_version=1, mascot=MASCOT, cast=CAST,
                            library=[CAVEMEN])
    assert paths == [
        tmp_path / "library" / "style" / "anchor_v1_ref.png",
        tmp_path / "library" / "mascot" / "ref_v1.png",
        tmp_path / "library" / "characters" / "cavemen_v1" / "ref.png",
    ]


def test_without_an_anchor_nothing_is_sent(tmp_path):
    assert reference_paths(tmp_path, None, style_version=1, mascot=MASCOT, cast=CAST, library=[CAVEMEN]) == []


def test_a_reference_is_read_once_and_named_by_path_and_hash(tmp_path):
    path = png(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    files = ReferenceFiles(tmp_path, ref_max_side=512)
    ref = files.load(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert (ref.path, ref.sha256, ref.size, ref.data) == ("library/style/anchor_v1_ref.png", f"sha256:{digest}",
                                                          (512, 384), path.read_bytes())
    assert ref.label == f"library/style/anchor_v1_ref.png#sha256:{digest}"
    assert files.load(path) is ref


def test_the_stock_images_are_never_sent(tmp_path):
    path = png(tmp_path / "style_refs" / "stock1.png")
    with pytest.raises(ConfigError, match="never sent"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(path)


@pytest.mark.parametrize(
    ("size", "kind", "message"),
    [((600, 400), "PNG", "larger than image.ref_max_side"), ((512, 384), "JPEG", "must be PNG")],
)
def test_a_reference_copy_must_be_a_small_png(tmp_path, size, kind, message):
    path = png(tmp_path / "library" / "style" / "anchor_v1_ref.png", size, kind)
    with pytest.raises(ConfigError, match=message):
        ReferenceFiles(tmp_path, ref_max_side=512).load(path)


def test_a_missing_reference_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="can't read"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(tmp_path / "library" / "style" / "anchor_v1_ref.png")
