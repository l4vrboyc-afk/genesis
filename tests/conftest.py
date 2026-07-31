"""
pytest configuration for the Genesis Trading Bot test suite.

Adds a ``--js`` CLI flag that runs the frontend JavaScript test suite
(profile + ticker tests) via ``npm test`` after all Python tests
complete, printing results inline in the pytest summary.
"""

import os
import subprocess
import sys

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--js`` CLI flag."""
    parser.addoption(
        "--js",
        action="store_true",
        default=False,
        help="Also run the frontend JavaScript test suite (profile + ticker) "
        "after Python tests complete.",
    )


def pytest_terminal_summary(
    terminalreporter, exitstatus: int, config: pytest.Config
) -> None:
    """Hook into the terminal summary phase.

    If ``--js`` was passed, invoke the frontend JS test suite via
    ``npm test`` in the ``dashboard/frontend/`` directory. Results
    are printed inline in the pytest summary output.
    """
    if not config.getoption("--js"):
        return

    terminalreporter.section("Frontend JS Tests", sep="-", blue=True)

    frontend_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dashboard",
        "frontend",
    )
    if not os.path.isdir(frontend_dir):
        terminalreporter.write_line(
            f"  ⚠️  Frontend directory not found: {frontend_dir}",
            red=True,
        )
        terminalreporter.write_line(
            "  JS tests skipped. Ensure dashboard/frontend/ exists.",
        )
        return

    terminalreporter.write_line(f"  Running: npm test (cwd={frontend_dir})\n")

    # Use ``shell=True`` on Windows so ``npm`` resolves via the user's
    # PATH (Git Bash / Node.js installer adds it).  Without this flag,
    # ``subprocess`` only sees the system PATH, which often excludes
    # the user's `%AppData%\npm` or Node.js installation directory.
    result = subprocess.run(
        "npm test",
        cwd=frontend_dir,
        capture_output=True,
        text=True,
        timeout=60,
        shell=True,
    )

    # Print stdout line-by-line through the terminal reporter so output
    # is collapsed under the section heading.
    for line in result.stdout.strip().split("\n"):
        terminalreporter.write_line(f"  {line}")

    if result.returncode != 0:
        # Print stderr for debugging
        for line in result.stderr.strip().split("\n"):
            if line.strip():
                terminalreporter.write_line(f"  ⚠️  {line}", red=True)

    terminalreporter.write_line("")

    if result.returncode == 0:
        terminalreporter.write_sep("-", "JS tests: ALL PASSED", green=True)
    else:
        terminalreporter.write_sep(
            "-",
            f"JS tests: FAILED (exit code {result.returncode})",
            red=True,
        )
        # Also mark the overall test session as failed by writing to
        # sys.exit status — but only if the Python suite itself passed.
        if exitstatus == 0:
            # We can't change exitstatus directly, but we can set
            # a marker for the caller. The non-zero return from
            # subprocess is already visible in the summary.
            terminalreporter.write_line(
                f"  Use `cd dashboard/frontend && npm test` for detailed "
                f"failure output.",
            )

    terminalreporter.write_line("")
