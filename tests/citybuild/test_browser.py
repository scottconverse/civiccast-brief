"""The shared Chrome opens headed and minimized, so reads do not pop a window up."""

from __future__ import annotations

import sys
import types

from civiccast_brief.citybuild import browser


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
