import os
import sys
import subprocess
import threading
import webbrowser

# Auto-re-execute in virtual environment if available and not already inside it
_project_root = os.path.dirname(os.path.abspath(__file__))
_venv_dir = os.path.join(_project_root, ".venv")
_in_project_venv = sys.executable.lower().startswith(_venv_dir.lower() + os.sep)
if not _in_project_venv and os.path.exists(_venv_dir):
    _venv_exe = (
        os.path.join(_venv_dir, "Scripts", "pythonw.exe")
        if sys.platform == "win32"
        else os.path.join(_venv_dir, "bin", "python")
    )
    if sys.platform == "win32" and not os.path.exists(_venv_exe):
        _venv_exe = os.path.join(_venv_dir, "Scripts", "python.exe")
        
    if os.path.exists(_venv_exe):
        if sys.platform == "win32" and _venv_exe.endswith("pythonw.exe"):
            subprocess.Popen([_venv_exe, __file__] + sys.argv[1:])
            sys.exit(0)
        else:
            try:
                sys.exit(subprocess.call([_venv_exe, __file__] + sys.argv[1:]))
            except KeyboardInterrupt:
                sys.exit(0)



from PIL import Image, ImageDraw


# launcher.py is a pystray-based system tray — Windows-only.
# On macOS / Linux, suggest the cross-platform CLI launcher instead
# rather than failing with an ImportError. Track (b) replaces
# launcher.py with a Tauri shell, but until then users on those
# hosts need a clear pointer.
if sys.platform != "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("❌  launcher.py is Windows-only (pystray tray app).")
    print("    On macOS / Linux use instead:")
    print("        python launch.py run            # run the bot")
    print("        python launch.py check          # preflight diagnostic")
    print("    Track (b) replaces this with a cross-platform Tauri shell.")
    sys.exit(0)

try:
    import pystray
    from pystray import MenuItem as item
except ImportError:
    print("Please install pystray: pip install pystray pillow")
    sys.exit(1)

# Global process reference
bot_process = None
current_profile = "Stopped"

def create_image(status_color="blue"):
    """Create a simple generated icon. Color changes based on status."""
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), "black")
    dc = ImageDraw.Draw(image)
    dc.rectangle(
        (width // 4, height // 4, width * 3 // 4, height * 3 // 4),
        fill=status_color)
    return image

def update_icon_state(icon, profile):
    global current_profile
    current_profile = profile
    if profile == "Stopped":
        icon.icon = create_image("gray")
        icon.title = "Genesis: Stopped"
    else:
        icon.icon = create_image("green")
        icon.title = f"Genesis: Running ({profile})"

def start_bot(icon, profile):
    global bot_process
    
    # Ensure any running bot is stopped
    if bot_process is not None:
        bot_process.terminate()
        bot_process.wait()
    
    env = os.environ.copy()
    env["GENESIS_PROFILE"] = profile
    
    # Use python executable from the current environment
    python_exe = sys.executable
    
    # Run main.py in a new process without a console window
    startupinfo = None
    creationflags = 0
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    
    bot_process = subprocess.Popen(
        [python_exe, "main.py"],
        env=env,
        startupinfo=startupinfo,
        creationflags=creationflags
    )
    
    update_icon_state(icon, profile)

def stop_bot(icon, item=None):
    global bot_process
    if bot_process is not None:
        bot_process.terminate()
        bot_process.wait()
        bot_process = None
    update_icon_state(icon, "Stopped")

def on_start_default(icon, item):
    start_bot(icon, "default")

def on_start_scalper(icon, item):
    start_bot(icon, "scalper")

def on_start_breakout(icon, item):
    start_bot(icon, "breakout")

def open_dashboard(icon, item):
    # Depending on the profile, the port might be different, but we'll default to 8000
    port = 8000
    if current_profile == "scalper": port = 8001
    if current_profile == "breakout": port = 8002
    webbrowser.open(f"http://127.0.0.1:{port}")

def quit_app(icon, item):
    stop_bot(icon)
    icon.stop()

def setup_tray():
    image = create_image("gray")
    
    menu = pystray.Menu(
        item('Start > Default Profile', on_start_default),
        item('Start > Scalper Profile', on_start_scalper),
        item('Start > Breakout Profile', on_start_breakout),
        pystray.Menu.SEPARATOR,
        item('Stop Bot', stop_bot),
        item('Open Dashboard', open_dashboard),
        pystray.Menu.SEPARATOR,
        item('Quit', quit_app)
    )
    
    icon = pystray.Icon("Genesis", image, "Genesis: Stopped", menu)
    icon.run()

if __name__ == "__main__":
    setup_tray()
