import json
import re
import shutil
import subprocess

import pytest

from stickman.review.app import STATIC

PAGE = (STATIC / "index.html").read_text(encoding="utf-8")
SCRIPT = (STATIC / "app.js").read_text(encoding="utf-8")
STYLE = (STATIC / "app.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def test_the_page_loads_its_own_script_and_style_and_nothing_external():
    assert '<script src="/static/app.js"></script>' in PAGE
    assert '<link rel="stylesheet" href="/static/app.css">' in PAGE
    for text in (PAGE, SCRIPT, STYLE):
        assert not re.search(r"https?://", text)


def test_every_view_has_a_tab():
    for view in ("plan", "sheets", "tests", "gallery"):
        assert f'data-view="{view}"' in PAGE


def test_every_keyboard_shortcut_is_handled():
    handlers = SCRIPT[SCRIPT.index("const shortcuts = {"):]
    for key in ("a:", "r:", "e:", "h:", "j:", "k:", "ArrowRight:", "ArrowLeft:", '"1":', '"2":'):
        assert key in handlers, key
    assert '"Escape"' in SCRIPT


def test_changes_carry_the_same_origin_header_and_events_are_followed():
    assert '"X-Stickman"' in SCRIPT
    assert 'new EventSource("/api/events")' in SCRIPT
    for event in ("plan", "state", "job", "budget"):
        assert f'addEventListener("{event}"' in SCRIPT


def test_the_style_covers_dark_mode_reduced_motion_focus_and_narrow_windows():
    assert "prefers-color-scheme: dark" in STYLE
    assert "prefers-reduced-motion" in STYLE
    assert ":focus-visible" in STYLE
    assert "@media (max-width: 900px)" in STYLE


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_script_is_valid_javascript():
    result = subprocess.run([NODE, "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_times_money_and_job_names_are_written_like_the_cli_writes_them():
    code = (
        f"const m = require({json.dumps(str(STATIC / 'app.js'))});"
        "console.log(JSON.stringify([m.formatTime(0), m.formatTime(21), m.formatTime(129.44), m.money(0.00228),"
        " m.money(0.5), m.money(12.3), m.describeJob({kind: 'regenerate', unit: '006a'}),"
        " m.describeJob({kind: 'candidates', unit: 'anchor'}), m.describeJob({kind: 'replan', unit: '001'})]));"
    )
    result = subprocess.run([NODE, "-e", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "0:00.0", "0:21.0", "2:09.4", "$0.0023", "$0.50", "$12.30",
        "regenerating 006a", "making anchor candidates", "replanning 001",
    ]


def test_the_page_needs_no_inline_script_or_style_attribute_under_its_content_security_policy():
    assert "<script>" not in PAGE and " style=" not in PAGE and "onclick=" not in PAGE
    assert not re.search(r"""["']?style["']?\s*:""", SCRIPT)  # h(tag, {style: ...}) would set a blocked attribute
    assert 'setAttribute("style"' not in SCRIPT
    assert ".style.flexGrow = " in SCRIPT  # the timeline's widths go through the CSSOM, which CSP allows
