"""The morning briefing must be spoken by exactly one voice.

It used to be spoken twice: by the PC's own Windows voice from the endpoint, and again by the browser in the voice the
page had selected, a fraction of a second apart. The PC voice is the one that reliably plays - a browser stays silent
until the page has been interacted with - so the endpoint keeps speaking, reports that it did, and the page stays quiet
when it sees spoken_by_server. ?speak=0 silences the PC side for a caller that wants the browser to read it instead.
"""
import app as app_module


class FakeEngine:
    """Stands in for the PC voice engine: the real one queues text for its worker thread, so the queue is what says
    whether something would have been spoken aloud."""

    def __init__(self):
        self.engine = object()
        self.said = []
        self.queue = self
        self.speaking = False

    def put(self, text):            # the real engine's speak() hands text to a queue
        self.said.append(text)

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


def test_one_setting_decides_who_speaks_and_the_pc_engine_obeys_it(tmp_path, monkeypatch):
    """Patching endpoint by endpoint did not hold: the scheduler, the learning bridge and the briefing each spoke on
    their own. They all go through this engine, so the gate lives here - and every page checks the same setting."""
    import jarvis_voice_response as voice

    monkeypatch.setattr(voice, "SPEAKER_CONFIG_PATH", tmp_path / "voice_config.json")
    assert voice.speaker_choice() == "pc", "with no file the PC speaks: it is the side that always plays"

    engine = FakeEngine()
    monkeypatch.setattr(voice, "get_voice_engine", lambda: engine)
    voice.set_speaker_choice("browser")
    assert voice.speaker_choice() == "browser"
    voice.VoiceResponseEngine.speak(engine, "this must not be said aloud")
    assert engine.said == [], "the page is speaking; the PC engine stays silent whoever calls it"

    voice.set_speaker_choice("pc")
    voice.VoiceResponseEngine.speak(engine, "this one is spoken")
    assert engine.said, "and it speaks again when it is the chosen side"

    try:
        voice.set_speaker_choice("both")
    except ValueError as exc:
        assert "pc" in str(exc) and "browser" in str(exc)
    else:
        raise AssertionError("'both' is exactly the state this fix exists to prevent")


def test_the_endpoint_reports_and_switches_the_speaker(tmp_path, monkeypatch):
    import jarvis_voice_response as voice

    monkeypatch.setattr(voice, "SPEAKER_CONFIG_PATH", tmp_path / "voice_config.json")
    client = app_module.app.test_client()
    body = client.get("/api/voice/speaker").get_json()
    assert body["speaker"] == "pc" and body["pc_speaks"] is True and body["browser_speaks"] is False

    switched = client.post("/api/voice/speaker", json={"speaker": "browser"}).get_json()
    assert switched["speaker"] == "browser" and switched["browser_speaks"] is True
    refused = client.post("/api/voice/speaker", json={"speaker": "both"})
    assert refused.status_code == 400 and refused.get_json()["speaker"] == "browser", "the setting is left unchanged"
