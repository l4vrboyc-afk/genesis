"""Rewrite launch_gui.py with proper formatting."""
import subprocess

# Generate the content using Python so CRLF/newlines are handled correctly
result = subprocess.run(
    [
        sys.executable,
        "-c",
        r"""
lines = []
lines.append('"""Genesis GUI Launcher (launch_gui.py)"""')
lines.append('')
lines.append('Drop Genesis.exe next to main.py and double-click. The launcher:')
lines.append('')
lines.append('1. Locates the project root (sibling of the .exe / __file__).')
lines.append('2. Runs scripts/check_setup.py as a preflight check.')
lines.append('3. Opens the profile picker (gui/profile_picker.html) in a pywebview native window.')
lines.append('4. On profile selection: kills any existing bot, sets GENESIS_PROFILE,')
lines.append('   spawns `main.py` via the venv Python, waits for the profile-specific port,')
lines.append('   then navigates the webview to the dashboard.')
lines.append('5. On window close: kills the bot process gracefully.')
lines.append('6. If the bot crashes on its own: navigates back to the picker.')
lines.append('')
lines.append('Run standalone::')
lines.append('')
lines.append('    .venv\\Scripts\\python.exe launch_gui.py')
lines.append('')
lines.append('PyInstaller note: gui/profile_picker.html must be collected into the bundle.')
lines.append('Add to your .spec (or pass via --add-data)::')
lines.append('')
lines.append('    datas=[("gui/profile_picker.html", "gui")],')
lines.append('"""')
lines.append('')
out = open(sys.argv[1], "w", encoding="utf-8")
out.write("\n".join(lines))
out.close()
""",
        r"C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis\launch_gui.py",
    ]
)
