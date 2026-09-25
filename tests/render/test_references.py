import hashlib
import io
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

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


def dir_link(link, target):
    """A directory link that needs no admin rights: a junction on Windows, a symlink elsewhere."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def stock_image(folder):
    return png(folder / "stock1.png", size=(300, 200))


def test_a_linked_stock_folder_is_refused(tmp_path):
    """style_refs/ is itself a link to the real folder, so resolve() leaves style_refs/ behind."""
    real = tmp_path / "stock_real"
    stock_image(real)
    dir_link(tmp_path / "style_refs", real)
    with pytest.raises(ConfigError, match="never sent"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(tmp_path / "style_refs" / "stock1.png")


def test_a_link_in_library_to_the_stock_folders_target_is_refused(tmp_path):
    real = tmp_path / "stock_real"
    stock_image(real)
    dir_link(tmp_path / "style_refs", real)
    dir_link(tmp_path / "library" / "linked", real)
    with pytest.raises(ConfigError, match="never sent"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(tmp_path / "library" / "linked" / "stock1.png")


def test_a_hard_link_to_a_stock_image_is_refused(tmp_path):
    stock = stock_image(tmp_path / "style_refs")
    link = tmp_path / "library" / "style" / "anchor_v1_ref.png"
    link.parent.mkdir(parents=True)
    os.link(stock, link)
    with pytest.raises(ConfigError, match="never sent"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(link)


def test_a_hard_link_is_refused_by_identity_even_after_the_stock_image_changed(tmp_path):
    stock = stock_image(tmp_path / "style_refs")
    link = tmp_path / "library" / "style" / "anchor_v1_ref.png"
    link.parent.mkdir(parents=True)
    os.link(stock, link)
    files = ReferenceFiles(tmp_path, ref_max_side=512)
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "black").save(buffer, format="PNG")
    stock.write_bytes(buffer.getvalue())  # rewritten in place: the same file, new bytes
    with pytest.raises(ConfigError, match="never sent"):
        files.load(link)


@pytest.mark.skipif(os.name != "nt", reason=r"\\?\ paths are Windows paths")
def test_a_long_path_prefix_does_not_hide_a_stock_image(tmp_path):
    stock = stock_image(tmp_path / "style_refs")
    long_path = Path(r"\\?" + "\\" + str(stock.resolve()))  # \\?\C:\...\style_refs\stock1.png
    assert long_path.is_file()
    with pytest.raises(ConfigError, match="never sent"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(long_path)


def test_a_copy_of_a_stock_image_is_refused(tmp_path):
    stock = stock_image(tmp_path / "style_refs")
    copy = tmp_path / "library" / "style" / "anchor_v1_ref.png"
    copy.parent.mkdir(parents=True)
    shutil.copyfile(stock, copy)
    with pytest.raises(ConfigError, match="never sent"):
        ReferenceFiles(tmp_path, ref_max_side=512).load(copy)


def test_other_references_are_still_read_when_style_refs_has_images(tmp_path):
    stock_image(tmp_path / "style_refs")
    path = png(tmp_path / "library" / "style" / "anchor_v1_ref.png")
    assert ReferenceFiles(tmp_path, ref_max_side=512).load(path).path == "library/style/anchor_v1_ref.png"
