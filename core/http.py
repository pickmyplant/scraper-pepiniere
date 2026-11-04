"""Client HTTP poli : robots.txt, délai entre requêtes, arrêt net en cas de blocage."""
import logging
import random
import re
import time
from urllib.parse import urlsplit

import requests

import config

log = logging.getLogger("scraper")


class Bloque(Exception):
    """Le site refuse l'accès (403, 429, captcha) : on arrête tout."""


class InterditParRobots(Exception):
    pass


class Robots:
    """Lecture de robots.txt pour le groupe « * » (jokers * et $ gérés)."""

    def __init__(self, texte):
        self.regles = []  # (allow: bool, regex, longueur)
        groupe_actif = False
        dans_agents = False
        for ligne in texte.splitlines():
            ligne = ligne.split("#", 1)[0].strip()
            if ":" not in ligne:
                continue
            cle, val = (x.strip() for x in ligne.split(":", 1))
            cle = cle.lower()
            if cle == "user-agent":
                if not dans_agents:
                    groupe_actif = False
                dans_agents = True
                if val == "*" or val.lower() in config.USER_AGENT.lower():
                    groupe_actif = True
                continue
            dans_agents = False
            if not groupe_actif or cle not in ("allow", "disallow") or not val:
                continue
            if not val.startswith("/") and not val.startswith("*"):
                val = "/" + val
            motif = re.escape(val).replace(r"\*", ".*")
            if motif.endswith(r"\$"):
                motif = motif[:-2] + "$"
            self.regles.append((cle == "allow", re.compile(motif), len(val)))

    def autorise(self, url):
        p = urlsplit(url)
        chemin = p.path + ("?" + p.query if p.query else "")
        meilleur = None
        for allow, rx, lg in self.regles:
            if rx.match(chemin) and (meilleur is None or lg > meilleur[1] or (lg == meilleur[1] and allow)):
                meilleur = (allow, lg)
        return True if meilleur is None else meilleur[0]


class ClientPoli:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})
        self._derniere = 0.0
        self.nb_requetes = 0
        self.robots = Robots(self._get(self.base + "/robots.txt", verifier_robots=False).text)

    def _attendre(self):
        delai = random.uniform(config.DELAI_MIN_S, config.DELAI_MAX_S)
        reste = self._derniere + delai - time.time()
        if reste > 0:
            time.sleep(reste)

    def _get(self, url, verifier_robots=True):
        if verifier_robots and not self.robots.autorise(url):
            log.warning("ROBOTS interdit, non lu : %s", url)
            raise InterditParRobots(url)
        for essai in (1, 2):
            self._attendre()
            t0 = time.time()
            try:
                r = self.session.get(url, timeout=config.TIMEOUT_S)
            except requests.RequestException as e:
                self._derniere = time.time()
                self.nb_requetes += 1
                log.warning("ERREUR réseau (essai %d) %s : %s", essai, url, e.__class__.__name__)
                if essai == 1:
                    time.sleep(config.PAUSE_AVANT_NOUVEL_ESSAI_S)
                    continue
                raise
            self._derniere = time.time()
            self.nb_requetes += 1
            log.info("GET %s -> %s (%.1f s, %d o)", url, r.status_code, time.time() - t0, len(r.content))
            if r.status_code in (403, 429):
                raise Bloque(f"HTTP {r.status_code} sur {url}")
            if r.status_code >= 500 and essai == 1:
                time.sleep(config.PAUSE_AVANT_NOUVEL_ESSAI_S)
                continue
            r.raise_for_status()
            debut = r.text[:20000].lower()
            if ("captcha" in debut and "application/ld+json" not in r.text) or "cf-challenge" in debut:
                raise Bloque(f"Captcha / défi anti-robot sur {url}")
            return r

    def get(self, url):
        return self._get(url)
