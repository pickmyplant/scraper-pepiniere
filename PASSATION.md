# Passation : comparateur de prix BDD Plante

**Pour démarrer une session, lire d'abord `DEMARRAGE.md` (court).** Ce document-ci sert de référence.

Ce document permet à une autre session Claude (second compte) de reprendre ou de compléter le travail sans repartir de zéro. Le prompt à coller est en bas de page.

## Où en est le projet (05/11/2026)

- Dépôt : `pickmyplant/scraper-pepiniere`, branche `claude/vibrant-keller-jibr76`.
- Script Python modulaire : noyau commun (`core/`), un adaptateur par pépinière (`adapters/`), réglages (`config.py`), journal (`logs/`). Voir le README pour le lancer.
- Premier adaptateur : **Promesse de Fleurs**. Testé sur 5 plantes (P006, P007, P013, P084, P116) : 64 requêtes, aucun blocage, 89 lignes produites.
- Ces 89 lignes ont été **ajoutées** à l'onglet Offres de la Google Sheet « BDD Plante » (lignes 61 à 149), avec l'accord de l'utilisatrice.

## État actuel (fin de session du 05/11/2026, Claude A)

- **Planfor** (adaptateur `adapters/planfor.py`, délai 1 à 2 s) : P001 à P080 relevés et dans Offres ; P081 relevé en test (Non trouvé). **Prochain lot : P082 à P120** (puis P081 à revoir selon la question Tamarix).
- **Promesse de Fleurs** : confié à Claude B (Ugo), Claude A n'y touche plus.
- **Mes Arbustes** : abandonné (Cloudflare).
- **Écriture dans Offres** : uniquement par le bouton « Relevés > Importer un relevé… » de la BDD (`outils/import_releve.gs`, testé et validé). Lancer le relevé avec `--releve <ID>` (ex. `PLF-AAAA-MM-JJ-A5`), relire avec `python outils/resume.py releves/<ID>.tsv`, commit et push, puis demander à l'utilisatrice de cliquer.
- **Données d'entrée** : `data/plantes_<lot>.csv` (colonnes ID, Nom commun, Autres noms, Synonymes, Genre, Espèce, Variétés). Lire la BDD seulement pour les lignes du lot (Plantes A:F ; Fiches colonne AB, jointure par ID car les lignes ne sont pas alignées). Claude B a déjà extrait certains lots sur sa branche `claude/pepiniere-ugo-p001-p040` (`data/plantes_P041-P080.csv`…) : les réutiliser si elles existent.
- **Économie de tokens** (objectif en cours) : ne pas relire tout Offres ni tout le Journal ; lire les onglets Règles, Réservations et Questions de la Sheet de coordination ; une session par lot.

## Ce qu'on sait du site Promesse de Fleurs

- La recherche du site est interdite par robots.txt (`/catalogsearch/`, `*keywords=`, `*page=`, filtres). On ne l'utilise pas.
- Le sitemap public (`/media/sitemap/fr/sitemap.xml`, 7 fichiers, ~31 700 URL) donne pour chaque page un `<image:title>` « Nom commun – Nom latin ». C'est par lui qu'on trouve les fiches.
- Chaque fiche produit contient un JSON-LD `ProductGroup` complet : `hasVariant` (un format par variante), `disambiguatingDescription` (contenant et hauteur), `price`, `availability`, et les prix dégressifs (`MinimumAdvertisedPrice` + `eligibleQuantity`). Pas besoin de navigateur.
- Dans l'environnement cloud, environ une requête sur deux a parfois été coupée au bout de 10 s par le proxy (ce n'était pas un blocage du site). Le script refait une seule tentative après 5 s.

## Règles de conduite (obligatoires)

- Ne travailler que dans le dépôt `scraper-pepiniere`. Ne toucher à aucun autre dépôt, en particulier celui du site de la BDD Plante.
- Ne commiter aucune clé, aucun mot de passe, aucune donnée personnelle.
- Avant de coder pour une nouvelle pépinière : étudier robots.txt, le sitemap, le JSON-LD Product, un éventuel JSON produit ou une API publique. Faire un rapport et attendre le feu vert.
- Respecter robots.txt. User-Agent clair (nom du projet). Attendre 3 à 5 s entre deux requêtes, jamais de requêtes en parallèle.
- En cas de blocage, de captcha, ou d'erreur 403 ou 429 : arrêter le script et prévenir, sans chercher à contourner.
- Ne simuler aucun panier, ne créer aucun compte, ne passer aucune commande.
- Google Sheet : ne jamais modifier les onglets existants (Plantes, Fiches, etc.). Dans Offres, **uniquement ajouter des lignes**, et **seulement après accord** de l'utilisatrice.

