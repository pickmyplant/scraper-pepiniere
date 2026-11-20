/**
 * Bouton « Importer un relevé » pour la Google Sheet BDD Plante.
 *
 * À coller une fois dans la Sheet : Extensions > Apps Script, remplacer le contenu de Code.gs,
 * enregistrer, puis recharger la Sheet. Un menu « Relevés » apparaît.
 *
 * Ce que fait l'import (sans que Claude ait à recopier les lignes) :
 *  1. lit sur GitHub releves/<ID>.json (fiche du relevé) et le TSV associé ;
 *  2. refuse si ce relevé figure déjà dans l'onglet « Relevés » ;
 *  3. si la même pépinière (ou « <pépinière> - <fournisseur> ») a déjà des lignes pour ces plantes dans « Offres » (mise à jour),
 *     demande confirmation puis supprime ces anciennes lignes ;
 *  4. ajoute toutes les nouvelles lignes en un seul bloc en bas de « Offres » ;
 *  5. ajoute une ligne dans « Relevés » ;
 *  6. ajoute une ligne « Terminé » dans l'onglet Réservations de la Sheet de coordination
 *     (si l'écriture échoue, l'import reste fait et le message le signale).
 *
 * Le jeton GitHub (lecture seule, dépôt pickmyplant/scraper-pepiniere) est stocké dans les
 * propriétés du script via le menu « Relevés > Configurer le jeton GitHub ». Il n'est jamais écrit
 * dans la Sheet ni dans le dépôt.
 */

var DEPOT = 'pickmyplant/scraper-pepiniere';
var BRANCHE_PAR_DEFAUT = 'claude/vibrant-keller-jibr76';
var ONGLET_OFFRES = 'Offres';
var ONGLET_REGISTRE = 'Relevés';
var COORDINATION_ID = '1QfeIyLnsM3EMQf_cc1KmPNYQ1xJJzRG_kYJr5ZfIFIM';
var ONGLET_RESERVATIONS = 'Réservations';
var COLONNES = ['ID', 'Plante', 'Variété', 'Pépinière', 'Lien du produit', 'Prix', 'Devise', 'Contenant',
  'Disponibilité', 'Frais de port', 'Relevé le', 'Lien affilié', 'Statut', 'Notes', 'Nom latin',
  'Hauteur ou forme', 'Lot', 'Expédié de'];   // Lot (Q, 19/11/2026) : « N ; prix du lot » si vendu seulement par lot ;
  // Expédié de (R, 20/11/2026) : lieu d'expédition (Plantes Avenue, un port par lieu)
var COL_PRIX = 5;   // index 0 dans COLONNES
var COL_DATE = 10;
var COL_FRAIS = 9;  // colonne J : calculée par une formule ARRAYFORMULA en J1, jamais écrite par le script

/**
 * Dernière ligne réellement remplie, d'après la colonne A (ID). getLastRow() compte aussi les
 * cellules où une formule renvoie "" (ex. formule tirée jusqu'en bas de la colonne J).
 */

/**
 * Lignes d'Offres de la pépinière du relevé : même nom, ou « <pépinière> - <fournisseur> » (20/11/2026 :
 * Botanic note « Botanic - La Bambouseraie » pour les plantes livrées par un fournisseur ; ces lignes sont
 * remplacées avec le relevé Botanic).
 */
function memePepiniere_(nom, pepiniere) {
  return nom === pepiniere || nom.indexOf(pepiniere + ' - ') === 0;
}

function derniereLigne_(onglet) {
  var max = onglet.getMaxRows();
  var col = onglet.getRange(1, 1, max, 1).getValues();
  for (var i = max - 1; i >= 0; i--) {
    if (String(col[i][0]).trim() !== '') return i + 1;
  }
  return 1;
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Relevés')
    .addItem('Importer un relevé…', 'importerReleve')
    .addItem('Appliquer des corrections d\'offres…', 'appliquerCorrections')
    .addItem('Ajouter des variétés aux fiches…', 'ajouterVarietes')
    .addItem('Activer l\'import automatique', 'activerImportAuto')
    .addItem('Arrêter l\'import automatique', 'arreterImportAuto')
    .addItem('Configurer le jeton GitHub…', 'configurerJeton')
    .addToUi();
}

