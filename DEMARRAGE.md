# Démarrage rapide (à lire en premier, à la place de PASSATION.md et du README)

Ne lire PASSATION.md ou le README qu'en cas de doute précis (format des colonnes, décisions, historique).

## Règles essentielles
- Seulement le dépôt `pickmyplant/scraper-pepiniere`, sur sa propre branche (Claude A : `claude/vibrant-keller-jibr76`).
- Rien de secret ou de personnel dans les commits.
- robots.txt respecté, une requête à la fois ; sur 403, 429, 503 ou captcha : arrêt et on prévient, sans contourner.
- **Jamais d'écriture dans l'onglet Offres** (ni avec le connecteur) : l'utilisatrice importe elle-même avec Relevés > Importer un relevé (confirmé le 08/11/2026).
- Questions à trancher : une ligne dans l'onglet Questions, sans décider seul.

## État
- Avancement par pépinière et par lot : onglet Réservations (ne pas le recopier ici).
- Adaptateurs : un fichier par pépinière dans `adapters/` (`NOM` et `PREFIXE` en tête) : arborescence (ARB),
  clorofila (CLO), mapepiniere (MPE), naudet (NAU), planfor (PLF), promessedefleurs (PDF, Claude B),
  rayondeserre (RDS), truffaut (TRU : Truffaut + Arborix + Bloomique en un seul relevé), verdiagarden (VER).
- Promesse de Fleurs : Claude B seulement. Mes Arbustes : abandonné (Cloudflare).
- Arborescence, décisions : prix en MAD sans conversion ; tout format vendu = « En stock » (stock non affiché, mention dans Notes) ;
  une fiche « Genre a / b » (deux espèces) est gardée pour les deux plantes.

## Branche commune
Chaque conversation travaille sur sa propre branche, mais publie sur `claude/vibrant-keller-jibr76` :
`outils/lot.sh` le fait tout seul (via `outils/publier.sh`). Pour tout autre travail (nouvel adaptateur, correction),
lancer `outils/publier.sh` après le commit. Ainsi chaque nouvelle conversation a tous les adaptateurs.

## Nouvelle pépinière
1. Étude du site (robots.txt, sitemap, JSON-LD, API) : rapport à l'utilisatrice, attendre son feu vert.
2. Un seul fichier à créer : `adapters/<nom>.py`, avec `NOM = "..."`, `PREFIXE = "XXX"` (3 lettres, pas déjà pris)
   et une classe `Adaptateur` exposant `releve(plante, date)` -> `(offres, anomalies)` ; s'inspirer de `adapters/planfor.py`.
   Rien à déclarer dans `run.py` ni dans `outils/lot.sh` (découverte automatique).
3. Délai propre au site, si accordé : `DELAIS_PAR_SITE` dans `config.py`.
4. Essai sur 5 plantes : `outils/lot.sh XXX-AAAA-MM-JJ-A1 data/plantes_<lot>.csv` (publie tout seul).

## Faire un lot (5 étapes)
1. Sheet de coordination `1QfeIyLnsM3EMQf_cc1KmPNYQ1xJJzRG_kYJr5ZfIFIM` : lire seulement Règles, Réservations, Questions ; ajouter une ligne « En cours » dans Réservations.
2. Extraire le lot sans le lire : `python outils/extraire_lot.py P082 P120` (onglet Export de la BDD publié en CSV, lien dans `BDD_EXPORT_URL`). Secours : lire seulement `Export!A<n>:G<m>` avec le connecteur (une ligne par plante, déjà jointe). Ne jamais afficher la colonne Variétés en entier : elle est très longue.
3. Une seule commande, qui lance le relevé, affiche le résumé, fait le commit et le push :
   ```bash
   outils/lot.sh PLF-AAAA-MM-JJ-A5 data/plantes_<lot>.csv
   ```
4. Ne regarder que les cas « À vérifier » du résumé ; sur arrêt (code 2), prévenir.
5. Terminer la réponse par la ligne « Identifiant à importer : <ID> » (sans parenthèses ; le bouton trouve la branche tout seul). Identifiant unique : vérifier dans Réservations le dernier numéro utilisé ; après import : une ligne « Terminé » dans Réservations et une dans Journal.