## Format de sortie (onglet Offres, 16 colonnes, dans cet ordre)

ID | Plante | Variété | Pépinière | Lien du produit | Prix | Devise | Contenant | Disponibilité | Frais de port | Relevé le | Lien affilié | Statut | Notes | Nom latin | Hauteur ou forme

- Plante = Nom commun de la BDD.
- **Nom latin** = « Genre espèce » pour l'espèce type, et « Genre espèce 'Variété' » pour une variété précise (exemple : `Malus × floribunda 'Evereste'`).
- Variété = la variété lue dans le titre de la fiche, ou « Espèce type ».
- Prix avec virgule décimale et sans symbole (34,90). Devise EUR. Relevé le au format JJ/MM/AAAA.
- Contenant normalisé (« Pot 2 à 3 L », « Godet 8/9 cm », « Racines nues », « Motte », « Sachet de graines »).
- **Hauteur ou forme** : on préfère la hauteur chiffrée (« 30-35 cm ») ; la forme (Mini-tige, Buisson…) va alors dans Notes. On n'écrit la forme dans cette colonne que si aucune hauteur n'est donnée.
- Disponibilité : « En stock » ou « Rupture ». Frais de port : port minimum à domicile de la pépinière, lu dans `data/frais_port.csv` (vide si non publié ; le seuil de gratuité reste dans ce fichier). Lien affilié vide. Statut « À vérifier » partout.
- Notes : prix dégressifs (« 29,50 € l'unité dès 3 »), lots, prix barrés, « Variété trouvée : X (absente de l'onglet Fiches) », « autres formats disponibles ».

## Décisions prises avec l'utilisatrice

1. **Lire toutes les fiches** qui correspondent à la plante (toutes les variétés), et garder **tous les formats en stock**. Le plafond de 8 lignes s'applique par fiche produit (`MAX_LIGNES_PAR_PRODUIT`), pas par plante.
2. Une fiche dont aucun format n'est en stock donne une seule ligne « Rupture », avec l'URL et sans prix.
3. Rapprochement : par Genre + espèce dans le titre du sitemap, ou par Genre + une variété de l'onglet Fiches (c'est le cas d'Evereste, rattaché à P007). Le nom latin doit figurer sur la fiche ; sinon, on ne relève rien et on le signale, sauf quand le genre et une variété de Fiches correspondent (mention dans Notes).
4. **Mélanges** : un mélange de plusieurs plantes est exclu ; un lot ou un mélange de la même espèce est gardé, avec une mention dans Notes.
5. Les coloris vendus comme variétés (laurier-rose « Rose », « Blanc », « Jaune »…) restent écrits sous ce format. L'utilisatrice les ajoutera elle-même dans la colonne Variétés de Fiches.
6. **Table d'équivalences** `data/equivalences_varietes.csv` (ID, Nom sur le site, Nom dans Fiches) pour les noms traduits ou écrits autrement (exemple : Soeur Agnes = Sister Agnes).
7. Aucune ligne « Non trouvé » quand au moins une fiche correspond.

## Données d'entrée

- `data/plantes_test.csv` : export des onglets Plantes (ID, Nom commun, Autres noms, Synonymes, Genre, Espèce) et Fiches (colonne Variétés, en colonne AB, au format « Nom = particularités », une variété par ligne).
- ID de la Google Sheet « BDD Plante » : `1-jgpIhnAYTMXQ6DMYipqoll3DNkgdMOpCRoqwdwnUqM`.
- Attention : les lignes de Fiches ne sont pas alignées sur celles de Plantes. Il faut joindre par ID, pas par numéro de ligne.

## Pour éviter les conflits entre les deux comptes