function configurerJeton() {
  var ui = SpreadsheetApp.getUi();
  var r = ui.prompt('Jeton GitHub',
    'Collez un jeton GitHub en lecture seule (« fine-grained token », accès Contents : Read-only, ' +
    'dépôt ' + DEPOT + ').', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  PropertiesService.getScriptProperties().setProperty('GITHUB_TOKEN', r.getResponseText().trim());
  ui.alert('Jeton enregistré dans les propriétés du script.');
}

function lireGithub_(chemin, branche) {
  var url = 'https://api.github.com/repos/' + DEPOT + '/contents/' + encodeURI(chemin) +
    '?ref=' + encodeURIComponent(branche);
  var rep = UrlFetchApp.fetch(url, {headers: enTetes_(), muteHttpExceptions: true});
  if (rep.getResponseCode() !== 200) {
    throw new Error('GitHub ' + rep.getResponseCode() + ' pour ' + chemin + ' (branche ' + branche + ')');
  }
  return rep.getContentText('UTF-8');
}

function convertirLigne_(cellules) {
  var v = cellules.slice(0, COLONNES.length);
  while (v.length < COLONNES.length) v.push('');
  var prix = String(v[COL_PRIX]).trim();
  if (prix) v[COL_PRIX] = Number(prix.replace(',', '.'));
  var d = String(v[COL_DATE]).match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (d) v[COL_DATE] = new Date(Number(d[3]), Number(d[2]) - 1, Number(d[1]), 12);  // midi : pas de décalage de fuseau
  return v;
}

function lireTsv_(texte) {
  var lignes = texte.replace(/\r/g, '').split('\n').filter(function (l) { return l.length > 0; });
  var entete = lignes[0].split('\t');
  // les relevés d'avant les colonnes Lot et Expédié de (16 ou 17 colonnes) restent importables : colonnes vides
  if ([0, 1, 2].every(function (k) { return entete.join('|') !== COLONNES.slice(0, COLONNES.length - k).join('|'); })) {
    throw new Error('En-tête du TSV inattendu : ' + entete.join(', '));
  }
  return lignes.slice(1).map(function (l) { return convertirLigne_(l.split('\t')); });
}

function plages_(numeros) {
  // [5,6,7,10] -> [[5,3],[10,1]] (début, nombre), triées de bas en haut pour la suppression
  numeros.sort(function (a, b) { return a - b; });
  var res = [];
  numeros.forEach(function (n) {
    var der = res[res.length - 1];
    if (der && n === der[0] + der[1]) der[1]++; else res.push([n, 1]);
  });
  return res.reverse();
}

function enTetes_() {
  var jeton = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!jeton) throw new Error('Jeton GitHub absent : menu Relevés > Configurer le jeton GitHub.');
  return {Authorization: 'Bearer ' + jeton, Accept: 'application/vnd.github.raw'};
}

/** Toutes les branches du dépôt (chaque conversation Claude pousse sur la sienne). */
function listerBranches_() {
  var noms = [], page = 1;
  while (page < 20) {
    var rep = UrlFetchApp.fetch('https://api.github.com/repos/' + DEPOT + '/branches?per_page=100&page=' + page,
      {headers: enTetes_(), muteHttpExceptions: true});
    if (rep.getResponseCode() !== 200) throw new Error('GitHub ' + rep.getResponseCode() + ' (liste des branches)');
    var lot = JSON.parse(rep.getContentText('UTF-8'));
    lot.forEach(function (b) { noms.push(b.name); });
    if (lot.length < 100) break;
    page++;
  }
  return noms;
}

