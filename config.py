"""Réglages du relevé d'offres. Aucune clé ni mot de passe ici."""

# Identité du robot (envoyée dans chaque requête)
USER_AGENT = "scraper-pepiniere/0.1 (comparateur de prix BDD Plante; robot de test, 1 requete toutes les 3-5 s)"

# Politesse : délai aléatoire entre deux requêtes, jamais de parallélisme
DELAI_MIN_S = 3.0
DELAI_MAX_S = 5.0
TIMEOUT_S = 30
# Délais propres à un site (min, max), quand le site le permet (pas de Crawl-delay) et sur accord de l'utilisatrice
DELAIS_PAR_SITE = {
    "planfor": (1.0, 2.0),
}
# Une seule nouvelle tentative en cas de coupure réseau (pas pour 403/429)
PAUSE_AVANT_NOUVEL_ESSAI_S = 5.0

# Plafonds de lignes
# Par fiche produit (une fiche = une variété) : au-delà, formats les mieux répartis
MAX_LIGNES_PAR_PRODUIT = 100  # garde-fou contre un bug (formats répétés) ; en pratique tous les formats sont gardés
# Par plante et par pépinière : None = pas de limite (toutes les fiches, tous les formats en stock)
MAX_LIGNES_PAR_PLANTE = None
# Nombre maximal de fiches produit lues par plante (garde-fou)
MAX_FICHES_PAR_PLANTE = 1000

# Fichiers
FICHIER_PLANTES = "data/plantes_test.csv"
# Équivalences de noms de variétés (site -> Fiches)
FICHIER_EQUIVALENCES = "data/equivalences_varietes.csv"
# Noms latins écrits autrement sur les sites, validés par l'utilisatrice
FICHIER_EQUIVALENCES_LATIN = "data/equivalences_latin.csv"
DOSSIER_CACHE = "cache"
DOSSIER_LOGS = "logs"
