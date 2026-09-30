from octopus.browser_agent import BrowserCapability, BrowserObservation


def test_browser_capability_records_and_resumes_without_playwright(monkeypatch, tmp_path):
    checkpoints = {}
    monkeypatch.setattr("octopus.browser_agent.tasks.step_value", lambda tid, key, default: checkpoints.get(key, default))
    monkeypatch.setattr("octopus.browser_agent.tasks.save_step", lambda tid, key, value: checkpoints.__setitem__(key, value))

    class FakePage:
        def title(self): return "Test"
        def locator(self, selector): return self
        def aria_snapshot(self, timeout=0): return "- button: Next"
        def count(self): return 1
        def select_option(self, *a): pass
        def mouse(self): return self
        def press(self, *a): pass

    class FakeBrowser:
        def __init__(self): self.current = "http://test/"; self._page = FakePage()
        def stop(self): pass
        def url(self): return self.current
        def snapshot(self, n): return "Welcome Done"
        def screenshot(self): return tmp_path / "shot.png"
        def goto(self, url): self.current = url
        def click(self, s): pass
        def type(self, s, t): pass

    fake = FakeBrowser()
    monkeypatch.setattr("octopus.browser_agent.browser.new_browser", lambda **kw: fake)
    cap = BrowserCapability(task_id=7)
    result = cap.run("complete form", lambda obs: ({"action": {"kind": "click", "selector": "#next"}, "verify": {"text": "Done"}} if not checkpoints.get("browser.completed") else None))
    assert len(result["steps"]) == 1
    assert checkpoints["browser.completed"][0]["verified"] is True
    cap.close()


def test_observation_is_bounded():
    assert BrowserObservation("u", "t", "x", "a").as_dict()["url"] == "u"
