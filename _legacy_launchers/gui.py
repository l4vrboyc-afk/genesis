import os
import sys
import subprocess
import webbrowser
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from PIL import Image, ImageTk

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
            try:
                subprocess.Popen([_venv_exe, __file__] + sys.argv[1:])
            except Exception:
                pass
            sys.exit(0)
        else:
            try:
                sys.exit(subprocess.call([_venv_exe, __file__] + sys.argv[1:]))
            except KeyboardInterrupt:
                sys.exit(0)

# Global process reference
bot_process = None
active_profile = "default"

class GenesisApp(tk.Tk):
    def __init__(self):
        super().__init__()
        
        # Set AppUserModelID on Windows so the taskbar groups this process separately with its own icon
        if sys.platform == "win32":
            try:
                import ctypes
                myappid = "genesis.tradingbot.controlcenter.1"
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            except Exception:
                pass

        self.title("Genesis Control Center")
        self.geometry("540x700")
        self.resizable(False, False)
        self.configure(bg="#0f172a") # Slate 900
        
        # Set app icon if available (.ico preferred on Windows, fallback to .png)
        self.icon_path = os.path.join(_project_root, "genesis_icon.ico")
        if os.path.exists(self.icon_path):
            try:
                self.iconbitmap(self.icon_path)
            except Exception:
                try:
                    png_path = os.path.join(_project_root, "genesis_icon.png")
                    if os.path.exists(png_path):
                        self.icon_image_raw = Image.open(png_path)
                        self.icon_img = ImageTk.PhotoImage(self.icon_image_raw)
                        self.iconphoto(False, self.icon_img)
                except Exception:
                    pass



        # Center the window on screen
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"+{x}+{y}")
        
        # Bind close event
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.setup_fonts()
        self.create_widgets()
        self.select_card("default")


    def setup_fonts(self):
        self.title_font = tkfont.Font(family="Segoe UI", size=18, weight="bold")
        self.subtitle_font = tkfont.Font(family="Segoe UI", size=10, weight="normal")
        self.header_font = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        self.body_font = tkfont.Font(family="Segoe UI", size=10, weight="normal")
        self.button_font = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.status_font = tkfont.Font(family="Segoe UI", size=11, weight="bold")

    def create_widgets(self):
        # Header Frame
        header_frame = tk.Frame(self, bg="#0f172a")
        header_frame.pack(fill="x", padx=30, pady=(30, 20))
        
        title_label = tk.Label(
            header_frame, 
            text="🚀 GENESIS CONTROL CENTER", 
            font=self.title_font, 
            fg="#f8fafc", 
            bg="#0f172a"
        )
        title_label.pack(anchor="w")
        
        subtitle_label = tk.Label(
            header_frame, 
            text="Decoupled Multi-Profile Algo-Trading System", 
            font=self.subtitle_font, 
            fg="#64748b", # Slate 500
            bg="#0f172a"
        )
        subtitle_label.pack(anchor="w", pady=(2, 0))

        # Status Panel Frame
        self.status_frame = tk.Frame(self, bg="#1e293b", bd=1, relief="flat")
        self.status_frame.pack(fill="x", padx=30, pady=10)
        
        status_inner = tk.Frame(self.status_frame, bg="#1e293b", padx=15, pady=12)
        status_inner.pack(fill="both", expand=True)
        
        status_title = tk.Label(status_inner, text="SYSTEM STATUS:", font=self.subtitle_font, fg="#94a3b8", bg="#1e293b")
        status_title.grid(row=0, column=0, sticky="w")
        
        self.status_val = tk.Label(status_inner, text="Stopped", font=self.status_font, fg="#ef4444", bg="#1e293b")
        self.status_val.grid(row=0, column=1, sticky="w", padx=(5, 0))
        
        profile_title = tk.Label(status_inner, text="ACTIVE PROFILE:", font=self.subtitle_font, fg="#94a3b8", bg="#1e293b")
        profile_title.grid(row=0, column=2, sticky="w", padx=(30, 0))
        
        self.profile_val = tk.Label(status_inner, text="None", font=self.status_font, fg="#94a3b8", bg="#1e293b")
        self.profile_val.grid(row=0, column=3, sticky="w", padx=(5, 0))

        # Profile Cards Section
        cards_title = tk.Label(self, text="Select Trading Profile:", font=self.header_font, fg="#f8fafc", bg="#0f172a")
        cards_title.pack(anchor="w", padx=30, pady=(15, 10))

        # Profile Cards Container
        self.cards = {}
        self.create_profile_card(
            profile_id="default",
            title="Genesis Default (Swing Profile)",
            desc="Market regime-aware trading (H4 filter, M15 entry).\nSwaps dynamically between Trend-Following & Mean Reversion.",
            port="8000"
        )
        
        self.create_profile_card(
            profile_id="scalper",
            title="Apex Scalper (M1/M5 Momentum)",
            desc="Hyper-active momentum execution. Disables HTF trend rules.\nExploits intraday noise using tight ATR-based risk management.",
            port="8001"
        )
        
        self.create_profile_card(
            profile_id="breakout",
            title="Session Breakout Sniper (London/NY Opens)",
            desc="Time-locked sniper bot. Maps quiet Asian range and trades\ninstitutional breakout volume at London & New York session opens.",
            port="8002"
        )

        # Action Buttons Frame
        btn_frame = tk.Frame(self, bg="#0f172a")
        btn_frame.pack(fill="x", padx=30, pady=(30, 20))
        
        # Launch Button
        self.launch_btn = tk.Button(
            btn_frame,
            text="Launch Bot & Open Dashboard",
            font=self.button_font,
            bg="#10b981", # Emerald 500
            fg="#ffffff",
            activebackground="#059669",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=15,
            pady=10,
            command=self.launch_bot
        )
        self.launch_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.launch_btn.bind("<Enter>", lambda e: self.launch_btn.configure(bg="#059669") if self.launch_btn["state"] != "disabled" else None)
        self.launch_btn.bind("<Leave>", lambda e: self.launch_btn.configure(bg="#10b981") if self.launch_btn["state"] != "disabled" else None)
        
        # Stop Button
        self.stop_btn = tk.Button(
            btn_frame,
            text="Stop Bot",
            font=self.button_font,
            bg="#ef4444", # Red 500
            fg="#ffffff",
            activebackground="#dc2626",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=15,
            pady=10,
            command=self.stop_bot,
            state="disabled"
        )
        self.stop_btn.pack(side="right", fill="x", expand=True, padx=(10, 0))
        self.stop_btn.bind("<Enter>", lambda e: self.stop_btn.configure(bg="#dc2626") if self.stop_btn["state"] != "disabled" else None)
        self.stop_btn.bind("<Leave>", lambda e: self.stop_btn.configure(bg="#ef4444") if self.stop_btn["state"] != "disabled" else None)

        # Utility Buttons Frame (For MT5 Terminal)
        util_frame = tk.Frame(self, bg="#0f172a")
        util_frame.pack(fill="x", padx=30, pady=(10, 0))
        
        self.mt5_btn = tk.Button(
            util_frame,
            text="📊 Open MetaTrader 5 Terminal",
            font=self.subtitle_font,
            bg="#334155", # Slate 700
            fg="#f8fafc",
            activebackground="#475569",
            activeforeground="#f8fafc",
            relief="flat",
            bd=0,
            padx=15,
            pady=8,
            command=self.launch_mt5
        )
        self.mt5_btn.pack(fill="x")
        self.mt5_btn.bind("<Enter>", lambda e: self.mt5_btn.configure(bg="#475569"))
        self.mt5_btn.bind("<Leave>", lambda e: self.mt5_btn.configure(bg="#334155"))

    def create_profile_card(self, profile_id, title, desc, port):

        # Outer Card Frame acting as border
        card_border = tk.Frame(self, bg="#334155", bd=1, relief="flat") # Slate 700
        card_border.pack(fill="x", padx=30, pady=6)
        
        # Inner Card Frame
        card_inner = tk.Frame(card_border, bg="#1e293b", padx=15, pady=10) # Slate 800
        card_inner.pack(fill="both", expand=True, padx=1, pady=1)
        
        # Title
        lbl_title = tk.Label(card_inner, text=title, font=self.header_font, fg="#f8fafc", bg="#1e293b")
        lbl_title.pack(anchor="w")
        
        # Description
        lbl_desc = tk.Label(card_inner, text=desc, font=self.body_font, fg="#94a3b8", bg="#1e293b", justify="left")
        lbl_desc.pack(anchor="w", pady=(4, 0))
        
        # Port
        lbl_port = tk.Label(card_inner, text=f"Dashboard Port: {port}", font=self.subtitle_font, fg="#475569", bg="#1e293b")
        lbl_port.pack(anchor="e", pady=(4, 0))
        
        # Bind click events to select the card
        for widget in (card_border, card_inner, lbl_title, lbl_desc, lbl_port):
            widget.bind("<Button-1>", lambda e, pid=profile_id: self.select_card(pid))
            
        self.cards[profile_id] = {
            "border": card_border,
            "inner": card_inner,
            "title": lbl_title,
            "desc": lbl_desc,
            "port": port
        }

    def select_card(self, profile_id):
        global active_profile
        if bot_process is not None:
            # Don't allow changing profiles while running
            return
            
        active_profile = profile_id
        
        for pid, components in self.cards.items():
            if pid == profile_id:
                components["border"].configure(bg="#3b82f6") # Blue 500 accent border
                components["title"].configure(fg="#3b82f6")
            else:
                components["border"].configure(bg="#334155")
                components["title"].configure(fg="#f8fafc")

    def launch_bot(self):
        global bot_process
        if bot_process is not None:
            messagebox.showinfo("Genesis", "Bot is already running!")
            return
            
        env = os.environ.copy()
        env["GENESIS_PROFILE"] = active_profile
        
        # Use python executable from the current environment (.venv)
        python_exe = sys.executable
        
        # Run main.py in a new process without a console window
        startupinfo = None
        creationflags = 0
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        
        try:
            bot_process = subprocess.Popen(
                [python_exe, "main.py"],
                env=env,
                startupinfo=startupinfo,
                creationflags=creationflags
            )
            
            # Update GUI status
            self.status_val.configure(text=f"Running ({active_profile.upper()})", fg="#10b981")
            self.profile_val.configure(text=active_profile.upper(), fg="#f8fafc")
            
            # Toggle buttons
            self.launch_btn.configure(state="disabled", bg="#1e293b")
            self.stop_btn.configure(state="normal", bg="#ef4444")
            
            # Open browser after delay to allow server startup
            port = self.cards[active_profile]["port"]
            self.after(2000, lambda: webbrowser.open(f"http://127.0.0.1:{port}"))
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start bot process:\n{e}")
            bot_process = None

    def stop_bot(self):
        global bot_process
        if bot_process is None:
            return
            
        try:
            bot_process.terminate()
            bot_process.wait(timeout=5)
        except Exception:
            try:
                bot_process.kill()
            except Exception:
                pass
            
        bot_process = None
        
        # Reset GUI status
        self.status_val.configure(text="Stopped", fg="#ef4444")
        self.profile_val.configure(text="None", fg="#94a3b8")
        
        # Reset buttons
        self.launch_btn.configure(state="normal", bg="#10b981")
        self.stop_btn.configure(state="disabled", bg="#1e293b")

    def launch_mt5(self):
        try:
            from bot.config.settings import settings
            path = settings.mt5_path
        except Exception:
            path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
            
        if not path or not os.path.exists(path):
            messagebox.showerror(
                "Error", 
                f"MetaTrader 5 executable not found at:\n{path}\n\n"
                "Please configure your MT5 installation path in settings."
            )
            return
            
        try:
            subprocess.Popen([path])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch MetaTrader 5:\n{e}")

    def on_closing(self):

        if bot_process is not None:
            if messagebox.askokcancel("Quit", "Genesis bot is still running in the background.\nClosing this window will stop the bot.\n\nDo you want to stop the bot and exit?"):
                self.stop_bot()
            else:
                return
        self.destroy()

if __name__ == "__main__":
    app = GenesisApp()
    app.mainloop()
