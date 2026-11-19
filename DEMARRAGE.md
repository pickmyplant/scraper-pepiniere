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

## Corriger les variétés de l'onglet Offres (sans écrire dans Offres)
1. `python outils/corriger_offres.py CORR-AAAA-MM-JJ-A1` : lit les onglets publiés (Offres, Export), nettoie la colonne Variété
   (formes, tailles, cache-pots, nom d'espèce répété), relie aux variétés de Fiches et écrit `corrections/<ID>*.tsv`.
   Synonymes, fiches mal rangées et erreurs : `data/corrections_varietes.txt` (une ligne par règle).
2. `python outils/decrire_varietes.py <ID> <cache.json>` : une page vendeur par variété absente de Fiches, pour le petit texte et la source
   (`corrections/<ID>_fiches.tsv`).
3. Commit, `outils/publier.sh`, puis l'utilisatrice clique dans la BDD : Relevés > Appliquer des corrections d'offres…,
   puis Relevés > Ajouter des variétés aux fiches… (même identifiant). Ne lire que `corrections/<ID>_resume.txt`.

## Actualisation automatique (prix, stocks), sans Claude
- `.github/workflows/actualiser.yml` : le 1er et le 15 du mois, `outils/actualiser.sh <pépinière>` pour chaque adaptateur (relevé complet
  par lots de 100 plantes, un relevé par lot `<PREFIXE>-<date>-AUTO-L<n>`, variétés nettoyées), puis un commit sur la branche commune
  et la liste `releves/a_importer.txt`. Lot raté (listé dans le résumé de la tâche GitHub) : le relancer avec `outils/lot.sh` et le même identifiant (ajouté seul à `releves/a_importer.txt`, donc importé automatiquement).
  Garde-fous : 100 formats par produit, 1000 fiches par plante (`config.py`).
- Sites relevés par l'ordinateur de l'utilisatrice (liste MAC du fichier) : ceux qui refusent les serveurs de GitHub. Sites découpés
  en parties (liste PARTIES) : ceux qui dépassent 6 heures ; parties relevées l'une après l'autre pour les sites qui saturent (liste SUITE : Jardin du Pic Vert). Dépôt public : minutes GitHub illimitées.
- La Sheet importe seule les relevés de cette liste (menu Relevés > Activer l'import automatique).
- `outils/lot.sh` nettoie aussi les variétés de chaque relevé manuel (`corriger_offres.py --releve`).
- Lots (colonne Q « Lot » d'Offres, 19/11/2026) : l'adaptateur note seulement « lot de N » dans Notes avec le prix du lot ;
  `core/lots.py` (appelé à l'export) regroupe : lot doublant une ligne à l'unité → reporté dans ses Notes ; vendu seulement
  par lot → Prix d'un plant, Lot = « N ; prix du lot » (plus petit lot), détail dans Notes. Relevés à 17 colonnes.
- Claude n'intervient que si un adaptateur casse (tâche en échec dans l'onglet Actions de GitHub).

## Règles ajoutées le 19/11/2026 (contrôle des offres des 10 plantes les plus vendues)
- Variété : `core/matching.variete_fiches` retient le nom de Fiches cité le plus tôt dans le texte, puis le plus long
  (« Endless Summer The Bride » -> The Bride grâce au synonyme ; « Cherry Bouquet Rouge » -> Cherry Bouquet).
  Une sous-variété vendue sous le nom d'une gamme se règle donc en l'ajoutant à Fiches > Variétés avec le nom complet en synonyme.
- Cultivar entre guillemets : `M.cultivar_cite` / `M._CITE` (apostrophe intérieure gardée : 'Rambour d'Hiver', 'Longue d'Août') ;
  `variete_du_titre` le lit avant de conclure à l'espèce type (‘Granny Smith’ chez Le Jardin de Pascal).
- Lots : `corriger_offres.regrouper_lots`, appliqué à chaque relevé (manuel et automatique) : une ligne de lot qui double la ligne
  à l'unité est retirée et notée sur celle-ci ; un produit vendu seulement par lot garde le prix d'un plant et la note
  « Vendu seulement par lot : … » (consignes de la BDD, section 7).
- Autres espèces exclues par leur lien et orthographes unifiées : bloc du 19/11/2026 de `data/corrections_varietes.txt`.
