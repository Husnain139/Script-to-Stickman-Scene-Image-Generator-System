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


def test_sheet_buttons_wait_while_the_page_makes_candidates():
    sheets = SCRIPT[SCRIPT.index("function sheetsView"):SCRIPT.index("// --- Tests and Gallery ---")]
    assert 'data.job.kind === "candidates"' in sheets
    assert "disabled: c.previous_anchor || making" in sheets  # Approve
    assert '"Spends neurons: two images and their checks", disabled: making' in sheets  # Make 2 more


def test_live_reload_keeps_the_hint_being_typed_and_the_focused_control():
    assert "ui.hints[unit.id]" in SCRIPT and "oninput:" in SCRIPT  # a replan hint survives a redraw
    for key in ('"data-key": `queue:${unit.id}`', '"data-key": `hint:${unit.id}`', '"data-key": `detail:${unit.id}:approve`'):
        assert key in SCRIPT, key
    for view in ("plan", "sheets", "tests", "gallery"):
        assert f'data-key="tab:{view}"' in PAGE
    render = SCRIPT[SCRIPT.index("function render()"):SCRIPT.index("function problemsPanel")]
    assert "restoreFocus(" in render
    assert "ui.scrolledTo" in render  # the queue scrolls only when the focused unit changed
    assert "versionsSignature(" in render  # the history dialog is redrawn only when its versions changed


def test_the_event_stream_refetches_when_it_reconnects():
    assert 'addEventListener("open"' in SCRIPT


def test_dialogs_keep_their_unit_and_give_focus_back_and_timeline_buttons_stay_buttons():
    save = SCRIPT[SCRIPT.index("async function savePrompt"):SCRIPT.index("// --- keyboard")]
    assert "ui.overlayUnit" in save and "focusedUnit()" not in save
    close = SCRIPT[SCRIPT.index("function closeOverlay"):SCRIPT.index("function renderOverlay")]
    assert "ui.opener" in close and "restoreFocus(" in close
    assert not re.search(r'h\("button", \{\s*type: "button", role: "listitem"', SCRIPT)
    assert 'h("div", { role: "listitem"' in SCRIPT


def test_unit_ids_are_encoded_in_api_urls():
    assert "encodeURIComponent" in SCRIPT
    assert not re.search(r"/api/units/\$\{unit\.id\}", SCRIPT)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_redraw_helpers():
    code = (
        f"const m = require({json.dumps(str(STATIC / 'app.js'))});"
        "const unit = {id: '006a', current_version: 2, approved_version: null, versions: [{v: 1, qc: null}, {v: 2, qc: {passed: true}}]};"
        "const same = {...unit, visual_idea: 'changed text', versions: unit.versions.map((v) => ({...v, url: '/x'}))};"
        "const more = {...unit, versions: [...unit.versions, {v: 3, qc: null}]};"
        "console.log(JSON.stringify([m.versionsSignature(unit) === m.versionsSignature(same),"
        " m.versionsSignature(unit) === m.versionsSignature(more), m.versionsSignature(null),"
        " m.keySelector('queue:006a'), m.keySelector('a\"b'), m.unitPath('006a', 'approve'), m.unitPath('a/b?c', 'replan')]));"
    )
    result = subprocess.run([NODE, "-e", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        True, False, "", '[data-key="queue:006a"]', '[data-key="a\\"b"]', "/api/units/006a/approve",
        "/api/units/a%2Fb%3Fc/replan",
    ]
