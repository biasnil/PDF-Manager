from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ModeMismatch:
    message: str          # what to show the person
    switch_to_mode: str    # the mode value that actually matches this file


def check_mode_mismatch(
    file_path: str,
    current_mode: str,
    current_mode_extensions: set[str],
    other_mode: str,
    other_mode_extensions: set[str],
    current_mode_label: str,
    other_mode_label: str,
) -> Optional[ModeMismatch]:
    """
    Returns a ModeMismatch if file_path's extension matches the OTHER
    mode's expected type instead of the current one — e.g. the person is
    on "Word to PDF" but picked/dropped a .pdf. Returns None when there's
    nothing to warn about (including when the extension matches neither
    mode — that's a plain file-type problem, not a mode mismatch).
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in current_mode_extensions:
        return None
    if ext in other_mode_extensions:
        return ModeMismatch(
            message=(
                f'"{os.path.basename(file_path)}" looks like a file for '
                f'"{other_mode_label}", not "{current_mode_label}". '
                f"Switch to \"{other_mode_label}\" instead?"
            ),
            switch_to_mode=other_mode,
        )
    return None