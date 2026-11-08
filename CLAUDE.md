# Consignes pour toute session Claude sur ce dépôt

Tout le travail est sur la branche `claude/vibrant-keller-jibr76` (`main` ne contient presque rien).

1. Au démarrage : `git fetch origin claude/vibrant-keller-jibr76` puis `git checkout claude/vibrant-keller-jibr76`
   (ou la fusionner dans la branche de la conversation). C'est la branche commune : elle contient tous les adaptateurs.
   **Toujours y publier le travail** avec `outils/publier.sh` après le commit (`outils/lot.sh` le fait tout seul).
2. Lire **seulement `DEMARRAGE.md`** sur cette branche. PASSATION.md et le README uniquement en cas de doute précis.
3. Sheet de coordination `1QfeIyLnsM3EMQf_cc1KmPNYQ1xJJzRG_kYJr5ZfIFIM` : lire seulement Règles, Réservations, Questions.
4. Extraire un lot sans lire la BDD : `python outils/extraire_lot.py P0xx P0yy` (lien de l'onglet Export dans
   `BDD_EXPORT_URL`, sinon le demander à l'utilisatrice ; ne jamais le commiter). Lancer : `outils/lot.sh <ID> <CSV>`.
   Nouvelle pépinière : section « Nouvelle pépinière » de DEMARRAGE.md (un seul fichier `adapters/<nom>.py`).
5. Ne jamais écrire dans l'onglet Offres (l'utilisatrice importe avec le bouton). Rien de secret ni de personnel dans les commits.
6. Réponses concises : un résumé court et seulement les cas à vérifier.
