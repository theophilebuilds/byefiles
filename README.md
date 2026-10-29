# screentri

**Trie un dossier en bazar (Téléchargements, Bureau, captures d'écran…) avec l'IA d'Apple, entièrement sur ton Mac.**

screentri lit chaque fichier d'un dossier (captures, PDF, Word, PowerPoint, Excel, Pages, textes, vidéos, sons, installeurs…), comprend de quoi il parle, puis le range dans l'une de quatre catégories :

| Catégorie | Ce qu'on y trouve |
|---|---|
| 🗑️ **Supprimables** | Démarches terminées, inspiration ponctuelle, rafales de captures, réglages, achats, vieux installeurs |
| 🤔 **À vérifier** | Projets de quelques semaines ou mois, photos perso, fichiers illisibles |
| ✅ **À garder** | Projets récents, billets et rendez-vous à venir, factures, contrats, CV… |
| ⚠️ **Sensible** | Pièces d'identité, IBAN, clés d'API, mots de passe, codes de récupération |

Tu valides ensuite dans une interface web locale : aperçu de chaque fichier, sélection en un clic, envoi à la Corbeille et annulation.

**Rien ne quitte le Mac.** Pas de compte, pas de clé d'API, pas de serveur distant : la lecture du texte, l'OCR et l'IA tournent sur la machine.

---

## Prérequis

- Un Mac Apple Silicon (M1 ou plus récent)
- macOS 26 ou plus récent, avec **Apple Intelligence activé** (Réglages Système → Apple Intelligence et Siri)
- Python 3 (aucune dépendance à installer, seulement la bibliothèque standard)
- Les outils en ligne de commande Xcode, pour compiler la partie Swift : `xcode-select --install`

## Installation

```sh
git clone <url-du-dépôt> ~/Projects/screentri
cd ~/Projects/screentri
mkdir -p bin
swiftc -O analyze.swift -o bin/screentri-analyze
```

Pour lancer `screentri` depuis n'importe quel dossier, ajoute un petit script dans ton `PATH` (ici `~/.local/bin`) :

```sh
mkdir -p ~/.local/bin
cat > ~/.local/bin/screentri <<'EOF'
#!/bin/bash
exec python3 -I "$HOME/Projects/screentri/screentri.py" "$@"
EOF
chmod +x ~/.local/bin/screentri
```

## Utilisation

```sh
screentri                 # trie le dossier courant
screentri ~/Downloads     # trie un autre dossier
screentri --reanalyze     # refait l'analyse de ce dossier sans utiliser le cache
screentri --port 9000     # change le port de l'interface (8765 par défaut)
screentri --no-open       # n'ouvre pas le navigateur automatiquement
```

Seuls les fichiers à la racine du dossier sont triés, les sous-dossiers ne sont pas touchés.

L'interface s'ouvre tout de suite et se remplit pendant l'analyse : une barre montre où elle en est, et chaque fichier apparaît dès qu'il est classé. Les captures vides et les fichiers sensibles s'affichent immédiatement, sans attendre l'IA. La première analyse prend un peu de temps (OCR et IA sur chaque fichier) ; les suivantes sont presque instantanées, car seuls les fichiers nouveaux ou modifiés sont relus.

Dans l'interface (`http://127.0.0.1:8765`) :
- les fichiers sont regroupés par catégorie, puis par sujet (« Parcoursup », « Projet Plumb », « Facture EDF »…) ;
- dans la vue agrandie, chaque fichier affiche **la raison** de son classement (ex. « démarche ancienne (142 j), sans doute terminée ») ;
- un clic ouvre la vue agrandie : PDF lisible, vidéo et son jouables, texte affiché, première page pour Word, PowerPoint ou Pages ;
- « Corbeille » envoie la sélection dans la Corbeille de macOS, et « Annuler » remet les fichiers à leur place ;
- les doublons exacts sont signalés à part.

---

## Comment ça marche

```
 Dossier
   │
   ▼
 1. Extraction ──────────── Swift : Quick Look, PDFKit, Vision (OCR)
   │   texte + aperçu + empreinte visuelle de chaque fichier
   ▼   (fichier par fichier : l'IA démarre sans attendre la fin)
 2. Groupes ─────────────── Python : rafales, séries « (1) », images quasi identiques
   │   + installeurs, photos et données sensibles classés sans IA
   ▼
 3. Analyse IA ──────────── Swift : Foundation Models (modèle d'Apple, local)
   │   un fichier par groupe, 2 à la fois : sujet, type, échéance à venir
   ▼
 4. Règles de tri ───────── Python : fonction decide()
   │   catégorie + raison
   ▼
 5. Regroupement + doublons
   │
   ▼
 Interface web locale ──── Python http.server + ui.html
```

### 1. Extraction du texte et des aperçus

Le moteur Swift (`analyze.swift`) traite les fichiers en parallèle (4 à la fois) :

- **Images** : OCR avec le framework **Vision** d'Apple (français et anglais).
- **PDF** : texte des 5 premières pages avec **PDFKit**. Si le PDF est scanné (pas de texte), la première page passe en OCR.
- **Word, RTF, OpenDocument** : lecture via `NSAttributedString`. Pour `.docx`, `.pptx`, `.xlsx` et OpenDocument, Python lit aussi directement le XML contenu dans le fichier (ce sont des archives zip).
- **Textes, code, HTML** : lecture directe (le HTML est débarrassé de ses balises).
- **Le reste** (vidéos, Keynote, Pages…) : **Quick Look** génère un aperçu, et le texte visible dessus passe en OCR.

Chaque fichier reçoit aussi une miniature Quick Look pour l'interface (JPEG de 300 px, ou 900 px quand l'aperçu sert à l'OCR). Pour les images, le moteur calcule en plus une **empreinte visuelle** (dHash) : la miniature réduite à 9×8 pixels en gris, où chaque bit dit si un pixel est plus clair que son voisin.

Chaque fichier lu part aussitôt vers l'étape suivante : l'IA commence à analyser les premiers fichiers pendant que les autres sont encore en lecture.

### 2. Groupes et fichiers classés sans IA

L'IA est l'étape la plus lente (environ 1 s par fichier). screentri l'évite dès qu'elle n'apporte rien.

**Fichiers qui montrent la même chose** : ils forment un groupe, et un seul fichier par groupe passe dans l'IA. Le résultat s'applique à tous. Deux fichiers sont reliés si c'est :
- une **rafale** : même genre, créés à moins de 5 minutes d'écart, texte ressemblant à au moins 35 % (similarité de Jaccard sur les mots) ;
- une **série** : même nom aux suffixes de copie près (« Garanties.pdf », « Garanties (1).pdf », « Garanties - Copie.pdf »), même extension, taille à 20 % près ;
- une **image quasi identique** : empreintes visuelles à 3 bits ou moins l'une de l'autre, même à plusieurs jours d'écart (la même capture redimensionnée ou réenregistrée). Deux pages différentes d'un même document sont à 6 bits ou plus.

**Fichiers que les règles suffisent à classer** :
- installeurs et archives, reconnus à leur extension (`.dmg`, `.pkg`, `.zip`, `.iso`…) ;
- images sans aucun texte qui ne sont pas des captures : des photos perso ;
- captures vides et fichiers contenant une donnée sensible (voir les règles plus bas).

### 3. Analyse par le modèle d'Apple

Le texte (1 400 caractères max), le nom, le genre, la taille et la date du fichier sont envoyés au modèle de langage intégré à macOS via le framework **Foundation Models**. Ce modèle tourne sur la puce du Mac, sans connexion.

La réponse est **structurée** grâce à `@Generable` : le modèle ne peut pas répondre en texte libre, il doit remplir une structure Swift précise.

```swift
@Generable
struct Analyse {
    var sujet: String    // « Parcoursup », « Facture EDF »…
    var type: String     // une valeur parmi 14 : projet_travail, administratif, inspiration…
    var aVenir: Bool     // billet, rendez-vous ou échéance après aujourd'hui ?
}
```

La température est à 0 pour que le même fichier donne toujours le même résultat.

### 4. Les règles de tri

**L'IA ne décide pas seule de la catégorie.** Elle décrit le fichier (sujet, type), puis des règles fixes et lisibles, dans la fonction `decide` de `screentri.py`, choisissent la catégorie. Le classement est donc prévisible, explicable et facile à modifier.

Dans l'ordre :

1. **Capture d'écran sans aucun texte** → supprimable.
2. **Donnée sensible** repérée par expressions régulières (IBAN, passeport, carte d'identité, tokens GitHub, clés d'API OpenAI, Anthropic, Google ou AWS, clés privées, jetons JWT, codes de récupération…) → sensible. Ces motifs passent *avant* l'IA, car des règles fixes sont plus fiables qu'un modèle pour ça.
3. **Identité ou banque** selon l'IA → sensible.
4. **Échéance à venir** : l'IA le signale *et* une date future est réellement trouvée dans le texte → à garder.
5. **Document à conserver** (facture, contrat, CV…) → à garder.
6. **Photo perso** → à vérifier, *jamais* supprimable d'office.
7. **Installeur ou archive** : à vérifier pendant 7 jours, supprimable ensuite.
8. **Projet ou cours** : à garder pendant 30 jours, à vérifier jusqu'à 120 jours, supprimable ensuite.
9. **Démarche administrative** : à garder pendant 30 jours, à vérifier jusqu'à 90 jours, supprimable ensuite.
10. **Tout le reste** (inspiration, réseaux sociaux, achats…) : à vérifier pendant 7 jours, supprimable ensuite.

L'âge d'un fichier vient de la date dans son nom (captures macOS, CleanShot…) ou, à défaut, de sa date de création.

### 5. Regroupement et doublons

- **Sujets proches** : « Projet Plumb » et « Plumb Xcode » partagent le mot « Plumb » et finissent dans le même groupe (union-find sur les mots significatifs, en ignorant les mots trop vagues comme « projet » ou « capture »).
- **Doublons exacts** : fichiers de même taille, puis de même empreinte MD5.

### L'interface

Un petit serveur HTTP en Python pur (`http.server`) sert `ui.html`, une page HTML et JavaScript sans framework ni dépendance. Le serveur :

- n'écoute que sur `127.0.0.1` : il est inaccessible depuis le réseau ;
- protège les actions (Corbeille, ouverture, annulation) par un jeton aléatoire généré à chaque lancement ;
- refuse tout chemin hors du dossier trié (pas de `/`, pas de fichiers cachés, pas de liens symboliques) ;
- sert les fichiers HTML, SVG et XML comme du texte brut, avec une politique `sandbox`, pour qu'un fichier du dossier ne puisse jamais exécuter de script ;
- gère les requêtes de plage d'octets, pour lire les vidéos dans Safari.

La suppression passe par la commande `trash` de macOS : les fichiers vont dans la Corbeille et restent récupérables. screentri note où chacun a atterri, ce qui permet le bouton « Annuler » (50 dernières opérations par dossier). Si un fichier du même nom est réapparu entre-temps, il n'est pas écrasé : le fichier restauré prend le suffixe « (restauré) ».

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `analyze.swift` | Moteur natif : extraction du texte, OCR, aperçus Quick Look, appel au modèle d'Apple. Compilé en `bin/screentri-analyze`. |
| `screentri.py` | Orchestration : cache, groupes de fichiers similaires, règles de tri, regroupement par sujet, doublons, serveur web, Corbeille et annulation. |
| `ui.html` | Interface (HTML, CSS et JavaScript, mode sombre inclus). |
| `~/.screentri/` | Données locales : cache d'analyse, miniatures, aperçus, historique des mises à la Corbeille (`trash-log.txt`, `undo.json`). |

Les deux programmes communiquent en JSON par l'entrée et la sortie standard du moteur Swift, qui répond un objet JSON par ligne au fur et à mesure. La progression s'affiche donc en direct.

```sh
bin/screentri-analyze check      # le modèle d'Apple est-il disponible ?
bin/screentri-analyze extract    # {"files":[…],"thumbs":"…"}  →  texte, aperçu, empreinte par fichier
bin/screentri-analyze classify   # 1re ligne {"today":"…","parallel":2}, puis un fichier par ligne,
                                 # envoyés au fil de l'eau  →  sujet, type, aVenir
```

## Personnaliser

- **Changer les délais ou les catégories** : modifie la fonction `decide` dans `screentri.py`.
- **Ajouter un motif sensible** : ajoute une ligne à la liste `SENSITIVE` (expression régulière + libellé).
- **Changer les types de contenu reconnus par l'IA** : modifie la structure `Analyse` et `instructionsClassify` dans `analyze.swift`, puis recompile.

## Limites

- macOS uniquement, et seulement sur les Mac compatibles Apple Intelligence.
- Le modèle d'Apple est petit : il se trompe parfois de sujet, et refuse certains contenus à cause de ses garde-fous. Ces fichiers arrivent dans « À vérifier → Non analysés ».
- Les règles sont pensées pour un usage en français (dates, mots-clés, libellés).
- screentri ne supprime jamais rien définitivement, mais relis quand même la catégorie « Supprimables » avant de vider la Corbeille.
