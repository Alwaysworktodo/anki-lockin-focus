"""Focused Chromium regressions for the actual reviewer JS and CSS.

Requires the optional Python Playwright package and its Chromium installation.
No Anki server or external network is used; all requests are fulfilled locally.
"""
from pathlib import Path
import json

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def reviewer(browser):
    pages = []

    def open_page(pomo=None, dark=False):
        page = browser.new_page(viewport={"width": 1000, "height": 800},
                                reduced_motion="reduce", color_scheme="dark" if dark else "light")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            url = route.request.url
            if url == "https://lockin.test/":
                route.fulfill(content_type="text/html", body="<html><head></head><body><div id='qa'>Card</div></body></html>")
            elif url == "https://lockin.test/web/cats/cat-01.png":
                route.fulfill(content_type="image/png", path=str(ROOT / "web/cats/cat-01.png"))
            else:
                route.fulfill(status=404, body="missing")

        page.route("**/*", serve)
        page.goto("https://lockin.test/")
        config = json.loads((ROOT / "config.json").read_text())
        config.update(ui_language="en", pomodoro_enable_sound=False,
                      speed_focus_volume=0, speed_focus_alert_delay=0,
                      custom_answer_buttons=True, hide_answer_buttons=False)
        page.evaluate("""({config, pomo, dark}) => {
          window.AnkiLockInConfig = config;
          window.AnkiLockInWebviewKind = 'main';
          window.AnkiLockInPomoState = pomo || {state:'study', pre_pause_state:'study', time_left:120};
          window.commands = [];
          window.pycmd = message => window.commands.push(message);
          if (dark) document.body.classList.add('nightMode');
        }""", {"config": config, "pomo": pomo, "dark": dark})
        page.add_style_tag(content=(ROOT / "web/lockin.css").read_text())
        page.add_script_tag(content=(ROOT / "web/lockin.js").read_text())
        page.wait_for_selector("#lockin-bar")
        pages.append((page, errors))
        return page

    yield open_page
    for page, errors in pages:
        page.close()
        assert not errors, errors


PAYLOAD = {str(i): {"rating": rating, "label": rating, "interval": "10d"}
           for i, rating in enumerate(("again", "hard", "good", "easy"), 1)}


@pytest.mark.parametrize("dark", [False, True])
def test_settings_rebuild_preserves_question_and_answer_controls(reviewer, dark):
    page = reviewer(dark=dark)
    page.evaluate("payload => { AnkiLockIn.onShowQuestion(); AnkiLockIn.updateButtons(payload); }", PAYLOAD)
    page.evaluate("""payload => {
      AnkiLockIn.updateConfig({...AnkiLockInConfig, bar_position:'top'});
      AnkiLockIn.updateButtons(payload); // exactly the backend settings replay
    }""", PAYLOAD)
    assert page.locator("#lockin-show-ans-btn").count() == 1
    assert page.locator(".lockin-ans-btn[data-ease]").count() == 0
    page.locator("#lockin-show-ans-btn").click()
    assert page.evaluate("commands.includes('ans')")
    page.evaluate("AnkiLockIn.onShowAnswer()")
    page.evaluate("""payload => {
      AnkiLockIn.updateConfig({...AnkiLockInConfig, bar_position:'bottom'});
      AnkiLockIn.updateButtons(payload);
    }""", PAYLOAD)
    assert page.locator("#lockin-show-ans-btn").count() == 0
    assert page.locator(".lockin-ans-btn[data-ease]").count() == 4
    assert page.locator(".lockin-ans-interval").all_text_contents() == ["10d"] * 4
    page.locator('[data-ease="3"]').click()
    assert page.evaluate("commands.includes('ease3')")


def test_toast_real_id_view_and_broken_image_fallback(reviewer):
    page = reviewer()
    cat_id = json.loads((ROOT / "collectibles/catalog.json").read_text())["cats"][0]["id"]
    event = {"id": cat_id, "localized_name": "Popcat", "localized_reason": "Milestone",
             "asset_url": "https://lockin.test/web/cats/cat-01.png"}
    page.evaluate("event => AnkiLockIn.showCatUnlock([event])", event)
    page.wait_for_function("document.querySelector('.lockin-cat-toast-thumb img')?.naturalWidth > 0")
    assert page.locator(".lockin-cat-toast-thumb-fallback").evaluate("el => getComputedStyle(el).display") == "none"
    page.locator(".lockin-cat-toast-view").click()
    assert page.evaluate("commands", None)[-1] == "lockin-open-cat-collection:" + cat_id
    page.wait_for_selector(".lockin-cat-toast", state="detached")
    event["asset_url"] = "https://lockin.test/missing.png"
    page.evaluate("event => AnkiLockIn.showCatUnlock([event])", event)
    page.wait_for_function("document.querySelector('.lockin-cat-toast-thumb img')?.hidden")
    assert page.locator(".lockin-cat-toast-thumb-fallback").is_visible()


@pytest.mark.parametrize("dark", [False, True])
def test_paused_break_resume_pause_reset_are_reachable(reviewer, dark):
    page = reviewer({"state": "paused", "pre_pause_state": "break", "time_left": 240}, dark=dark)
    overlay = page.locator("#lockin-break-overlay")
    assert "overlay-open" in overlay.get_attribute("class")
    resume = page.locator("#lockin-break-resume-btn")
    assert resume.is_visible()
    resume.click()  # real pointer hit test, not dispatchEvent
    assert "overlay-open" in overlay.get_attribute("class")
    page.locator("#lockin-break-pause-btn").click()
    assert "overlay-open" in overlay.get_attribute("class")
    resume.focus()
    page.keyboard.press("Enter")
    assert page.locator("#lockin-break-pause-btn").is_visible()
    page.locator("#lockin-break-reset-btn").focus()
    page.keyboard.press("Enter")
    assert "overlay-open" not in (overlay.get_attribute("class") or "")
    states = page.evaluate("commands.filter(c => c.startsWith('lockin-pomo-sync:')).map(c => JSON.parse(c.split(':').slice(1).join(':')))")
    assert [state["state"] for state in states[-4:]] == ["break", "paused", "break", "paused"]
    assert states[-1]["pre_pause_state"] == "study"
