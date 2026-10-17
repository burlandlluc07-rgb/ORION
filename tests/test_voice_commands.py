from orion.brain import router
from orion.voice.commands import STOP, build_catalog, lookup, number_words, speakable_app


def test_every_spoken_command_reaches_the_local_fast_path():
    cat = build_catalog(["Spotify", "Visual Studio Code", "7-Zip File Manager", "Uninstall Foo"], smart_home=True)
    assert len(cat) > 1000
    missing = [(s, c) for s, c in cat.items() if c != STOP and router.match(c) is None]
    assert not missing, missing[:20]


def test_canonical_values():
    cat = build_catalog(["Spotify"])
    assert cat["set the volume to thirty five"] == "volume 35"
    assert router.match(cat["set the volume to thirty five"]).args == {"level": 35}
    assert cat["set a timer for one minute"] == "timer 1 minute"
    assert router.match("timer 1 minute").tool == "set_timer"
    assert cat["open spotify"] == "open spotify" and cat["close spotify"] == "close spotify"
    assert cat["open my downloads"] == "open downloads"
    assert cat["stop talking"] == STOP


def test_lookup_strips_wake_phrase_and_wildcard():
    cat = build_catalog([])
    wake = ["Hey Orion", "Friday"]
    assert lookup(cat, "Hey Orion pause the music", wake) == "pause"
    assert lookup(cat, "hey orion, set the volume to twenty", wake) == "volume 20"
    assert lookup(cat, "Friday next song", wake) == "next song"
    assert lookup(cat, "Hey Orion ...", wake) is None
    assert lookup(cat, "Hey Orion what's the weather in Paris", wake) is None
    assert lookup(cat, "pause the music") == "pause"


def test_number_words_and_app_names():
    assert [number_words(n) for n in (0, 7, 21, 40, 99, 100)] == ["zero", "seven", "twenty one", "forty", "ninety nine",
                                                                    "one hundred"]
    assert speakable_app("Microsoft Edge") == "microsoft edge"
    assert speakable_app("Notepad++") == "notepad plus plus"
    assert speakable_app("Uninstall Foo") is None and speakable_app("7z") is None
