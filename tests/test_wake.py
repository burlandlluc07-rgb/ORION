import pytest

from orion.voice import wake


def test_parse_recogniser_output():
    assert wake.parse_line("HIT:0.912:Hey Orion\r\n") == ("hit", 0.912, "Hey Orion")
    assert wake.parse_line("READY:en-US") == ("ready", 0.0, "en-US")
    assert wake.parse_line("ERR:NORECOGNIZER") == ("err", 0.0, "NORECOGNIZER")
    assert wake.parse_line("END") == ("end", 0.0, "")
    assert wake.parse_line("some noise") is None


def test_phrases_and_sensitivity():
    assert wake.clean_phrases(["Hey ORION", "hey orion", " Friday ", "x", "Hey,  Jarvis"]) == ["Hey Orion", "Friday",
                                                                                            "Hey Jarvis"]
    assert wake.threshold_for(0) > wake.threshold_for(0.5) > wake.threshold_for(1)
    assert wake.threshold_for(0.5) == pytest.approx(0.675)


def test_windows_wake_routes_hits_by_threshold(monkeypatch):
    lines = [b"READY:en-GB\n", b"HIT:0.950:Hey Orion\n", b"HIT:0.300:Hey Orion\n"]

    class FakeProc:
        def __init__(self, *a, **k):
            import io

            self.stdout = io.BytesIO(b"".join(lines))

        def poll(self):
            return 0

    monkeypatch.setattr(wake.subprocess, "Popen", FakeProc)
    hits, heard, errors = [], [], []
    def on_heard(t, c, a):
        heard.append(a)
        if len(heard) == 2:
            w._stop.set()  # one pass, no restart loop

    w = wake.WindowsWake(["Hey ORION"], 0.5, "", lambda t, c: hits.append(t), on_heard, errors.append)
    w._run()
    assert w.language == "en-GB" and hits == ["Hey Orion"] and heard == [True, False] and not errors
    with pytest.raises(ValueError):
        wake.WindowsWake([""], 0.5, "", print, print, print)