/**
 * Trouve la branche qui contient releves/<id>.json : branche par défaut d'abord, sinon toutes les
 * branches (requêtes groupées). Si plusieurs branches ont des versions différentes, on demande ID@branche.
 */
function trouverReleve_(id) {
  var chemin = 'releves/' + id + '.json';
  try {
    return {branche: BRANCHE_PAR_DEFAUT, fiche: JSON.parse(lireGithub_(chemin, BRANCHE_PAR_DEFAUT))};
  } catch (e) { /* pas sur la branche par défaut : on cherche ailleurs */ }
  var branches = listerBranches_().filter(function (b) { return b !== BRANCHE_PAR_DEFAUT; });
  var tetes = enTetes_();
  var reps = UrlFetchApp.fetchAll(branches.map(function (b) {
    return {url: 'https://api.github.com/repos/' + DEPOT + '/contents/' + encodeURI(chemin) +
      '?ref=' + encodeURIComponent(b), headers: tetes, muteHttpExceptions: true};
  }));
  var trouves = [], versions = {};
  reps.forEach(function (rep, i) {
    if (rep.getResponseCode() !== 200) return;
    var texte = rep.getContentText('UTF-8');
    trouves.push({branche: branches[i], fiche: JSON.parse(texte)});
    versions[texte] = true;
  });
  if (!trouves.length) throw new Error('Relevé ' + id + ' introuvable sur toutes les branches (pas encore poussé ?)');
  if (Object.keys(versions).length > 1) {
    throw new Error('Plusieurs versions différentes de ' + id + ' : saisir ID@branche parmi ' +
      trouves.map(function (t) { return t.branche; }).join(', '));
  }
  // Même contenu partout : on préfère la branche où le relevé a été fait.
  var origine = trouves.filter(function (t) { return t.branche === t.fiche.branche; })[0];
  return origine || trouves[0];
}

/**
 * Ajoute une ligne « Terminé » dans Réservations (Sheet de coordination), en reprenant la
 * pépinière et les plantes de la dernière réservation qui cite cet identifiant.
 */
function marquerTermine_(id, fiche, debut, fin) {
  var onglet = SpreadsheetApp.openById(COORDINATION_ID).getSheetByName(ONGLET_RESERVATIONS);
  if (!onglet) throw new Error('onglet ' + ONGLET_RESERVATIONS + ' introuvable');
  var pepiniere = fiche.pepiniere, plantes = '';
  var n = onglet.getLastRow();
  if (n > 1) {
    var lignes = onglet.getRange(2, 1, n - 1, 6).getDisplayValues();
    for (var i = lignes.length - 1; i >= 0; i--) {
      if (lignes[i][5].indexOf(id) >= 0) { pepiniere = lignes[i][2]; plantes = lignes[i][3]; break; }
    }
  }
  if (!plantes) {
    var ps = fiche.plantes.slice().sort();
    plantes = ps.length > 1 ? ps[0] + ' à ' + ps[ps.length - 1] : ps.join('');
  }
  var date = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'dd/MM/yyyy');
  onglet.appendRow([date, fiche.par, pepiniere, plantes, 'Terminé',
    'Offres ' + debut + '-' + fin + ' (' + id + '), importé par le bouton']);
  onglet.getRange(onglet.getLastRow(), 1).setNumberFormat('@').setValue(date);
}

