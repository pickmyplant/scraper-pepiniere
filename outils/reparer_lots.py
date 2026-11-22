#!/usr/bin/env python3
"""Répare dans des relevés les lignes à l'unité prises pour des lots (défaut corrigé le 22/11/2026, core/lots.reparer).
Usage : python outils/reparer_lots.py releves/<ID>.tsv [...]   (réécrit les fichiers ; à réimporter ensuite)
"""
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.lots import completer, reparer   # noqa: E402

for chemin in sys.argv[1:]:
    with open(chemin, encoding="utf-8", newline="") as f:
        lignes = list(csv.reader(f, delimiter="\t"))
    n = 0
    for i, r in enumerate(lignes[1:], 1):
        a = completer(r)[:]
        b = reparer(completer(r))
        if b != a:
            lignes[i] = b[:len(r)] if len(r) >= 17 else b
            n += 1
    if n:
        with open(chemin, "w", encoding="utf-8", newline="") as f:
            csv.writer(f, delimiter="\t", lineterminator="\n").writerows(lignes)
    print("%s : %d ligne(s) réparée(s)" % (os.path.basename(chemin), n))
