# scraper-pepiniere

Relevé des offres de pépinières pour le comparateur BDD Plante. Le script produit un TSV au format de l'onglet **Offres**.

## Lancer

```bash
pip install requests
python run.py --ids P006,P007,P013,P084,P116
```

Pour Planfor :

```bash
python run.py --pepiniere planfor --ids P006,P007,P013,P084,P116
```

Options :
- `--plantes` : CSV d'entrée (par défaut `data/plantes_test.csv`, voir plus bas) ;
- `--sortie` : fichier de sortie (par défaut `offres_<pepiniere>_test.tsv`) ;
- `--pepiniere` : adaptateur à utiliser, `promessedefleurs` (par défaut) ou `planfor`.

Fichiers produits :
- `offres_promessedefleurs_test.tsv` : les lignes à coller dans Offres, en **ajout uniquement** ;
- `offres_promessedefleurs_test_anomalies.tsv` : ce qui n'a pas pu être relevé, avec la raison ;
- `logs/releve_*.log` : le journal de chaque requête et de chaque erreur (non versionné).

## Entrée

`data/plantes_test.csv` est un export des onglets Plantes (ID, Nom commun, Autres noms, Synonymes, Genre, Espèce) et Fiches (colonne Variétés, une variété par ligne au format « Nom = particularités »).

## Écriture dans la Google Sheet (bouton d'import)

Pour économiser des tokens, Claude ne recopie plus les lignes dans la Sheet : c'est la Sheet qui les importe elle-même depuis GitHub.

1. Relevé avec un identifiant :
   ```bash
   python run.py --pepiniere planfor --plantes data/plantes_P081-P120.csv --releve PLF-2026-11-06-A4 --par "Claude A"
   ```
   Cela produit `releves/PLF-2026-11-06-A4.tsv`, le fichier d'anomalies et la fiche `releves/PLF-2026-11-06-A4.json`.
   En une seule commande (relevé silencieux, résumé, commit et push) : `outils/lot.sh PLF-2026-11-06-A4 data/plantes_P081-P120.csv`.
2. Résumé compact à relire, sans tout afficher : `python outils/resume.py releves/PLF-2026-11-06-A4.tsv`
3. Commit et push, puis l'utilisatrice clique sur **Relevés > Importer un relevé…** dans la Sheet et saisit l'identifiant (ou `ID@branche` pour une autre branche).
   Le script vérifie le registre, remplace les anciennes lignes s'il s'agit d'une mise à jour (même pépinière et mêmes plantes), ajoute tout en un seul bloc et remplit l'onglet « Relevés ».

Installation du bouton (une seule fois) : copier `outils/import_releve.gs` dans la Sheet (Extensions > Apps Script), enregistrer, recharger la Sheet, puis **Relevés > Configurer le jeton GitHub** avec un jeton GitHub « fine-grained » en lecture seule (Contents : Read-only) sur ce dépôt. Le jeton reste dans les propriétés du script, jamais dans le dépôt.

Sans le bouton, l'ancienne méthode reste possible : registre « Relevés », puis `outils/remplacement.py` pour une mise à jour (voir `PASSATION.md`).

## Équivalences de variétés

Quand le site écrit une variété autrement que l'onglet Fiches (traduction, orthographe), ajoutez une ligne dans `data/equivalences_varietes.csv` :

```
ID,Nom sur le site,Nom dans Fiches
P084,Soeur Agnes,Sister Agnes
```

- `ID` vide : l'équivalence vaut pour toutes les plantes.
- `Nom dans Fiches` doit être écrit exactement comme dans la colonne Variétés.

La ligne relevée prend alors le nom de Fiches (Variété = Sister Agnes, Nom latin = Nerium oleander 'Sister Agnes'). Pas besoin de toucher au code.

## Organisation

| Fichier | Rôle |
|---|---|
| `config.py` | plafonds de lignes, délais (3 à 5 s), User-Agent, timeouts |
| `core/http.py` | client poli : robots.txt, une requête à la fois, arrêt sur 403, 429 ou captcha |
| `core/bdd.py` | lecture de la BDD |
| `core/matching.py` | rapprochement par nom latin et variétés de Fiches |
| `core/formatting.py` | colonnes Offres : prix `34,90`, Contenant, Hauteur ou forme |
| `core/export.py` | export TSV |
| `adapters/promessedefleurs.py` | sitemap public, puis JSON-LD de chaque fiche produit |
| `adapters/planfor.py` | recherche du site en GET (autorisée), puis JSON-LD ; la variété est lue offre par offre |

## Règles appliquées

- Une ligne par format en stock et par fiche produit (une fiche = une variété). Toutes les fiches sont lues. Le plafond est `MAX_LIGNES_PAR_PRODUIT = 8` ; au-delà, on garde des formats répartis du plus petit au plus grand.
- **Nom latin** : « Genre espèce 'Variété' » quand la fiche porte une variété, sinon « Genre espèce ».
- Les mélanges de plusieurs plantes sont exclus. Un lot ou un mélange de la même espèce est gardé, avec une mention dans Notes.
- Statut toujours « À vérifier ». Frais de port et Lien affilié restent vides.
- Délais : 3 à 5 s par défaut, réglables par site dans `DELAIS_PAR_SITE` (Planfor : 1 à 2 s, sur accord). Le `Crawl-delay` de robots.txt est toujours respecté s'il est plus long.
- Si le site renvoie 403, 429, 503 ou un captcha, le script s'arrête sans rien tenter d'autre (code de sortie 2).

Pour ajouter une pépinière, créez `adapters/<nom>.py` avec une classe `Adaptateur` qui expose `releve(plante, date)` et renvoie `(offres, anomalies)`, puis déclarez-la dans `run.py`.
