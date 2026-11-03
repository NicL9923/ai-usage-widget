# Repository tools

- `plasma-visual-harness.sh` launches a uniquely named, fixture-fed applet and captures its QML pixels without touching the installed widget. Set `AI_USAGE_VISUAL_PRESENTATION` to `horizontal` or `vertical` to exercise a temporary panel; it is removed on exit.
- `check-macos-app.py`: launch the built app, verify loading-to-result menu bar updates through macOS accessibility, check the bundle signature, and stop only the test process. Requires a logged-in GUI session and accessibility access.
