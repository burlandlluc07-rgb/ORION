import asyncio

from orion.voice.tts import TTS, SpeechStream, speakable


class FakeSpeaker:
    def __init__(self):
        self.played = []

    def play(self, pcm):
        self.played.append(pcm)

    def clear(self):
        pass

    async def wait_done(self):
        return None


class FakeTTS(TTS):
    async def _synth_edge(self, text):
        # Later sentences finish synthesising first, to prove playback keeps order.
        await asyncio.sleep(0.05 if text.startswith("First") else 0.01)
        return text.encode()


async def test_stream_speaks_sentences_in_order_as_they_arrive():
    spk = FakeSpeaker()
    stream = SpeechStream(FakeTTS(spk))
    for chunk in ["First sent", "ence here. Second one", " is here! ```code. block``` Third", " part."]:
        stream.push(chunk)
    await asyncio.sleep(0.1)
    assert spk.played[:2] == [b"First sentence here.", b"Second one is here!"]  # spoken before finish()
    stream.finish()
    await stream.wait()
    assert spk.played[-1] == b"I've put the code on screen. Third part."
    assert stream.spoke


async def test_interrupt_stops_stream():
    spk = FakeSpeaker()
    tts = FakeTTS(spk)
    stream = SpeechStream(tts)
    stream.push("First one. Second one. ")
    tts.stop()
    stream.finish()
    await stream.wait()
    assert spk.played == []


def test_speakable_strips_markdown_and_links():
    assert speakable("**Done** — see https://x.y/z") == "Done — see the link on screen"
