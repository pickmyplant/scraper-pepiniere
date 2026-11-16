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
        self.crawl_delay = 0.0
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
            if groupe_actif and cle == "crawl-delay":
                try:
                    self.crawl_delay = max(self.crawl_delay, float(val))
                except ValueError:
                    pass
                continue
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
    def __init__(self, base_url, delai_min=None, delai_max=None):
        """Délais propres au site (sinon ceux de config) ; le Crawl-delay de robots.txt est toujours respecté."""
        self.base = base_url.rstrip("/")
        self.delai_min = config.DELAI_MIN_S if delai_min is None else delai_min
        self.delai_max = config.DELAI_MAX_S if delai_max is None else delai_max
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})
        self._derniere = 0.0
        self.nb_requetes = 0
        try:
            texte = self._get(self.base + "/robots.txt", verifier_robots=False).text
        except requests.HTTPError as e:
            # Pas de robots.txt (404) : tout est permis
            if e.response is None or e.response.status_code != 404:
                raise
            texte = ""
        self.robots = Robots(texte)
        if self.robots.crawl_delay > self.delai_min:
            log.info("robots.txt demande Crawl-delay %.0f s : délai relevé", self.robots.crawl_delay)
            self.delai_min = self.robots.crawl_delay
            self.delai_max = max(self.delai_max, self.delai_min + 1)

    def _attendre(self):
        delai = random.uniform(self.delai_min, self.delai_max)
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
            if r.status_code in (403, 429, 503):
                raise Bloque(f"HTTP {r.status_code} sur {url}")
            if r.status_code >= 500 and essai == 1:
                time.sleep(config.PAUSE_AVANT_NOUVEL_ESSAI_S)
                continue
            r.raise_for_status()
            # Les réglages reCAPTCHA invisible (v3) d'un formulaire ne sont pas un défi : on les ignore
            debut = re.sub(r'"recaptcha":\{"type":"v3"', "", r.text[:20000].lower())
            if ("captcha" in debut and "application/ld+json" not in r.text) or "cf-challenge" in debut:
                raise Bloque(f"Captcha / défi anti-robot sur {url}")
            return r

    def get(self, url):
        return self._get(url)
