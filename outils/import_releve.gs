/**
 * Bouton « Importer un relevé » pour la Google Sheet BDD Plante.
 *
 * À coller une fois dans la Sheet : Extensions > Apps Script, remplacer le contenu de Code.gs,
 * enregistrer, puis recharger la Sheet. Un menu « Relevés » apparaît.
 *
 * Ce que fait l'import (sans que Claude ait à recopier les lignes) :
 *  1. lit sur GitHub releves/<ID>.json (fiche du relevé) et le TSV associé ;
 *  2. refuse si ce relevé figure déjà dans l'onglet « Relevés » ;
 *  3. si la même pépinière a déjà des lignes pour ces plantes dans « Offres » (mise à jour),
 *     demande confirmation puis supprime ces anciennes lignes ;
 *  4. ajoute toutes les nouvelles lignes en un seul bloc en bas de « Offres » ;
 *  5. ajoute une ligne dans « Relevés ».
 *
 * Le jeton GitHub (lecture seule, dépôt pickmyplant/scraper-pepiniere) est stocké dans les
 * propriétés du script via le menu « Relevés > Configurer le jeton GitHub ». Il n'est jamais écrit
 * dans la Sheet ni dans le dépôt.
 */

var DEPOT = 'pickmyplant/scraper-pepiniere';
var BRANCHE_PAR_DEFAUT = 'claude/vibrant-keller-jibr76';
var ONGLET_OFFRES = 'Offres';
var ONGLET_REGISTRE = 'Relevés';
var COLONNES = ['ID', 'Plante', 'Variété', 'Pépinière', 'Lien du produit', 'Prix', 'Devise', 'Contenant',
  'Disponibilité', 'Frais de port', 'Relevé le', 'Lien affilié', 'Statut', 'Notes', 'Nom latin',
  'Hauteur ou forme'];
var COL_PRIX = 5;   // index 0 dans COLONNES
var COL_DATE = 10;

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Relevés')
    .addItem('Importer un relevé…', 'importerReleve')
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
  if (entete.join('|') !== COLONNES.join('|')) {
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
      ui.alert('Le relevé ' + id + ' est déjà dans l\'onglet « Relevés ». Rien n\'a été importé.');
      return;
    }

    // 2. Mise à jour : anciennes lignes de cette pépinière pour ces plantes
    var plantes = {};
    fiche.plantes.forEach(function (p) { plantes[p] = true; });
    var nbOff = offres.getLastRow();
    var anciennes = [];
    if (nbOff > 1) {
      var colA = offres.getRange(2, 1, nbOff - 1, 1).getValues();
      var colD = offres.getRange(2, 4, nbOff - 1, 1).getValues();
      for (var i = 0; i < colA.length; i++) {
        if (plantes[String(colA[i][0]).trim()] && String(colD[i][0]).trim() === fiche.pepiniere) {
          anciennes.push(i + 2);
        }
      }
    }
    var message = lignes.length + ' ligne(s) ' + fiche.pepiniere + ' à ajouter (relevé ' + id + ').';
    if (anciennes.length) {
      message += '\n\nMISE À JOUR : ' + anciennes.length + ' ancienne(s) ligne(s) ' + fiche.pepiniere +
        ' pour ces plantes seront supprimées et remplacées.';
    }
    if (ui.alert('Confirmer l\'import', message + '\n\nContinuer ?', ui.ButtonSet.YES_NO) !== ui.Button.YES) return;

    plages_(anciennes).forEach(function (p) { offres.deleteRows(p[0], p[1]); });

    // 3. Ajout en un seul bloc
    var debut = offres.getLastRow() + 1;
    offres.getRange(debut, 1, lignes.length, COLONNES.length).setValues(lignes);
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

    ui.alert('Import terminé : lignes ' + debut + ' à ' + fin + ' de « Offres »' +
      (anciennes.length ? ', ' + anciennes.length + ' ancienne(s) ligne(s) remplacée(s)' : '') + '.');
  } catch (e) {
    ui.alert('Import interrompu, rien n\'a été écrit après l\'erreur : ' + e.message);
  } finally {
    verrou.releaseLock();
  }
}
