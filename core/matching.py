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
    if contient(tn, plante.genre) and variete_fiches(plante, f"{titre} {slug}"):
        return "genre+variete"
    if plante.genre_seul and contient(tn, plante.genre) and not autre_espece(plante, tn):
        return "genre seul"
    return None


def autre_espece(plante, texte_norm):
    """Vrai si le texte nomme une autre espèce du même genre présente dans la BDD."""
    return any(contient(texte_norm, n) for n in plante.autres_especes_du_genre)


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