function importerReleve() {
  var ui = SpreadsheetApp.getUi();
  var r = ui.prompt('Importer un relevé',
    'Identifiant du relevé (ex. PLF-2026-11-06-A4). La branche est trouvée automatiquement ' +
    '(ID@branche pour la forcer).', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  // Tolère les copier-coller : parenthèses, guillemets, crochets, espaces, « .json » final.
  var saisie = r.getResponseText().replace(/[()\[\]{}"'`«»\s]/g, '').replace(/\.json$/, '');
  var id = saisie.split('@')[0];
  var brancheForcee = saisie.indexOf('@') > 0 ? saisie.split('@')[1] : '';
  importerUn_(id, brancheForcee, ui);
}

/** Import d'un relevé ; ui absent = import automatique, sans question (voir importerNouveauxReleves). */
function importerUn_(id, brancheForcee, ui) {
  var verrou = LockService.getDocumentLock();
  verrou.waitLock(30000);
  try {
    var classeur = SpreadsheetApp.getActiveSpreadsheet();
    var offres = classeur.getSheetByName(ONGLET_OFFRES);
    var registre = classeur.getSheetByName(ONGLET_REGISTRE);

    var trouve = brancheForcee
      ? {branche: brancheForcee, fiche: JSON.parse(lireGithub_('releves/' + id + '.json', brancheForcee))}
      : trouverReleve_(id);
    var branche = trouve.branche, fiche = trouve.fiche;
    var lignes = lireTsv_(lireGithub_(fiche.fichier, branche));
    if (lignes.length !== fiche.nb_lignes) {
      throw new Error('Le TSV contient ' + lignes.length + ' lignes, la fiche en annonce ' + fiche.nb_lignes);
    }

    // 1. Relevé déjà importé ?
    var nbReg = registre.getLastRow();
    var ids = nbReg > 1 ? registre.getRange(2, 1, nbReg - 1, 1).getValues().map(function (x) { return x[0]; }) : [];
    if (ids.indexOf(id) >= 0) {
      if (ui) ui.alert('Le relevé ' + id + ' est déjà dans l\'onglet « Relevés ». Rien n\'a été importé.');
      return 'déjà importé';
    }

    // 2. Mise à jour : anciennes lignes de cette pépinière pour ces plantes
    var plantes = {};
    fiche.plantes.forEach(function (p) { plantes[p] = true; });
    var nbOff = derniereLigne_(offres);
    var anciennes = [];
    if (nbOff > 1) {
      var colA = offres.getRange(2, 1, nbOff - 1, 1).getValues();
      var colD = offres.getRange(2, 4, nbOff - 1, 1).getValues();
      for (var i = 0; i < colA.length; i++) {
        if (plantes[String(colA[i][0]).trim()] && memePepiniere_(String(colD[i][0]).trim(), fiche.pepiniere)) {
          anciennes.push(i + 2);
        }
      }
    }
    var message = lignes.length + ' ligne(s) ' + fiche.pepiniere + ' à ajouter (relevé ' + id + ').';
    if (anciennes.length) {
      message += '\n\nMISE À JOUR : ' + anciennes.length + ' ancienne(s) ligne(s) ' + fiche.pepiniere +
        ' pour ces plantes seront supprimées et remplacées.';
    }
    if (ui && ui.alert('Confirmer l\'import', message + '\n\nContinuer ?', ui.ButtonSet.YES_NO) !== ui.Button.YES) return 'annulé';

    plages_(anciennes).forEach(function (p) { offres.deleteRows(p[0], p[1]); });

    // 3. Ajout en un seul bloc
    var debut = derniereLigne_(offres) + 1;
    if (debut + lignes.length - 1 > offres.getMaxRows()) {
      offres.insertRowsAfter(offres.getMaxRows(), debut + lignes.length - 1 - offres.getMaxRows());
    }
    // A..I puis K..fin : la colonne J (frais de port) est laissée à la formule de J1.
    offres.getRange(debut, 1, lignes.length, COL_FRAIS)
      .setValues(lignes.map(function (l) { return l.slice(0, COL_FRAIS); }));
    offres.getRange(debut, COL_FRAIS + 2, lignes.length, COLONNES.length - COL_FRAIS - 1)
      .setValues(lignes.map(function (l) { return l.slice(COL_FRAIS + 1); }));
    offres.getRange(debut, COL_DATE + 1, lignes.length, 1).setNumberFormat('dd/MM/yyyy');
    var fin = debut + lignes.length - 1;

    // 4. Registre
    var remarques = fiche.remarques || '';
    if (anciennes.length) {
      remarques = (remarques ? remarques + ' ' : '') + 'Mise à jour : ' + anciennes.length +
        ' ancienne(s) ligne(s) ' + fiche.pepiniere + ' supprimée(s).';
    }
    var d = fiche.date.split('/');
    registre.appendRow([id, fiche.pepiniere, new Date(Number(d[2]), Number(d[1]) - 1, Number(d[0]), 12),
      fiche.par, fiche.plantes.join(', '), debut + '-' + fin, lignes.length,
      fiche.fichier + ' (branche ' + branche + ')', remarques]);
    registre.getRange(registre.getLastRow(), 3).setNumberFormat('dd/MM/yyyy');

    var reservation;
    try {
      marquerTermine_(id, fiche, debut, fin);
      reservation = '\nRéservations : ligne « Terminé » ajoutée.';
    } catch (e2) {
      reservation = '\nRéservations : ligne « Terminé » NON ajoutée (' + e2.message + ').';
    }

    if (ui) ui.alert('Import terminé : lignes ' + debut + ' à ' + fin + ' de « Offres »' +
      (anciennes.length ? ', ' + anciennes.length + ' ancienne(s) ligne(s) remplacée(s)' : '') + '.' +
      reservation);
    return 'importé';
  } catch (e) {
    if (!ui) throw e;
    ui.alert('Import interrompu, rien n\'a été écrit après l\'erreur : ' + e.message);
  } finally {
    verrou.releaseLock();
  }
}


/* ------------------------------------------------------------------------------------------------
 * Corrections de l'onglet Offres et ajout de variétés dans Fiches (fichiers corrections/<ID>*.tsv,
 * produits par outils/corriger_offres.py et outils/decrire_varietes.py, sur la branche commune).
 * ---------------------------------------------------------------------------------------------- */

function demanderCorrection_(titre) {
  var ui = SpreadsheetApp.getUi();
  var r = ui.prompt(titre, 'Identifiant des corrections (ex. CORR-2026-11-15-A1) :', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return null;
  return r.getResponseText().trim();
}

function tableau_(texte) {
  return texte.split('\n').filter(function (l) { return l !== ''; }).map(function (l) { return l.split('\t'); });
}

function dejaFait_(cle) {
  return PropertiesService.getScriptProperties().getProperty(cle) === 'oui';
}

/**
 * Modifie ou supprime des lignes d'Offres. Une ligne est reconnue par ID + Pépinière + Lien + Variété + Hauteur ou forme.
 * Seules les colonnes ID, Plante, Variété, Statut, Notes (ajouts), Nom latin, Hauteur ou forme sont réécrites
 * (prix, dates et frais de port ne sont pas touchés). Crée aussi les fiches d'hybrides de <ID>_plantes.tsv.
 */
function appliquerCorrections() {
  var ui = SpreadsheetApp.getUi();
  var id = demanderCorrection_('Corrections d\'offres');
  if (!id) return;
  if (dejaFait_('CORR_' + id)) { ui.alert('Ces corrections ont déjà été appliquées.'); return; }
  var verrou = LockService.getDocumentLock();
  verrou.waitLock(30000);
  try {
    var corr = tableau_(lireGithub_('corrections/' + id + '.tsv', BRANCHE_PAR_DEFAUT)).slice(1);
    var table = {};
    corr.forEach(function (c) { while (c.length < 15) c.push(''); table[c.slice(0, 5).join('\u0001')] = c; });
    var classeur = SpreadsheetApp.getActive();
    var offres = classeur.getSheetByName(ONGLET_OFFRES);
    var n = derniereLigne_(offres) - 1;
    var plage = offres.getRange(2, 1, n, COLONNES.length);
    var vues = plage.getDisplayValues();
    var gauche = offres.getRange(2, 1, n, 3), droite = offres.getRange(2, 13, n, 4);
    var g = gauche.getValues(), d = droite.getValues();
    var modifs = 0, aSupprimer = [];
    for (var i = 0; i < n; i++) {
      var v = vues[i];
      var c = table[[v[0], v[3], v[4], v[2], v[15]].join('\u0001')];
      if (!c) continue;
      if (c[5] === 'SUPPR') { aSupprimer.push(i + 2); continue; }
      g[i] = [c[6], c[7], c[8]];
      var notes = String(d[i][1]).split(';').map(function (x) { return x.trim(); }).filter(function (x) {
        return x && !(c[13] === 'oui' && x.indexOf('Variété trouvée') === 0) &&
          !(c[6] !== v[0] && x.indexOf('Autre plante') === 0);
      });
      c[10].split(' ; ').forEach(function (x) { if (x && notes.indexOf(x) < 0) notes.push(x); });
      d[i] = [c[9] || d[i][0], notes.join(' ; '), c[11], c[12]];
      modifs++;
    }
    var rep = ui.alert('Corrections ' + id, modifs + ' lignes à modifier et ' + aSupprimer.length +
      ' à supprimer dans Offres (sur ' + corr.length + ' corrections prévues). Continuer ?', ui.ButtonSet.OK_CANCEL);
    if (rep !== ui.Button.OK) return;
    offres.getRange(2, 16, n, 1).setNumberFormat('@');
    gauche.setValues(g);
    droite.setValues(d);
    for (var k = aSupprimer.length - 1; k >= 0; k--) offres.deleteRow(aSupprimer[k]);
    var creees = creerFiches_(id);
    PropertiesService.getScriptProperties().setProperty('CORR_' + id, 'oui');
    ui.alert('Terminé : ' + modifs + ' lignes modifiées, ' + aSupprimer.length + ' supprimées, ' +
      creees + ' fiche(s) créée(s) dans Plantes et Fiches.');
  } catch (e) {
    ui.alert('Corrections interrompues : ' + e.message);
  } finally {
    verrou.releaseLock();
  }
}

/** Ajoute en bas de Plantes et de Fiches les fiches de <ID>_plantes.tsv qui n'existent pas encore (ligne minimale). */
function creerFiches_(id) {
  var lignes;
  try { lignes = tableau_(lireGithub_('corrections/' + id + '_plantes.tsv', BRANCHE_PAR_DEFAUT)).slice(1); }
  catch (e) { return 0; }
  var classeur = SpreadsheetApp.getActive();
  var plantes = classeur.getSheetByName('Plantes'), fiches = classeur.getSheetByName('Fiches');
  var titres = plantes.getRange(1, 1, 1, plantes.getLastColumn()).getValues()[0];
  var col = function (nom) { return titres.indexOf(nom) + 1; };
  var nP = derniereLigne_(plantes), nF = derniereLigne_(fiches);
  var existants = plantes.getRange(1, 1, nP, 1).getValues().map(function (x) { return String(x[0]); });
  var creees = 0;
  lignes.forEach(function (l) {
    if (existants.indexOf(l[0]) >= 0) return;
    nP++; nF++; creees++;
    plantes.getRange(nP, 1).setValue(l[0]);
    plantes.getRange(nP, col('Nom commun')).setValue(l[1]);
    plantes.getRange(nP, col('Genre')).setValue(l[2]);
    plantes.getRange(nP, col('Espèce')).setValue(l[3]);
    plantes.getRange(nP, col('Famille')).setValue(l[4]);
    fiches.getRange(nF, 1, 1, 2).setValues([[l[0], l[1]]]);
  });
  return creees;
}

/** Ajoute dans la colonne Variétés de Fiches les lignes de <ID>_fiches.tsv (une variété déjà présente n'est pas ajoutée). */
function ajouterVarietes() {
  var ui = SpreadsheetApp.getUi();
  var id = demanderCorrection_('Variétés à ajouter aux fiches');
  if (!id) return;
  if (dejaFait_('VAR_' + id)) { ui.alert('Ces variétés ont déjà été ajoutées.'); return; }
  var verrou = LockService.getDocumentLock();
  verrou.waitLock(30000);
  try {
    var lignes = tableau_(lireGithub_('corrections/' + id + '_fiches.tsv', BRANCHE_PAR_DEFAUT)).slice(1);
    var fiches = SpreadsheetApp.getActive().getSheetByName('Fiches');
    var titres = fiches.getRange(1, 1, 1, fiches.getLastColumn()).getValues()[0];
    var colonne = titres.indexOf('Variétés') + 1;
    if (!colonne) throw new Error('Colonne « Variétés » introuvable dans Fiches.');
    var n = derniereLigne_(fiches) - 1;
    var ids = fiches.getRange(2, 1, n, 1).getValues().map(function (x) { return String(x[0]); });
    var plage = fiches.getRange(2, colonne, n, 1);
    var cellules = plage.getValues();
    var simple = function (s) {
      return String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
    };
    var ajoutees = 0, ignorees = 0, pleines = {};
    lignes.forEach(function (l) {
      var i = ids.indexOf(l[0]);
      if (i < 0) { ignorees++; return; }
      var texte = String(cellules[i][0]);
      var noms = texte.split('\n').map(function (x) { return simple(x.split(' = ')[0]); });
      if (noms.indexOf(simple(l[1])) >= 0) { ignorees++; return; }
      if (texte.length + l[2].length + 1 > 49000) { pleines[l[0]] = true; ignorees++; return; }
      cellules[i][0] = texte ? texte + '\n' + l[2] : l[2];
      ajoutees++;
    });
    var rep = ui.alert('Variétés ' + id, ajoutees + ' variétés à ajouter dans Fiches (' + ignorees +
      ' ignorées : déjà présentes, fiche absente ou cellule pleine). Continuer ?', ui.ButtonSet.OK_CANCEL);
    if (rep !== ui.Button.OK) return;
    plage.setValues(cellules);
    PropertiesService.getScriptProperties().setProperty('VAR_' + id, 'oui');
    var p = Object.keys(pleines);
    ui.alert('Terminé : ' + ajoutees + ' variétés ajoutées.' + (p.length ? ' Cellules pleines (limite de 50 000 caractères) : ' + p.join(', ') : ''));
  } catch (e) {
    ui.alert('Ajout interrompu : ' + e.message);
  } finally {
    verrou.releaseLock();
  }
}


/* ------------------------------------------------------------------------------------------------
 * Import automatique : le relevé programmé (GitHub Actions) écrit la liste de ses relevés dans
 * releves/a_importer.txt ; toutes les heures, les relevés de cette liste absents du registre sont importés.
 * ---------------------------------------------------------------------------------------------- */

function activerImportAuto() {
  arreterImportAuto();
  ScriptApp.newTrigger('importerNouveauxReleves').timeBased().everyHours(1).create();
  SpreadsheetApp.getUi().alert('Import automatique activé : vérification toutes les heures.');
}

function arreterImportAuto() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'importerNouveauxReleves') ScriptApp.deleteTrigger(t);
  });
}

function importerNouveauxReleves() {
  var debut = Date.now(), liste;
  try { liste = lireGithub_('releves/a_importer.txt', BRANCHE_PAR_DEFAUT); } catch (e) { return; }
  var registre = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(ONGLET_REGISTRE);
  var n = registre.getLastRow();
  var faits = n > 1 ? registre.getRange(2, 1, n - 1, 1).getValues().map(function (x) { return String(x[0]); }) : [];
  var ids = liste.split('\n').map(function (x) { return x.trim(); }).filter(function (x) { return x && faits.indexOf(x) < 0; });
  for (var i = 0; i < ids.length; i++) {
    if (Date.now() - debut > 4 * 60 * 1000) return;   // la suite à la prochaine heure (limite de 6 minutes)
    try { importerUn_(ids[i], BRANCHE_PAR_DEFAUT, null); }
    catch (e) { console.error('Import automatique de ' + ids[i] + ' : ' + e.message); }
  }
}
