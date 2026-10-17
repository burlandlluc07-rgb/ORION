import pytest

from orion.voice import wake


def test_parse_recogniser_output():
    assert wake.parse_line("HIT:0.912:Hey Orion\r\n") == ("hit", 0.912, "Hey Orion")
    assert wake.parse_line("READY:en-US") == ("ready", 0.0, "en-US")
    assert wake.parse_line("ERR:NORECOGNIZER") == ("err", 0.0, "NORECOGNIZER")
    assert wake.parse_line("END") == ("end", 0.0, "")
    assert wake.parse_line("NEAR:0.540:Hey Orion") == ("near", 0.54, "Hey Orion")
    assert wake.parse_line("some noise") is None


def test_phrases_and_sensitivity():
    assert wake.clean_phrases(["Hey ORION", "hey orion", " Friday ", "x", "Hey,  Jarvis"]) == ["Hey Orion", "Friday",
                                                                                            "Hey Jarvis"]
    assert wake.threshold_for(0) > wake.threshold_for(0.5) > wake.threshold_for(1)
    assert wake.threshold_for(0.5) == pytest.approx(0.675)


class FakeProc:
    """Stands in for the PowerShell speech process: canned stdout, records what's written to stdin."""

    def __init__(self, lines, replies=None):
        import io
        import threading

        self.replies = replies or {}
        self.written: list[str] = []
        self._out = list(lines)
        self._cv = threading.Condition()
        self.stdout = self
        outer = self

        class In(io.RawIOBase):
            def write(self, b):
                line = b.decode().strip()
                outer.written.append(line)
                verb, rid = line.split(" ")[:2]
                with outer._cv:
                    outer._out.append(outer.replies[verb].format(id=rid).encode())
                    outer._cv.notify()
                return len(b)

            def flush(self):
                pass

        self.stdin = In()
        self.closed = False

    def readline(self):
        with self._cv:
            while not self._out and not self.closed:
                self._cv.wait(0.05)
            return self._out.pop(0) if self._out else b""

    def poll(self):
        return 0 if self.closed else None

    def wait(self, *a):
        self.closed = True

    def kill(self):
        self.closed = True


def test_speech_service_routes_hits_by_threshold_and_commands(monkeypatch):
    lines = [b"READY:en-GB\n", b"HIT:0.950:Hey Orion ...\n", b"HIT:0.300:Hey Orion\n", b"NEAR:0.540:Hey Orion\n",
             b"HIT:0.930:Hey Orion pause the music\n"]
    procs = []
    monkeypatch.setattr(wake.subprocess, "Popen", lambda *a, **k: procs.append(FakeProc(lines)) or procs[-1])
    hits, heard, errors = [], [], []
    sp = wake.SpeechService(["Hey ORION"], {"pause the music": "pause"}, "", True, 0.5,
                            lambda p, c, cmd: hits.append((p, cmd)), lambda t, c, a: heard.append(a), errors.append)
    assert sp.start(5) and sp.language == "en-GB"
    import time

    for _ in range(100):
        if len(heard) == 4:
            break
        time.sleep(0.02)
    assert hits == [("Hey Orion", None), ("Hey Orion", "pause")]
    assert heard == [True, False, False, True] and not errors
    sp._stop.set()
    procs[0].closed = True
    with pytest.raises(ValueError):
        wake.SpeechService([""], {}, listen=True)


def test_speech_service_requests(monkeypatch, tmp_path):
    import base64
    import wave

    out = tmp_path / "say.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x01\x00" * 240)
    replies = {"REC": "REC {id} 0.910 volume thirty\n", "DIC": "DIC {id} 0.620 what is the weather like\n",
               "SAY": "SAID {id} " + str(out) + "\n"}
    proc = FakeProc([b"READY:en-US\n"], replies)
    monkeypatch.setattr(wake.subprocess, "Popen", lambda *a, **k: proc)
    sp = wake.SpeechService([], {"volume thirty": "volume 30"}, listen=False)
    assert sp.start(5)
    assert sp.match_command("C:/a b.wav") == ("volume 30", 0.91, "volume thirty")
    assert sp.dictate("C:/a.wav") == ("what is the weather like", 0.62)
    assert sp.synthesize("Hello there") == b"\x01\x00" * 240 and not out.exists()
    verb, _, path = proc.written[0].split(" ")
    assert verb == "REC" and base64.b64decode(path).decode() == "C:/a b.wav"
    assert proc.written[2].split(" ")[2:5] == ["en-GB", "male", "1"]
    sp._stop.set()
    proc.closed = True


def test_parse_reply():
    assert wake.parse_reply("REC 7 0.912 pause the music\r\n") == ("REC", "7", 0.912, "pause the music")
    assert wake.parse_reply("REC 7 -1.000 ") == ("REC", "7", -1.0, "")
    assert wake.parse_reply("DIC 3 0.500 hello world") == ("DIC", "3", 0.5, "hello world")
    assert wake.parse_reply("HIT:0.9:x") is None


def test_match_phrase_ignores_what_follows():
    phrases = ["Hey Orion", "Orion", "Friday"]
    assert wake.match_phrase("Hey Orion ... ", phrases) == "Hey Orion"
    assert wake.match_phrase("hey orion what is the weather", phrases) == "Hey Orion"
    assert wake.match_phrase("Orion", phrases) == "Orion"
    assert wake.match_phrase("Friday ...", phrases) == "Friday"
