"""Rapprochement plante / produit par nom latin et variétés de l'onglet Fiches."""
import re
import unicodedata

VARIETES_IGNOREES = {"espece type", "autres varietes"}
MOTS_MELANGE = ("collection", "duo", "trio", "kit", "melange", "assortiment", "mix")


def norm(texte):
    """Minuscules, sans accents ni ponctuation ; le signe hybride (× ou x isolé) est retiré."""
    t = re.sub(r"[’‘]", "'", texte or "")  # apostrophe typographique = apostrophe simple
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"(^| )x(?= )", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def contient(texte_norm, motif):
    m = norm(motif)
    return bool(m) and re.search(rf"(^| ){re.escape(m)}( |$)", texte_norm) is not None


def noms_latins(plante):
    """Nom accepté + synonymes latins de la BDD."""
    return [plante.nom_latin] + plante.synonymes_latins


def variete_fiches(plante, texte):
    """Variété de l'onglet Fiches citée dans le texte (nom ou synonyme), sinon None."""
    tn = norm(texte)
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
    if contient(tn, plante.genre) and variete_fiches(plante, f"{titre} {slug}") \
            and not autre_espece_nommee(plante, titre):
        return "genre+variete"
    if plante.genre_seul and contient(tn, plante.genre) and not autre_espece(plante, tn):
        return "genre seul"
    return None


SUFFIXES_LATINS = ("us", "a", "um", "is", "ii", "ae", "es", "ensis", "oides", "ana", "iana")
MOTS_FRANCAIS = {"nain", "naine", "panache", "panachee", "geant", "geante", "hybride", "rouge", "blanc",
                 "blanche", "jaune", "rose", "bleu", "bleue", "en", "de", "du", "des", "a", "la", "le",
                 "les", "et", "pot", "mini", "mottes", "graines", "racines", "tige", "nues", "plena"}


def autre_espece_nommee(plante, *textes):
    """Nom d'espèce du même genre écrit sur la fiche et qui n'est pas celui de la plante
    (ni un de ses synonymes) : « Yucca gloriosa Variegata » pour Yucca gigantea.
    Une autre espèce du même genre, qui n'est pas un vrai synonyme, n'est jamais retenue."""
    a_moi = {norm(n).split()[1] for n in noms_latins(plante) if len(norm(n).split()) > 1}
    genre = norm(plante.genre)
    for texte in textes:
        mots = re.findall(r"[^\s'’\"()\-,]+", texte or "")
        for k, g in enumerate(mots[:-1]):
            if norm(g) != genre:
                continue
            suivant = mots[k + 1]
            if suivant.lower() in ("x", "×") and k + 2 < len(mots):  # hybride : Musa x paradisiaca
                suivant = mots[k + 2]
            if not suivant[:1].islower():
                continue
            ep = norm(suivant)
            if ep in ("subsp", "var", "f", "cv") or ep in a_moi or ep in MOTS_FRANCAIS:
                continue
            if ep.endswith(SUFFIXES_LATINS) and len(ep) > 3:
                return suivant
    return None


def autre_espece(plante, texte_norm):
    """Vrai si le texte nomme une autre espèce du même genre présente dans la BDD."""
    return any(contient(texte_norm, n) for n in plante.autres_especes_du_genre)


def candidat_nom_commun(plante, titre):
    tn = norm(titre)
    return any(contient(tn, n) for n in [plante.nom_commun] + plante.autres_noms)


def autre_plante_dans_titre(plante, titre):
    """Un autre nom latin « Genre espèce » (genre différent) dans le titre d'un lot :
    « Duo 5 Lippia nodiflora et 5 Achillea crithmifolia » mélange deux plantes."""
    genre = norm(plante.genre)
    a_moi = {norm(n) for n in noms_latins(plante)}
    for g, ep in re.findall(r"\b([A-Z][a-z]+)\s+([a-z][a-z-]{3,})\b", titre or ""):
        if norm(f"{g} {ep}") in a_moi:
            continue
        if norm(g) != genre and ep.endswith(tuple(x for x in SUFFIXES_LATINS if x != "es")) \
                and norm(ep) not in MOTS_FRANCAIS:
            return f"{g} {ep}"
    return None


def est_melange(titre):
    tn = norm(titre)
    return any(contient(tn, m) for m in MOTS_MELANGE)


def _tokens(texte):
    """Mots du texte (les apostrophes séparent les mots), avec leur position de fin."""
    return [(m.group(0), m.end()) for m in re.finditer(r"[^\s'’]+", texte)]


def _apres(segment, motif):
    """Texte (casse et apostrophes d'origine) qui suit `motif` dans `segment`, ou None si absent.
    Les mots composés (« Laurier-rose ») sont comparés morceau par morceau."""
    toks = _tokens(segment)
    sous = [(m, i) for i, (t, _) in enumerate(toks) for m in norm(t).split()]
    cible = norm(motif).split()
    if not cible:
        return None
    for k in range(len(sous) - len(cible) + 1):
        if [m for m, _ in sous[k:k + len(cible)]] == cible:
            fin = toks[sous[k + len(cible) - 1][1]][1]
            return segment[fin:].strip(" -–,'’\"")
    return None


def variete_du_titre(plante, titre):
    """Renvoie (variété, présente_dans_Fiches). Variété vide = espèce type."""
    v = variete_fiches(plante, titre)
    if v:
        return v, True
    segments = [s.strip() for s in re.split(r"\s[-–]\s?|\s?[-–]\s|,", titre) if s.strip()]
    reste, segs_latins = None, set()
    # 1) Nom latin complet dans un segment ; 2) à défaut seulement, le genre seul
    # (« Yucca pied d'éléphant - Yucca elephantipes » : le nom latin complet l'emporte)
    for noms in (noms_latins(plante), [plante.genre]):
        for i, seg in enumerate(segments):
            for n in noms:
                r = _apres(seg, n)
                if r is not None:
                    reste = r
                    if not r and n != plante.genre:
                        segs_latins.add(i)
                    break
            if reste:
                break
        if reste or segs_latins:
            break
    # « Strelitzia reginae - Oiseau de Paradis » : nom latin en tête, la suite n'est qu'un
    # nom commun -> espèce type. « Laurier-rose Rouge Simple - Nerium oleander » : la variété
    # suit le nom commun.
    if not reste and 0 not in segs_latins:
        for i, seg in enumerate(segments):
            if i in segs_latins:
                continue
            for n in sorted([plante.nom_commun] + plante.autres_noms, key=len, reverse=True):
                r = _apres(seg, n)
                if r is not None:  # le nom commun le plus long qui correspond l'emporte
                    reste = r
                    break
            if reste is not None:
                break
    # Un nom commun de la plante entre parenthèses n'est pas une variété :
    # « Fruit de la passion (Grenadille) - Passiflora edulis » = espèce type
    communs = {norm(n) for n in [plante.nom_commun] + plante.autres_noms}
    reste = re.sub(r"\(([^)]*)\)", lambda m: "" if norm(m.group(1)) in communs else m.group(0), reste or "")
    reste = re.sub(r"\s+", " ", reste).strip(" '\"‘’")
    if not reste:
        return "", True  # espèce type
    return reste, False


def latin_sur_fiche(plante, textes):
    tn = norm(" ".join(textes))
    return any(contient(tn, n) for n in noms_latins(plante))
