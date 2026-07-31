"""Environment variable utilities — MT5 credential cascade.

Shared between ``main.py`` and ``scripts/check_setup.py`` so both use the
same save / restore logic when loading a profile-specific ``.env`` file.

Profile ``.env`` files (``.env.scalper``, ``.env.breakout``, etc.) only
specialise strategy parameters.  If they contain ``MT5_LOGIN=0`` or an empty
``MT5_PASSWORD``, calling ``load_dotenv(override=True)`` would wipe the real
credentials loaded from the base ``.env``.  This helper snapshots the base
values *before* the profile load and restores them when the profile left
them empty / placeholder.
"""

import os

# ── MT5 env‑var keys that the cascade protects ─────────────────────
_MT5_KEYS = ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH")

# ── Values that indicate a key was left as a placeholder in the
#    profile .env and should be reverted to the base value.  Kept in a
#    frozenset for O(1) membership lookup (called once per key per load).
_PLACEHOLDER_VALUES = frozenset({
    "your_login",
    "your_password",
    "your_server",
    "changeme",
    "placeholder",
    "<password>",
    "<server>",
})


def preserve_mt5_credentials(profile_env_path, load_dotenv_func):
    """Snapshot MT5 env vars, load a profile ``.env``, restore any that
    the profile wiped with empty / placeholder defaults.

    Parameters
    ----------
    profile_env_path : str or os.PathLike
        Path to the profile-specific ``.env`` file
        (e.g. ``.env.scalper``, ``.env.breakout``).
    load_dotenv_func : callable
        A ``load_dotenv(path, override=True)``-compatible function,
        typically ``dotenv.load_dotenv``.
    """
    # Snapshot current MT5 values (loaded from base .env)
    snapshot = {
        k: os.environ[k]
        for k in _MT5_KEYS
        if os.environ.get(k)
    }

    # Apply the profile override
    load_dotenv_func(profile_env_path, override=True)

    # Restore any credential the profile wiped with a placeholder
    for key, base_value in snapshot.items():
        current = os.environ.get(key, "").strip()
        if _is_placeholder(current):
            os.environ[key] = base_value


# ── Internal helpers ────────────────────────────────────────────────

def _is_placeholder(value: str) -> bool:
    """Return True if *value* is empty, zero, or a known placeholder."""
    if not value:
        return True
    if value == "0":
        return True
    return value.lower() in _PLACEHOLDER_VALUES
