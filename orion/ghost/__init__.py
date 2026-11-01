"""Ghost mode: ORION works in his own see-through windows with his own cursor, and never touches yours.

The rule (Lluc, 2026-11-01): ORION must not interrupt the person using the computer AT ALL. So when he needs a
desktop app for his own work:

* the app opens as a **ghost window**: see-through, click-through, never focusable, opening where and as big as the app
  would if you opened it yourself: plain 30% glass with no outline, out of the taskbar and Alt+Tab;
* he works it with a **ghost cursor**: a drawn overlay that glides to where he acts. Your real pointer, keyboard
  focus and foreground window never change. Clicks and typing go through UI Automation and window messages;
  he reads the window with PrintWindow, which needs neither focus nor visibility;
* if an app ignores background input (Chromium/Electron content, some Store apps) he says so and asks; he never
  borrows your mouse to get round it;
* "Take over" in the notch hands a ghost window back to you as a normal window.

``core`` holds the pure logic (tested on every platform); ``win32`` and ``overlay`` are the Windows plumbing.
"""
