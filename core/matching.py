"""Rapprochement plante / produit par nom latin et variétés de l'onglet Fiches."""
import re
import unicodedata

VARIETES_IGNOREES = {"espece type", "autres varietes"}
MOTS_MELANGE = ("collection", "duo", "trio", "kit", "melange", "assortiment", "mix")


def norm(texte):
    """Minuscules, sans accents ni ponctuation ; le signe hybride (× ou x isolé) est retiré."""
    t = unicodedata.normalize("NFKD", (texte or "").lower()).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"(^| )x(?= )", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def contient(texte_norm, motif):
    m = norm(motif)
    return bool(m) and re.search(rf"(^| ){re.escape(m)}( |$)", texte_norm) is not None


def noms_latins(plante):
    """Nom accepté + synonymes latins de la BDD + équivalences validées (data/equivalences_latin.csv)."""
    return [plante.nom_latin] + plante.synonymes_latins + list(getattr(plante, "equivalences_latin", []))


def equivalence_utilisee(plante, textes):
    """Équivalence validée trouvée sur la fiche alors que les noms de la BDD n'y sont pas (sinon None)."""
    tn = norm(" ".join(textes))
    if any(contient(tn, n) for n in [plante.nom_latin] + plante.synonymes_latins):
        return None
    for n in getattr(plante, "equivalences_latin", []):
        if contient(tn, n):
            return n
    return None


# Mots qui suivent parfois le genre sans être une autre espèce (botanique, français, anglais)
_PAS_UNE_ESPECE = set("""var subsp ssp cv hybride hybrides hybrid hybrida pour avec sans dans nain naine type variete
varietes groupe group arbre arbuste plante plantes demi blanc blanche rouge rose jaune pourpre panache panachee violet
orange mauve white pink yellow purple golden gold silver green black blue glorieux superbe commun commune japon chine
fleur fleurs double simple grand grande petit petite tige touffe boule multi ramifie bonsai topiaire html
fenouil japonais japonaise odeur feuilles feuille tropical tropicale garden magical little hortense communis
collection serie series""".split())


def autre_espece(plante, texte):
    """Épithète d'une autre espèce du même genre nommée dans le texte (« Prunus laurocerasus » pour
    Prunus avium), sinon None. Rien si l'espèce de la plante (ou un synonyme) est aussi citée, ni pour
    une plante « Genre Hybrides... » ou « Genre a / b »."""
    tn = norm(texte)
    genre = norm(plante.genre)
    esp = norm(plante.espece)
    if not genre or not esp or esp.split()[0] in ("hybrides", "hybride", "groupe") or "/" in plante.espece:
        return None
    epithetes = set()
    for n in noms_latins(plante):
        t = norm(n).split()
        if len(t) > 1 and t[0] == genre:
            epithetes.add(t[1])
    mots_varietes = set()
    for v in plante.varietes:
        for nom in [v.nom] + v.synonymes:
            mots_varietes.update(norm(nom).split())
    mots = tn.split()
    if not epithetes or any(e in mots for e in epithetes):
        return None
    proches = epithetes | mots_varietes
    for m in re.finditer(rf"(?:^| ){re.escape(genre)} ([a-z]+)", tn):
        e = m.group(1)
        if len(e) < 4 or e in _PAS_UNE_ESPECE:
            continue
        # faute de frappe sur l'espèce ou sur une variété (« fragans », « edlweiss ») : pas une autre espèce
        if any(_distance(e, w) <= 2 or (len(w) >= 5 and (w.startswith(e) or e.startswith(w)))
               for w in proches if len(w) >= 4):
            continue
        return e
    return None


def variete_fiches(plante, texte):
    """Variété de l'onglet Fiches citée dans le texte (nom ou synonyme), sinon None.
    None aussi quand le texte nomme une autre espèce du même genre (laurier-cerise 'Van Nes' ≠ cerisier 'Van')."""
    tn = norm(texte)
    if autre_espece(plante, texte):
        return None
    for v in plante.varietes:
        if norm(v.nom) in VARIETES_IGNOREES:
            continue
        for nom in [v.nom] + v.synonymes:
            if contient(tn, nom):
                return v.nom
    return None


def est_candidat(plante, titre, url):
    """Pré-sélection depuis le sitemap : Genre + espèce, ou Genre + une variété de Fiches."""
    slug = url.rsplit("/", 1)[-1].replace("-", " ")
    tn = norm(f"{titre} {slug}")
    if any(contient(tn, n) for n in noms_latins(plante)):
        return "latin"
    if contient(tn, plante.genre) and variete_fiches(plante, f"{titre} {slug}"):
        return "genre+variete"
    return None


def candidat_nom_commun(plante, titre):
    tn = norm(titre)
    return any(contient(tn, n) for n in [plante.nom_commun] + plante.autres_noms)


def est_melange(titre):
    tn = norm(titre)
    return any(contient(tn, m) for m in MOTS_MELANGE)


def _tokens(texte):
    return [t for t in re.split(r"\s+", texte.replace("'", " ").replace("’", " ")) if t]


def _apres(segment, motif):
    """Texte (casse d'origine) qui suit `motif` dans `segment`, ou None si absent.
    Les mots composés (« Laurier-rose ») sont comparés morceau par morceau."""
    toks = _tokens(segment)
    sous = [(m, i) for i, t in enumerate(toks) for m in norm(t).split()]
    cible = norm(motif).split()
    if not cible:
        return None
    for k in range(len(sous) - len(cible) + 1):
        if [m for m, _ in sous[k:k + len(cible)]] == cible:
            dernier = sous[k + len(cible) - 1][1]
            return " ".join(toks[dernier + 1:]).strip(" -–,'\"")
    return None


def variete_du_titre(plante, titre):
    """Renvoie (variété, présente_dans_Fiches). Variété vide = espèce type."""
    v = variete_fiches(plante, titre)
    if v:
        return v, True
    segments = [s.strip() for s in re.split(r"\s[-–]\s?|\s?[-–]\s|,", titre) if s.strip()]
    reste = None
    for seg in segments:
        for n in noms_latins(plante) + [plante.genre]:
            r = _apres(seg, n)
            if r is not None:
                reste = r
                break
        if reste:
            break
    if not reste:
        for seg in segments:
            for n in sorted([plante.nom_commun] + plante.autres_noms, key=len, reverse=True):
                r = _apres(seg, n)
                if r:
                    reste = r
                    break
            if reste:
                break
    reste = (reste or "").strip(" '\"‘’")
    if not reste:
        return "", True  # espèce type
    return reste, False


def latin_sur_fiche(plante, textes):
    tn = norm(" ".join(textes))
    return any(contient(tn, n) for n in noms_latins(plante))


def _distance(a, b):
    """Distance d'édition (Levenshtein) entre deux mots courts."""
    prec = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cour = [i]
        for j, cb in enumerate(b, 1):
            cour.append(min(prec[j] + 1, cour[j - 1] + 1, prec[j - 1] + (ca != cb)))
        prec = cour
    return prec[-1]


def latin_approche(plante, textes, tolerance=2):
    """Nom latin écrit un peu autrement sur la fiche (même genre, épithète à 1 ou 2 lettres près).
    Renvoie le nom tel qu'écrit sur la fiche, ou None."""
    genre = norm(plante.genre)
    espece = norm(plante.espece)
    if not genre or len(espece) < 5:
        return None
    mots = norm(" ".join(textes)).split()
    for i in range(len(mots) - 1):
        if mots[i] == genre and 0 < _distance(mots[i + 1], espece) <= tolerance:
            return f"{plante.genre} {mots[i + 1]}"
    return None
