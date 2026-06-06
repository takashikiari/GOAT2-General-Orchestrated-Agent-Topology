#!/usr/bin/env python3
"""
Listează fișierele din directorul 'supervisor'.

Funcția poate fi importată și apelată din orice script Python,
sau rulată direct ca script.
"""

import os
from pathlib import Path
from typing import List, Optional


def list_supervisor_files(
    directory: str = "supervisor",
    recursive: bool = False,
    pattern: Optional[str] = None,
) -> List[str]:
    """
    Returnează o listă cu numele fișierelor din directorul 'supervisor'.

    Parametri:
        directory (str):  Calea către director (implicit: 'supervisor').
        recursive (bool): Dacă True, listează recursiv și subdirectoarele.
        pattern (str):    Filtru opțional de tip glob (ex: '*.py', '*.txt').

    Returnează:
        List[str]: Lista de căi relative către fișierele găsite.

    Aruncă:
        FileNotFoundError: Dacă directorul nu există.
        PermissionError:   Dacă nu există permisiuni de citire.
    """
    dir_path = Path(directory)

    if not dir_path.exists():
        raise FileNotFoundError(f"Directorul '{directory}' nu există.")

    if not dir_path.is_dir():
        raise NotADirectoryError(f"'{directory}' nu este un director.")

    if not os.access(str(dir_path), os.R_OK):
        raise PermissionError(f"Nu există permisiuni de citire pentru '{directory}'.")

    if recursive:
        if pattern:
            return sorted(str(p) for p in dir_path.rglob(pattern) if p.is_file())
        else:
            return sorted(str(p) for p in dir_path.rglob("*") if p.is_file())
    else:
        if pattern:
            return sorted(str(p) for p in dir_path.glob(pattern) if p.is_file())
        else:
            return sorted(
                str(p) for p in dir_path.iterdir() if p.is_file()
            )


def main():
    """Punct de intrare pentru rularea directă ca script."""
    try:
        files = list_supervisor_files(recursive=False)
        print(f"Fișiere în directorul 'supervisor' ({len(files)} găsite):\n")
        for f in files:
            print(f"  • {f}")
    except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
        print(f"Eroare: {e}")
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
