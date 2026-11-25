"""The shared Chrome opens headed and minimized, so reads do not pop a window up."""

from __future__ import annotations

import sys
import threading
import types

import pytest

from civiccast_brief.citybuild import browser


@pytest.fixture(autouse=True)
def _no_shared_state():
    """No test inherits another's browser or fake."""
    browser.use(None)
    getattr(browser._shared, "__dict__", {}).pop("browser", None)
    yield
    browser.use(None)
    getattr(browser._shared, "__dict__", {}).pop("browser", None)


@pytest.fixture
def fake_chrome(monkeypatch):
    made: list[types.SimpleNamespace] = []

    class FakeChrome:
        def __init__(self) -> None:
            self.closed = False
            made.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(browser, "Chrome", FakeChrome)
    return made


def _run(work):
    box = {}
    thread = threading.Thread(target=work, args=(box,))
    thread.start()
    thread.join()
    return box


def test_each_thread_gets_its_own_chrome(fake_chrome):
    a = _run(lambda box: box.update(browser=browser.shared()))
    b = _run(lambda box: box.update(browser=browser.shared()))
    assert a["browser"] is not b["browser"]
    assert len(fake_chrome) == 2


def test_close_shared_leaves_another_threads_chrome_open(fake_chrome):
    opened: dict[str, object] = {}
    holding = threading.Event()
    done = threading.Event()

    def worker() -> None:
        opened["b"] = browser.shared()
        holding.set()
        done.wait(5)

    thread = threading.Thread(target=worker)
    thread.start()
    assert holding.wait(5)
    mine = browser.shared()
    browser.close_shared()
    assert mine.closed is True  # type: ignore[attr-defined]
    assert opened["b"].closed is False  # type: ignore[attr-defined]
    done.set()
    thread.join()


def test_use_gives_the_same_fake_to_every_thread():
    fake = types.SimpleNamespace()
    browser.use(fake)
    a = _run(lambda box: box.update(browser=browser.shared()))
    b = _run(lambda box: box.update(browser=browser.shared()))
    assert a["browser"] is fake and b["browser"] is fake
    browser.close_shared()
    assert not hasattr(fake, "closed")


class _Cdp:
    def __init__(self, sent):
        self.sent = sent

    def send(self, method, params=None):
        self.sent.append((method, params))
        return {"windowId": 7} if method == "Browser.getWindowForTarget" else {}


class _Page:
    def __init__(self, sent):
        self.context = self
        self.sent = sent

    def new_cdp_session(self, _page):
        return _Cdp(self.sent)

    def set_default_timeout(self, _ms):
        pass


def _fake_playwright(monkeypatch, launched, sent):
    class _Chromium:
        def launch(self, **kwargs):
            launched.append(kwargs)
            return types.SimpleNamespace(new_page=lambda: _Page(sent), close=lambda: None)

    started = types.SimpleNamespace(chromium=_Chromium(), stop=lambda: None)
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = Exception
    sync_api.sync_playwright = lambda: types.SimpleNamespace(start=lambda: started)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


def test_chrome_opens_headed_with_its_window_minimized(monkeypatch):
    launched, sent = [], []
    _fake_playwright(monkeypatch, launched, sent)
    browser.Chrome()._open()
    assert launched == [{"channel": "chrome", "headless": False}]
    assert (
        "Browser.setWindowBounds",
        {"windowId": 7, "bounds": {"windowState": "minimized"}},
    ) in sent
