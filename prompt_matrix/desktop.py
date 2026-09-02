"""Double-click entry for the PyInstaller desktop build. Starts the local web UI."""

from __future__ import annotations


def main() -> int:
    from prompt_matrix.web import serve

    return serve([])


if __name__ == "__main__":
    raise SystemExit(main())
