"""The morning briefing must be spoken by exactly one voice.

It used to be spoken twice: by the PC's own Windows voice from the endpoint, and again by the browser in the voice the
page had selected, a fraction of a second apart. The PC voice is the one that reliably plays - a browser stays silent
until the page has been interacted with - so the endpoint keeps speaking, reports that it did, and the page stays quiet
when it sees spoken_by_server. ?speak=0 silences the PC side for a caller that wants the browser to read it instead.
"""
import app as app_module


class FakeEngine:
    def __init__(self):
        self.engine = object()
        self.said = []

    def speak(self, text, wait=False, pause_listening=True):
        self.said.append(text)


def test_exactly_one_speaker_reads_the_briefing(monkeypatch):
    import jarvis_voice_response

    engine = FakeEngine()
    monkeypatch.setattr(jarvis_voice_response, "get_voice_engine", lambda: engine)
    client = app_module.app.test_client()

    aloud = client.get("/api/morning-briefing").get_json()
    assert aloud["spoken_by_server"] is True, "the PC voice speaks by default: it is the one that always plays"
    assert engine.said == [aloud["briefing"]] and aloud["briefing"], "and the text still comes back for the page"

    quiet = client.get("/api/morning-briefing?speak=0").get_json()
    assert quiet["spoken_by_server"] is False and len(engine.said) == 1, "?speak=0 hands the reading to the browser"
