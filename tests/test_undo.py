from pathlib import Path

import pytest

from orion.tools import files
from orion.tools.registry import ToolError


@pytest.fixture
def sandbox(engine, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(files, "home", lambda: home)
    monkeypatch.setattr("orion.tools.platform.home", lambda: home)
    trashed = []
    monkeypatch.setattr("send2trash.send2trash", lambda p: (trashed.append(p), Path(p).unlink()))
    return home, trashed


def test_undo_overwrite_create_and_move(sandbox):
    home, trashed = sandbox
    f = home / "notes.txt"
    files.write_file(str(f), "version 1")
    files.write_file(str(f), "version 2")
    assert files.undo_last_change().startswith("Restored")
    assert f.read_text() == "version 1"
    files.move_or_copy(str(f), str(home / "moved.txt"), "move")
    assert not f.exists()
    assert "back" in files.undo_last_change()
    assert f.read_text() == "version 1"
    files.undo_last_change()  # undo the original creation → recycled
    assert trashed == [str(f)]
    with pytest.raises(ToolError):
        files.undo_last_change()


def test_recent_changes_lists_newest_first(sandbox):
    home, _ = sandbox
    files.create_folder(str(home / "Projects"))
    files.write_file(str(home / "Projects" / "a.md"), "# hi")
    changes = files.recent_file_changes()
    assert changes[0]["change"].startswith("Created") and "a.md" in changes[0]["change"]
    assert "folder" in changes[1]["change"]