**Coordination (Google Sheet)** : « Coordination Claude – Comparateur pépinières (Sheet) », ID `1QfeIyLnsM3EMQf_cc1KmPNYQ1xJJzRG_kYJr5ZfIFIM`. Onglets Règles, Réservations, Journal, Découvertes, Questions : une ligne ajoutée par étape, jamais de modification des lignes de l'autre Claude. L'ancien Google Doc (ID `1xBP17AeTDRVC86GzNqxWDZ6n0m06w4lxILCR9_l09Q8`) est archivé. Claude A = compte 1, Claude B = compte 2 (Ugo).

- Chaque compte travaille sur **sa propre branche** (par exemple `claude/pepiniere-<nom>`), jamais sur la branche de l'autre.
- Les deux comptes se répartissent le travail : des pépinières différentes, ou des lots de plantes différents. Ils ne doivent jamais relever la même plante dans la même pépinière.
- Un seul relevé à la fois par pépinière : deux sessions sur le même site doubleraient la charge et ne respecteraient plus le délai entre requêtes.
- **Écrire dans Offres via le bouton d'import** (décision du 05/11/2026, pour économiser des tokens) : lancer le relevé avec `--releve <ID>`, relire seulement `python outils/resume.py releves/<ID>.tsv`, commit et push, puis demander à l'utilisatrice de cliquer sur **Relevés > Importer un relevé…** dans la Sheet. Ne plus recopier les lignes avec le connecteur. Voir le README.
- Méthode de secours, sans le bouton. **Écrire dans Offres sans tout relire** (décision du 05/11/2026) :
  1. lire seulement l'onglet **« Relevés »** de la Sheet, le registre qui contient une ligne par écriture dans Offres ;
  2. si la même pépinière y a déjà un relevé pour ces plantes, c'est une **mise à jour** : lire seulement les colonnes A (ID) et D (Pépinière) d'Offres, lancer `python outils/remplacement.py ids.json pepinieres.json "<Pépinière>" P001,P002`, supprimer les lignes indiquées (requêtes fournies par l'outil), puis ajouter les nouvelles lignes en bas ;
  3. sinon, ajouter simplement en bas ;
  4. après l'écriture, ajouter une ligne dans « Relevés ». Pour une mise à jour, noter dans les Remarques de l'ancien relevé « P0xx remplacé par <Relevé> le <date> ».
- Les lignes « Non trouvé » s'écrivent aussi dans Offres.

---

## Prompt à coller dans le second compte

> Travaille uniquement dans le dépôt GitHub `pickmyplant/scraper-pepiniere`. Ne touche à aucun autre dépôt, en particulier celui du site de la BDD Plante. Ne commite aucune clé, aucun mot de passe ni aucune donnée personnelle.
>
> Commence par lire `DEMARRAGE.md` sur la branche `claude/vibrant-keller-jibr76` (court). Ne lis `PASSATION.md` ou `README.md` qu'en cas de doute précis. Applique les règles et décisions sans les remettre en question.
>
> Lis ensuite les onglets Règles, Réservations et Questions de la Google Sheet « Coordination Claude – Comparateur pépinières (Sheet) » (ID `1QfeIyLnsM3EMQf_cc1KmPNYQ1xJJzRG_kYJr5ZfIFIM`) avec le connecteur Google Sheets. Tu es **Claude B**. Respecte ses règles : réserve ta pépinière et tes plantes dans l'onglet Réservations avant de commencer, ne touche pas à ce que Claude A a réservé, et ajoute une ligne dans Journal à chaque étape et dans Découvertes pour chaque découverte, sans modifier les lignes de l'autre.
>
> Crée ta propre branche à partir de `claude/vibrant-keller-jibr76` et travaille uniquement dessus. Un autre compte Claude travaille en parallèle sur ce projet : ne touche pas à ses branches.
>
> **Ta tâche :** [À COMPLÉTER, par exemple :
> - « Ajoute un adaptateur pour la pépinière X (URL). Commence par le rapport d'étude du site (robots.txt, sitemap, JSON-LD, API), puis attends mon feu vert. »
> - ou « Lance l'adaptateur Promesse de Fleurs sur les plantes P120 à P160. »]
>
> Livraison : pour un nouveau site, d'abord le rapport, puis attends mon accord. Pour un lot, `outils/lot.sh <ID> data/plantes_<lot>.csv` puis donne-moi l'identifiant pour le bouton « Importer un relevé ». N'écris jamais toi-même dans l'onglet Offres. Réponses concises : résumé court et seulement les cas à vérifier.
