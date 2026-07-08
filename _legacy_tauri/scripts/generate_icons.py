"""One-shot script to populate desktop/src-tauri/icons/ with the
placeholder icon set expected by tauri.conf.json.

Run once before the first `npm run dev` (or `cargo tauri dev`):

    ..\\..\\.venv\\Scripts\\python.exe scripts\\generate_icons.py

Pillow is already a project dependency (used by `launcher.py`), so no
extra `pip install` is required.
"""

from __future__ import annotations

import os
import sys

try:
    from PIL import Image
except ImportError as exc:
    sys.stderr.write(
        "Pillow is required to generate icons.\n"
        "Install it into the project venv: "
        "`pip install pillow`\n"
    )
    raise SystemExit(1) from exc


HERE = os.path.dirname(os.path.abspath(__file__))
ICONS_DIR = os.path.normpath(os.path.join(HERE, "..", "src-tauri", "icons"))


def main() -> int:
    os.makedirs(ICONS_DIR, exist_ok=True)

    # 16x16 tray icons (PNG, RGBA so Tauri's image-png feature accepts them).
    for name, hex_fill in (
        ("tray-idle", (0x3A, 0x3A, 0x3A, 0xFF)),
        ("tray-running", (0x2E, 0xCC, 0x71, 0xFF)),
    ):
        path = os.path.join(ICONS_DIR, f"{name}.png")
        Image.new("RGBA", (16, 16), hex_fill).save(path)
        print(f"wrote {path}")

    # App icon sizes referenced by tauri.conf.json bundle.icon.
    for size in (32, 128):
        path = os.path.join(ICONS_DIR, f"{size}x{size}.png")
        Image.new("RGBA", (size, size), (0x1F, 0x29, 0x37, 0xFF)).save(path)
        print(f"wrote {path}")

    high_dpi = os.path.join(ICONS_DIR, "128x128@2x.png")
    Image.new("RGBA", (256, 256), (0x1F, 0x29, 0x37, 0xFF)).save(high_dpi)
    print(f"wrote {high_dpi}")

    # .ico (Windows installer + window default). Embed multiple sizes
    # so the shell looks crisp on hi-DPI displays.
    ico_path = os.path.join(ICONS_DIR, "icon.ico")
    base = Image.new("RGBA", (256, 256), (0x1F, 0x29, 0x37, 0xFF))
    base.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"wrote {ico_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
