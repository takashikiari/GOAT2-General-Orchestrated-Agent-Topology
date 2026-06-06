#!/usr/bin/env python3
"""Script care creează un fișier text pe desktop și scrie un mesaj."""

from pathlib import Path

# Determinăm calea către desktop
desktop = Path.home() / "Desktop"
nume_fisier = "mesaj.txt"
cale_fisier = desktop / nume_fisier

# Conținutul de scris
continut = "Eu sunt GOAT🐐, greatest of all time"

# Scrierea în fișier (creează fișierul dacă nu există, suprascrie dacă există)
with open(cale_fisier, 'w', encoding='utf-8') as f:
    f.write(continut)

print(f"Fișierul a fost creat: {cale_fisier}")
print(f"Conținut scris: {continut}")
