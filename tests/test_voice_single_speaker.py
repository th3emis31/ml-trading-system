"""The morning briefing must not be spoken twice in two different voices.

The browser reads the briefing aloud in the voice the page selected. The endpoint also spoke it through the PC's own
Windows voice, so the owner heard two voices a fraction of a second apart saying the same words. The server now speaks
only when a caller explicitly asks for it.
"""
import app as app_module


class FakeEngine:
    def __init__(self):
        self.engine = object()
        self.said = []

    def speak(self, text, wait=False, pause_listening=True):
        self.said.append(text)


def test_the_server_stays_silent_unless_a_caller_asks_to_hear_it(monkeypatch):
    import jarvis_voice_response

    engine = FakeEngine()
    monkeypatch.setattr(jarvis_voice_response, "get_voice_engine", lambda: engine)
    client = app_module.app.test_client()

    quiet = client.get("/api/morning-briefing").get_json()
    assert quiet["spoken_by_server"] is False, "the browser is speaking this; the PC must not speak it too"
    assert engine.said == [] and quiet["briefing"], "the text still comes back for the browser to read"

    aloud = client.get("/api/morning-briefing?speak=1").get_json()
    assert aloud["spoken_by_server"] is True and engine.said == [aloud["briefing"]], "a caller with no browser can ask"
