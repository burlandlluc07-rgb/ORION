import pytest

from orion.brain import router


@pytest.mark.parametrize("text,tool,args", [
    ("open spotify", "open_app", {"name": "spotify"}),
    ("Hey Jarvis, open Google Chrome please", "open_app", {"name": "Google Chrome"}),
    ("launch the calculator app", "open_app", {"name": "calculator"}),
    ("open github.com", "open_website", {"url": "github.com"}),
    ("open my downloads", "open_path", {"path": "downloads"}),
    ("volume 30", "set_volume", {"level": 30}),
    ("set the volume to fifty percent", "set_volume", {"level": 50}),
    ("turn it up", "change_volume", {"delta": 10}),
    ("mute", "mute", {"state": "on"}),
    ("brightness 70", "set_brightness", {"level": 70}),
    ("next song", "media_control", {"action": "next"}),
    ("pause the music", "media_control", {"action": "play_pause"}),
    ("play bohemian rhapsody on youtube", "play_youtube", {"query": "bohemian rhapsody"}),
    ("what time is it", "get_time_and_date", {}),
    ("lock my pc", "lock_pc", {}),
    ("take a screenshot", "take_screenshot", {}),
    ("set a timer for 10 minutes", "set_timer", {"duration": "10 minutes", "label": "Timer"}),
    ("five minute timer", "set_timer", {"duration": "5 minute", "label": "Timer"}),
    ("remind me to call mum at 6pm", "set_reminder", {"message": "call mum", "when": "at 6pm"}),
    ("remind me in 20 minutes to check the oven", "set_reminder", {"message": "check the oven", "when": "in 20 minutes"}),
    ("close discord", "close_app", {"name": "discord"}),
    ("how's my pc doing", "system_status", {}),
    ("undo that", "undo_last_change", {}),
    ("google cheap flights to rome", "search_in_browser", {"query": "cheap flights to rome", "site": "google"}),
])
def test_matches(text, tool, args):
    r = router.match(text)
    assert r is not None, text
    assert r.tool == tool
    assert r.args == args


@pytest.mark.parametrize("text", [
    "open the file report.docx and summarise it",
    "open notepad and write a poem",
    "what's the capital of France",
    "close this",
    "play some relaxing music",
    "search my documents for invoices from march and total them",
    "can you explain how volume works",
    "close orion",
    "look up the population of france",
    "search for the best budget gpu",
])
def test_falls_through_to_gemini(text):
    assert router.match(text) is None
