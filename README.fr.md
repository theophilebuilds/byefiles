# BYE Files

*Bin Your Extras Files* (« jette tes fichiers en trop »)

**Trie un dossier en bazar (Téléchargements, Bureau, captures d'écran…) avec l'IA d'Apple, directement sur le Mac. Rien ne quitte ta machine.**

[English version](README.md)

BYE Files lit chaque fichier d'un dossier (captures, PDF, documents Office et Pages, textes, vidéos, installeurs…), comprend de quoi il parle et le range dans quatre catégories :

| | |
|---|---|
| 🗑️ **Supprimables** | démarches terminées, inspiration ponctuelle, rafales de captures, réglages, achats, vieux installeurs |
| 🤔 **À vérifier** | projets de quelques semaines ou mois, photos perso, fichiers illisibles |
| ✅ **À garder** | projets récents, billets et rendez-vous à venir, factures, contrats, CV… |
| ⚠️ **Sensible** | pièces d'identité, IBAN, clés d'API, mots de passe, codes de récupération |

**Ça s'ouvre dans ton navigateur.** Lancer `byefiles` démarre un petit serveur web sur ton Mac (port 8765 par défaut) et ouvre `http://127.0.0.1:8765`, où tu valides tout : aperçus, sélection en un clic, Corbeille, annulation. Le serveur tourne seulement sur ton Mac, il est inaccessible depuis les autres appareils et s'arrête quand tu fais Ctrl+C dans le terminal.

## Prérequis

- Mac Apple Silicon, macOS 26 ou plus récent, **Apple Intelligence activé**

## Installation

```sh
brew install theophilebuilds/tap/byefiles
```

Mise à jour : `brew upgrade byefiles`.

<details>
<summary>Depuis le code source (demande l'app Xcode complète, pas seulement les outils en ligne de commande)</summary>

```sh
git clone https://github.com/theophilebuilds/byefiles.git ~/Projects/byefiles
cd ~/Projects/byefiles
mkdir -p bin && swiftc -O analyze.swift -o bin/byefiles-analyze
python3 -I byefiles.py ~/Downloads
```

Le modèle d'Apple a besoin d'un module du compilateur qui n'est fourni qu'avec Xcode.
</details>

## Utilisation

```sh
byefiles                  # dossier courant
byefiles ~/Downloads      # un autre dossier
byefiles --reanalyze      # refait ce dossier de zéro
```

Options : `--port N` (8765 par défaut), `--no-open` (n'ouvre pas le navigateur). Seuls les fichiers à la racine du dossier sont triés, pas les sous-dossiers.

**Patience au premier lancement.** L'IA prend environ 1 seconde par fichier : un dossier Téléchargements de 400 fichiers demande 5 à 7 minutes. La page s'ouvre tout de suite et se remplit au fur et à mesure, tu peux donc commencer à trier avant la fin.

**Conseil : fais-le en plusieurs fois.** Tu peux arrêter à tout moment avec Ctrl+C. Les résultats sont gardés en cache : au lancement suivant, BYE Files reprend là où il s'était arrêté, et les fichiers déjà lus s'affichent tout de suite.

**Raccourcis quand un fichier est ouvert :** `S` le sélectionne (rappuie pour le désélectionner), `←` `→` passent au fichier précédent ou suivant, `Échap` ou un clic à côté du fichier le ferme, `⌘Z` annule la dernière mise à la Corbeille.

## Comment ça marche

1. **Lecture** (Swift) : texte avec PDFKit, OCR avec Vision, aperçus Quick Look, et une empreinte visuelle pour les images.
2. **Groupes** (Python) : les fichiers qui montrent la même chose ne sont analysés qu'une fois (rafales de captures, copies comme `fichier (1).pdf`, images quasi identiques). Les installeurs, les photos sans texte et les fichiers sensibles sont classés par des règles, sans l'IA.
3. **Analyse** (Swift) : le modèle d'Apple intégré au Mac (Foundation Models) donne un sujet, un type de contenu, et dit s'il y a une date à venir. Il commence pendant que les fichiers sont encore en lecture.
4. **Tri** (Python) : des règles fixes et lisibles, dans `decide()`, choisissent la catégorie selon le type et l'âge du fichier. **L'IA décrit, les règles décident** : chaque choix est expliqué (ex. « démarche ancienne (142 j), sans doute terminée »).

Les données sensibles (IBAN, passeport, clés d'API, clés privées, jetons…) sont repérées par des expressions régulières, avant l'IA et sans dépendre d'elle.

**Confidentialité et sécurité :** le serveur n'écoute que sur `127.0.0.1`, les actions demandent un jeton créé à chaque lancement, et les fichiers du dossier ne sont jamais exécutés dans la page. Supprimer, c'est mettre à la Corbeille de macOS, et « Annuler » remet les fichiers en place.

## Fichiers

| | |
|---|---|
| `analyze.swift` | moteur natif : texte, OCR, aperçus, modèle d'Apple (compilé en `bin/byefiles-analyze`) |
| `byefiles.py` | cache, groupes, règles de tri, serveur web local, Corbeille et annulation |
| `ui.html` | l'interface |
| `~/.byefiles/` | cache local, aperçus et historique de la Corbeille |

Pour personnaliser : délais et catégories dans `decide()`, motifs sensibles dans `SENSITIVE` (`byefiles.py`), types de contenu de l'IA dans `Analyse` et `instructionsClassify` (`analyze.swift`, puis recompiler).

## Limites

- macOS uniquement, sur les Mac compatibles Apple Intelligence.
- Le modèle d'Apple est petit : il se trompe parfois de sujet ou refuse un fichier. Les fichiers refusés arrivent dans « À vérifier ».
- Les règles et les libellés sont pensés pour des contenus en français.
- Rien n'est supprimé définitivement, mais relis « Supprimables » avant de vider la Corbeille.

## Licence

MIT
