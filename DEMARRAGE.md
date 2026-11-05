# Démarrage rapide (à lire en premier, à la place de PASSATION.md et du README)

Ne lire PASSATION.md ou le README qu'en cas de doute précis (format des colonnes, décisions, historique).

## Règles essentielles
- Seulement le dépôt `pickmyplant/scraper-pepiniere`, sur sa propre branche (Claude A : `claude/vibrant-keller-jibr76`).
- Rien de secret ou de personnel dans les commits.
- robots.txt respecté, une requête à la fois ; sur 403, 429, 503 ou captcha : arrêt et on prévient, sans contourner.
- **Jamais d'écriture directe dans l'onglet Offres** : l'utilisatrice clique sur Relevés > Importer un relevé.
- Questions à trancher : une ligne dans l'onglet Questions, sans décider seul.

## État (05/11/2026)
- Planfor (Claude A) : P001-P081 faits ; **prochain lot P082-P120**.
- Promesse de Fleurs : Claude B seulement. Mes Arbustes : abandonné (Cloudflare).

## Faire un lot (5 étapes)
1. Sheet de coordination `1QfeIyLnsM3EMQf_cc1KmPNYQ1xJJzRG_kYJr5ZfIFIM` : lire seulement Règles, Réservations, Questions ; ajouter une ligne « En cours » dans Réservations.
2. `data/plantes_<lot>.csv` (ID, Nom commun, Autres noms, Synonymes, Genre, Espèce, Variétés) : lire dans la BDD `1-jgpIhnAYTMXQ6DMYipqoll3DNkgdMOpCRoqwdwnUqM` seulement les lignes du lot (Plantes A:F ; Fiches colonne AB, jointure par ID).
3. Une seule commande, qui lance le relevé, affiche le résumé, fait le commit et le push :
   ```bash
   outils/lot.sh PLF-AAAA-MM-JJ-A5 data/plantes_<lot>.csv
   ```
4. Ne regarder que les cas « À vérifier » du résumé ; sur arrêt (code 2), prévenir.
5. Donner l'identifiant à l'utilisatrice ; après import : une ligne « Terminé » dans Réservations et une dans Journal.
