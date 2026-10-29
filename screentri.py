"""screentri : trie les fichiers d'un dossier (captures, PDF, documents, vidéos…), 100 % en local.

Usage : screentri [dossier] [--port N] [--no-open] [--reanalyze]
  - dossier : par défaut le dossier courant (seuls les fichiers à la racine du dossier sont triés)
  - lecture du contenu et analyse par le modèle d'Apple, sur le Mac (rien ne part sur internet)
  - interface web sur http://127.0.0.1 (accessible seulement depuis ce Mac)
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "bin", "screentri-analyze")
STATE = os.path.expanduser("~/.screentri")
CACHE_FILE = os.path.join(STATE, "cache.json")
THUMBS = os.path.join(STATE, "thumbs")
PREVIEWS = os.path.join(STATE, "previews")
SKIP_NAMES = {".DS_Store", "Icon\r", ".localized"}
# Analyses IA lancées en même temps. Au-delà de 2, le modèle d'Apple ne va pas plus vite.
PARALLEL = 2

CATEGORIES = [
    ("supprimer", "🗑️ Probablement inutiles : supprimables",
     "Fichiers datés : démarches terminées, inspiration ponctuelle, rafales, réglages, achats, vieux installeurs."),
    ("verifier", "🤔 À vérifier : peut-être encore utiles",
     "Projets ou démarches de quelques semaines ou mois, photos perso, fichiers illisibles : à toi de voir."),
    ("garder", "✅ À garder : récent, à venir ou à conserver",
     "Projets récents, billets et rendez-vous à venir, documents à conserver (factures, contrats, CV…)."),
    ("sensible", "⚠️ Sensible : à traiter en priorité",
     "Pièces d'identité, banque, clés, mots de passe ou données personnelles. Range-les dans un endroit sûr ou supprime-les."),
]

TYPE_LABEL = {
    "projet_travail": "projet / travail", "cours_etudes": "cours / études", "administratif": "démarche administrative",
    "document_a_conserver": "document à conserver", "identite_banque": "identité / banque",
    "inspiration": "inspiration", "reseaux_sociaux": "réseaux sociaux", "achats": "achats",
    "divertissement": "divertissement", "reglages_systeme": "réglages système",
    "conversation_ia": "conversation avec une IA", "photo_perso": "photo perso",
    "installeur_archive": "installeur / archive", "autre": "autre",
}

KIND_LABEL = {"image": "image", "pdf": "PDF", "rich": "document", "text": "texte", "html": "page web",
              "video": "vidéo", "audio": "audio", "other": "fichier"}

# Données sensibles repérées par des règles fixes (plus fiables que le modèle pour ça).
SENSITIVE = [
    (r"(?i)passeport|page réservée aux autorités", "passeport"),
    (r"(?i)carte (nationale )?d'?identit|carte d.ID\b|date de délivrance|date of issue", "pièce d'identité"),
    (r"\bFR\d{2}[ ]?\d{4}[ ]?\d{4}", "IBAN"),
    (r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}", "token GitHub"),
    (r"\bsk-(ant-)?[A-Za-z0-9_\-]{16,}|\bAIza[0-9A-Za-z_\-]{20,}|\bAKIA[0-9A-Z]{16}|xox[bp]-[A-Za-z0-9-]{10,}", "clé d'API"),
    (r"(?i)(api[ _-]?key|clé (d'|de l')?api)", "clé d'API"),
    (r"BEGIN [A-Z ]*PRIVATE KEY", "clé privée"),
    (r"(?i)recovery (key|code|phrase)|clé de (récupération|secours)|codes? de secours|backup codes?|seed phrase|phrase secrète", "code de récupération"),
    (r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{6}-[A-Za-z0-9]{6}-[A-Za-z0-9]{2,6}\b", "mot de passe généré"),
    (r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}", "jeton de connexion"),
    (r"(?i)personal id|numéro de sécurité sociale|n° de sécu", "identifiant personnel"),
]


def log(msg):
    print(msg, flush=True)


def norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"[^a-z0-9 ]", "", "".join(c for c in s if not unicodedata.combining(c))).strip()


def words(text):
    return {w for w in re.findall(r"\w{3,}", text.lower())}


def human_size(n):
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def is_screenshot(name):
    return bool(re.match(r"(?i)(screenshot|capture d.écran|screen shot|cleanshot)", name))


def capture_date(path):
    name = os.path.basename(path)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\D{1,4}(\d{2})[.:h](\d{2})[.:](\d{2})", name)
    if m:
        try:
            return dt.datetime(*map(int, m.groups()))
        except ValueError:
            pass
    st = os.stat(path)
    return dt.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))


def engine(mode, payload, on_line=None):
    p = subprocess.Popen([ENGINE, mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    out = []
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        out.append(obj)
        if on_line:
            on_line(obj)
    p.wait()
    return out


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    os.makedirs(STATE, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def office_text(path):
    """Texte des fichiers Office / OpenDocument (ce sont des zip de XML)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if ext == ".pptx":
                parts = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                               key=lambda n: int(re.findall(r"\d+", n)[-1]))
            elif ext == ".xlsx":
                parts = [n for n in names if n == "xl/sharedStrings.xml"]
            elif ext == ".docx":
                parts = [n for n in names if n == "word/document.xml"]
            elif ext in (".odt", ".odp", ".ods"):
                parts = [n for n in names if n == "content.xml"]
            else:
                return ""
            out = []
            for n in parts[:30]:
                xml = z.read(n)[:400_000].decode("utf-8", "ignore")
                out.append(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", xml))))
            return "\n".join(out)[:20_000]
    except (zipfile.BadZipFile, OSError, KeyError):
        return ""


def sensitive_reason(text):
    for pat, why in SENSITIVE:
        if re.search(pat, text):
            return why
    return None


MONTHS = {"janv": 1, "jan": 1, "fevr": 2, "fev": 2, "feb": 2, "mars": 3, "mar": 3, "avr": 4, "apr": 4, "mai": 5,
          "may": 5, "juin": 6, "jun": 6, "juil": 7, "jul": 7, "aout": 8, "aug": 8, "sept": 9, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12}


def future_date_in_text(text, shot_date, today):
    """Cherche dans le texte une date postérieure à aujourd'hui (ex. « 02 nov. », « 02/11/2026 »)."""
    t = norm(text.replace("/", " / ").replace("-", " - "))
    found = []
    for d, m in re.findall(r"\b(\d{1,2}) (janv|jan|fevr|fev|feb|mars|mar|avr|apr|mai|may|juin|jun|juil|jul|aout|aug|sept|sep|oct|nov|dec)[a-z]*\b", t):
        found.append((int(d), MONTHS[m], None))
    for d, m, y in re.findall(r"\b(\d{1,2}) / (\d{1,2}) / (\d{2,4})\b", t):
        found.append((int(d), int(m), int(y) if len(y) == 4 else 2000 + int(y)))
    for y, m, d in re.findall(r"\b(20\d{2}) - (\d{1,2}) - (\d{1,2})\b", t):
        found.append((int(d), int(m), int(y)))
    for d, m, y in found:
        try:
            when = dt.datetime(y or shot_date.year, m, d, 23, 59)
            if y is None and when < shot_date - dt.timedelta(days=1):
                when = when.replace(year=when.year + 1)
        except ValueError:
            continue
        if when >= today:
            return True
    return False


# Mots trop vagues pour relier deux sujets entre eux.
STOP = set("""projet projets capture captures ecran page pages profil profils notes note site sites application app
compte comptes reglages reglage configuration commande commandes document documents information informations
dossier dossiers mon mes ton tes son ses les des une pour avec dans sur par aux the and with from your
fichier fichiers outil outils video videos post posts message messages email emails mail mails discussion
conversation recherche resultats resultat liste tableau bord parametres code erreur erreurs interface
nouveau nouvelle nouvelles nouveaux suivi demande demandes service services photo photos image images
installeur installeurs archive archives facture factures cours exercice exercices""".split())


def merge_topics(items):
    """Relie les sujets qui partagent un mot significatif (ex. « Projet Plumb » et « Plumb Xcode »)."""
    names = {}
    for it in items:
        s = (it["analysis"].get("sujet") or "").strip() or "Divers"
        names.setdefault(norm(s) or "divers", []).append(s)
    keys = list(names)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    owner = {}
    for k in keys:
        for w in set(k.split()):
            if len(w) < 4 or w in STOP or w.isdigit():
                continue
            if w in owner:
                parent[find(k)] = find(owner[w])
            else:
                owner[w] = k
    members = {}
    for k in keys:
        members.setdefault(find(k), []).extend(names[k])
    label = {root: max(set(ns), key=lambda n: (ns.count(n), -len(n))) for root, ns in members.items()}

    def theme_of(it):
        s = (it["analysis"].get("sujet") or "").strip() or "Divers"
        return label[find(norm(s) or "divers")]
    return theme_of


def decide(item, today):
    """Choisit la catégorie et explique pourquoi. Renvoie (catégorie, nom de groupe imposé ou None, raison)."""
    a = item["analysis"] or {}
    age = (today - item["date"]).days
    t = a.get("type") or "autre"
    has_text = bool(item["text"].strip())
    if not has_text and item["kind"] == "image" and is_screenshot(item["file"]):
        return "supprimer", "Captures vides", "aucun texte détecté"
    why = sensitive_reason(item["text"]) if has_text else None
    if why:
        return "sensible", why[0].upper() + why[1:], f"contient : {why}"
    if t == "identite_banque":
        return "sensible", None, "identité, banque ou données personnelles"
    if a.get("aVenir") and future_date_in_text(item["text"], item["date"], today):
        return "garder", None, "billet, rendez-vous ou échéance à venir"
    if t == "document_a_conserver":
        return "garder", None, "document à conserver (facture, contrat, attestation…)"
    if t == "photo_perso" or (item["kind"] == "image" and not has_text):
        return "verifier", "Photos perso", "photo : jamais supprimée d'office"
    if not a.get("sujet"):
        return "verifier", "Non analysés", "contenu non lu par le modèle"
    if t == "installeur_archive":
        if age <= 7:
            return "verifier", None, f"installeur ou archive récent ({age} j)"
        return "supprimer", None, f"installeur ou archive de {age} jours (déjà installé ou extrait ?)"
    if t in ("projet_travail", "cours_etudes"):
        if age <= 30:
            return "garder", None, f"{TYPE_LABEL[t]} récent ({age} j)"
        if age <= 120:
            return "verifier", None, f"{TYPE_LABEL[t]} d'il y a {age} jours"
        return "supprimer", None, f"{TYPE_LABEL[t]} ancien ({age} j)"
    if t == "administratif":
        if age <= 30:
            return "garder", None, f"démarche récente ({age} j)"
        if age <= 90:
            return "verifier", None, f"démarche d'il y a {age} jours"
        return "supprimer", None, f"démarche ancienne ({age} j), sans doute terminée"
    if age <= 7:
        return "verifier", None, f"{TYPE_LABEL.get(t, t)}, fichier récent"
    return "supprimer", None, f"{TYPE_LABEL.get(t, t)} ({age} j)"


def list_files(folder):
    out = []
    for f in os.listdir(folder):
        p = os.path.join(folder, f)
        if f.startswith(".") or f in SKIP_NAMES or not os.path.isfile(p) or os.path.islink(p):
            continue
        out.append(p)
    return sorted(out)


class Live:
    """État partagé entre l'analyse (en arrière-plan) et le serveur : ce qui est prêt à afficher."""

    def __init__(self, folder):
        import threading
        self.lock = threading.Lock()
        self.version = 0
        self.thumbs = {}
        self.status = {"phase": "Démarrage…", "done": 0, "total": 0, "finished": False, "error": None}
        self.data = {"folder": folder, "dups": [], "info": {}, "text": {}, "pending": 0,
                     "groups": [{"id": cid, "title": t, "desc": d, "items": []} for cid, t, d in CATEGORIES]}

    def publish(self, data=None, thumbs=None, **status):
        with self.lock:
            if data is not None:
                self.data = data
            if thumbs is not None:
                self.thumbs = thumbs
            self.status = {**self.status, **status}
            self.version += 1

    def snapshot(self):
        with self.lock:
            return self.data, self.thumbs, dict(self.status), self.version


def can_decide_without_ai(it):
    """Les captures vides et les données sensibles se classent par règles fixes, sans attendre l'IA."""
    if not it["text"].strip():
        return it["kind"] == "image" and is_screenshot(it["file"])
    return sensitive_reason(it["text"]) is not None


INSTALLER_EXT = {".dmg", ".pkg", ".mpkg", ".iso", ".xip", ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz",
                 ".bz2", ".xz", ".exe", ".msi", ".apk"}


def rule_analysis(it):
    """Analyse donnée par des règles fixes quand l'IA n'apporterait rien (installeurs, photos sans texte)."""
    if os.path.splitext(it["file"])[1].lower() in INSTALLER_EXT:
        return {"sujet": "Installeurs et archives", "type": "installeur_archive", "aVenir": False}
    if it["kind"] == "image" and not it["text"].strip() and not is_screenshot(it["file"]):
        return {"sujet": "Photos perso", "type": "photo_perso", "aVenir": False}
    return None


def series_name(it):
    """Nom sans les suffixes de copie : « Garanties (1).pdf » et « Garanties - Copie.pdf » -> « garanties.pdf »."""
    base, ext = os.path.splitext(it["file"])
    base = re.sub(r"(?i)(\s*\(\d+\)|\s*-?\s*(copie|copy)(\s*\d+)?)+$", "", base).strip()
    return norm(base) + ext.lower()


class Grouper:
    """Regroupe, au fur et à mesure qu'ils arrivent, les fichiers qui montrent la même chose.
    L'IA n'analyse qu'un fichier par groupe. Liens :
    - rafale : même genre, créés à moins de 5 min d'écart, texte proche ;
    - série : même nom aux suffixes de copie près (« x (1).pdf »), même extension, taille à 20 % près ;
    - image quasi identique : empreintes visuelles à 3 bits ou moins (même capture redimensionnée,
      recadrée de peu ou réenregistrée). Deux pages différentes d'un même document sont à 6 bits ou plus."""

    def __init__(self):
        self.items, self.parent, self.queued = [], [], []
        self.words, self.by_name, self.hashed = [], {}, []

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def link(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[ri] = rj
            self.queued[rj] = self.queued[rj] or self.queued[ri]

    def add(self, it):
        i = len(self.items)
        self.items.append(it)
        self.parent.append(i)
        self.queued.append(False)
        w = words(it["text"])
        self.words.append(w)
        for j, other in enumerate(self.items[:-1]):
            if other["kind"] != it["kind"] or abs((it["date"] - other["date"]).total_seconds()) > 300:
                continue
            u = w | self.words[j]
            if u and len(w & self.words[j]) / len(u) >= 0.35:
                self.link(i, j)
        name = series_name(it)
        for j in self.by_name.get(name, []):
            small, big = sorted((it["size"], self.items[j]["size"]))
            if big and small / big >= 0.8:
                self.link(i, j)
                break
        self.by_name.setdefault(name, []).append(i)
        if it["kind"] == "image" and it.get("dhash"):
            h = int(it["dhash"], 16)
            for j, hj in self.hashed:
                if bin(h ^ hj).count("1") <= 3:
                    self.link(i, j)
                    break
            self.hashed.append((i, h))
        return i

    def members(self, i):
        root = self.find(i)
        return [k for k in range(len(self.items)) if self.find(k) == root]


class StreamEngine:
    """Moteur d'analyse IA qui reçoit les fichiers un par un et répond au fil de l'eau."""

    def __init__(self, config, on_result):
        import threading
        self.p = subprocess.Popen([ENGINE, "classify"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1)
        self.send(config)
        self.reader = threading.Thread(target=self._read, args=(on_result,), daemon=True)
        self.reader.start()

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def _read(self, on_result):
        for line in self.p.stdout:
            if line.strip():
                on_result(json.loads(line))

    def finish(self):
        self.p.stdin.close()
        self.reader.join()
        self.p.wait()


def find_dups(items):
    """Doublons exacts : même taille d'abord, puis même contenu."""
    by_size = {}
    for it in items:
        by_size.setdefault(it["size"], []).append(it)
    dups = []
    for same in by_size.values():
        if len(same) < 2:
            continue
        by_hash = {}
        for it in same:
            h = hashlib.md5()
            try:
                with open(it["path"], "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
            except OSError:
                continue
            by_hash.setdefault(h.hexdigest(), []).append(it["file"])
        dups += [v for v in by_hash.values() if len(v) > 1]
    return dups


def build(folder, items, cache, today, dups, total=None):
    """Catégories et groupes pour les fichiers déjà prêts (analysés, ou classables sans l'IA)."""
    ready = [it for it in items if it["analysis"] is not None or can_decide_without_ai(it)]
    theme_of = merge_topics([it for it in ready if it["analysis"] is not None])
    groups = {c[0]: {} for c in CATEGORIES}
    info = {}
    for it in ready:
        cat, forced_label, why = decide(it, today)
        label = forced_label or theme_of(it)
        groups[cat].setdefault(label, []).append(it)
        ext = os.path.splitext(it["file"])[1].lstrip(".").upper()
        thumb = cache[it["path"]].get("thumb")
        info[it["file"]] = {"raison": why, "date": it["date"].strftime("%d/%m/%Y %H:%M"), "kind": it["kind"],
                            "ext": ext, "size": human_size(it["size"]), "thumb": bool(thumb and os.path.exists(thumb))}

    out_groups = []
    for cid, title, desc in CATEGORIES:
        g = groups[cid]
        labels = sorted(g, key=lambda k: (-len(g[k]), k))
        out_groups.append({"id": cid, "title": title, "desc": desc, "items": [
            {"label": lab, "files": [x["file"] for x in sorted(g[lab], key=lambda x: x["date"], reverse=True)]}
            for lab in labels]})
    return {"folder": folder, "groups": out_groups, "dups": dups, "info": info,
            "pending": (total or len(items)) - len(ready), "text": {it["file"]: it["text"][:1500] for it in ready}}


def analyze(folder, live, reanalyze=False):
    """Analyse le dossier et publie dans `live` ce qui est prêt, au fur et à mesure.
    L'IA commence dès que les premiers fichiers sont lus, sans attendre la fin de la lecture."""
    import threading
    import time
    files = list_files(folder)
    cache = load_cache()
    if reanalyze:
        # On n'oublie que les fichiers de ce dossier : le cache des autres dossiers reste intact.
        cache = {p: c for p, c in cache.items() if os.path.dirname(p) != folder}

    def key(p):
        try:
            st = os.stat(p)
            return f"v2:{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            return None

    todo = [p for p in files if cache.get(p, {}).get("key") != key(p)]
    today = dt.datetime.now()
    mu = threading.Lock()  # protège tout l'état ci-dessous (lecture et réponses de l'IA arrivent en parallèle)
    G = Grouper()
    thumbs, dups = {}, []
    count = {"read": 0, "sent": 0, "ai": 0, "reading": bool(todo), "last": 0.0}

    def publish(force=False):
        now = time.time()
        if not force and now - count["last"] < 0.4:
            return
        count["last"] = now
        if count["reading"]:
            phase = f"Lecture des fichiers : {count['read']} / {len(todo)} · analyse par l'IA : {count['ai']} / {count['sent']}"
        else:
            phase = f"Analyse par l'IA d'Apple : {count['ai']} / {count['sent']}"
        live.publish(data=build(folder, G.items, cache, today, dups, total=len(files)), thumbs=dict(thumbs),
                     phase=phase, done=count["read"] + count["ai"], total=len(todo) + count["sent"])

    def settle(i):
        """Partage l'analyse connue dans le groupe de i, ou envoie un fichier du groupe à l'IA."""
        group = [G.items[k] for k in G.members(i)]
        known = next((m for m in group if m["analysis"] is not None and m["from_ai"]), None)
        known = known or next((m for m in group if m["analysis"] is not None), None)
        if known:
            for m in group:
                if m["analysis"] is None:
                    m["analysis"], m["from_ai"] = known["analysis"], known["from_ai"]
                    if known["from_ai"]:
                        cache[m["path"]]["analysis"] = known["analysis"]
            return
        root = G.find(i)
        needs = [k for k in G.members(i) if not can_decide_without_ai(G.items[k])]
        if G.queued[root] or not needs:
            return
        G.queued[root] = True
        k = max(needs, key=lambda k: len(G.items[k]["text"]))
        rep = G.items[k]
        meta = (f"Nom du fichier : {rep['file']} — genre : {KIND_LABEL.get(rep['kind'], 'fichier')}"
                f" — taille : {human_size(rep['size'])}")
        count["sent"] += 1
        ai.send({"id": str(k), "text": rep["text"], "date": rep["date"].strftime("%d/%m/%Y"), "meta": meta})

    def add(p):
        c = cache[p]
        try:
            it = {"path": p, "file": os.path.basename(p), "text": c.get("text", ""), "kind": c.get("kind", "other"),
                  "size": os.path.getsize(p), "date": capture_date(p), "dhash": c.get("dhash"),
                  "analysis": c.get("analysis"), "from_ai": c.get("analysis") is not None}
        except OSError:
            return  # mis à la Corbeille ou déplacé entre-temps
        if it["analysis"] is None:
            # Installeurs, archives et photos sans texte : les règles fixes suffisent
            it["analysis"] = rule_analysis(it)
        thumbs[it["file"]] = c.get("thumb")
        settle(G.add(it))

    def on_result(o):
        with mu:
            count["ai"] += 1
            res = {k: o.get(k) for k in ("sujet", "type", "aVenir")}
            k = int(o["id"])
            G.items[k]["analysis"], G.items[k]["from_ai"] = res, True
            cache[G.items[k]["path"]]["analysis"] = res
            settle(k)
            if count["ai"] % 10 == 0:
                save_cache(cache)
            publish()

    ai = StreamEngine({"today": today.strftime("%d/%m/%Y"), "parallel": PARALLEL}, on_result)

    # 1. Fichiers déjà lus lors d'un lancement précédent : tout de suite
    with mu:
        for p in files:
            if p not in todo:
                add(p)
        publish(force=True)

    # 2. Lecture des fichiers nouveaux ou modifiés : chacun part à l'IA dès qu'il est lu
    if todo:
        log(f"Lecture du contenu et création des aperçus : {len(todo)} fichier(s)…")

        def got(o):
            p = o["path"]
            text = o.get("text", "")
            if os.path.splitext(p)[1].lower() in (".pptx", ".xlsx", ".docx", ".odt", ".odp", ".ods"):
                text = office_text(p) or text
            if o.get("kind") == "html":
                text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<(script|style)[\s\S]*?</\1>|<[^>]+>", " ", text)))
            with mu:
                count["read"] += 1
                cache[p] = {"key": key(p), "kind": o.get("kind", "other"), "text": text, "thumb": o.get("thumb"),
                            "dhash": o.get("dhash")}
                add(p)
                if count["read"] % 20 == 0 or count["read"] == len(todo):
                    log(f"  {count['read']}/{len(todo)}")
                    save_cache(cache)
                publish()
        engine("extract", {"files": todo, "thumbs": THUMBS}, got)

    # 3. Doublons exacts, puis on attend les dernières réponses de l'IA
    found = find_dups(G.items)
    with mu:
        dups[:] = found
        count["reading"] = False
        log(f"Analyse par le modèle d'Apple : {count['sent']} fichier(s) ou groupe(s)…")
        publish(force=True)
    ai.finish()

    with mu:
        save_cache(cache)
        for it in G.items:
            it["analysis"] = it["analysis"] or {"sujet": "", "type": "autre"}
        data = build(folder, G.items, cache, today, dups, total=len(files))
        live.publish(data=data, thumbs=dict(thumbs), phase="Terminé", finished=True)
    return data


def make_handler(folder, live, token, page):
    def safe_path(name):
        if not name or "/" in name or "\\" in name or name.startswith(".") or name in SKIP_NAMES:
            return None
        p = os.path.join(folder, name)
        return p if os.path.isfile(p) and not os.path.islink(p) else None

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, p, ctype):
            """Envoie un fichier, avec prise en charge des plages d'octets (lecture vidéo dans Safari)."""
            size = os.path.getsize(p)
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            # Un fichier ouvert dans l'interface ne doit jamais pouvoir exécuter de script sur ce serveur.
            if ctype != "application/pdf":
                self.send_header("Content-Security-Policy", "sandbox")
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                return self.send(200, page.replace("__TOKEN__", token), "text/html; charset=utf-8")
            if u.path == "/data":
                data, _, status, version = live.snapshot()
                present = sorted(f for f in os.listdir(folder) if not f.startswith("."))
                return self.send(200, json.dumps({**data, "present": present, "undo": undo_count(),
                                                  "status": status, "version": version}, ensure_ascii=False))
            for prefix in ("/thumb/", "/file/", "/preview/", "/text/"):
                if u.path.startswith(prefix):
                    name = urllib.parse.unquote(u.path[len(prefix):])
                    p = safe_path(name)
                    if not p:
                        return self.send(404, "{}")
                    return getattr(self, "get_" + prefix.strip("/"))(p, name)
            self.send(404, "{}")

        def get_thumb(self, p, name):
            t = live.snapshot()[1].get(name)
            if not t or not os.path.exists(t):
                return self.send(404, "{}")
            self.send_file(t, "image/jpeg")

        def get_file(self, p, name):
            ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
            # Les pages web, SVG et XML sont montrés comme du texte, jamais exécutés.
            if ctype.startswith("text/") or "xml" in ctype or "html" in ctype or "javascript" in ctype:
                ctype = "text/plain; charset=utf-8"
            self.send_file(p, ctype)

        def get_preview(self, p, name):
            """Grand aperçu Quick Look (première page / diapo) pour les formats que le navigateur ne lit pas."""
            if not live.snapshot()[1].get(name):
                # Quick Look n'a pas su lire ce fichier pendant l'analyse : inutile de réessayer.
                return self.send(404, "{}")
            st = os.stat(p)
            out_dir = os.path.join(PREVIEWS, hashlib.sha1(f"{p}:{st.st_size}:{st.st_mtime}".encode()).hexdigest()[:24])
            png = os.path.join(out_dir, os.path.basename(p) + ".png")
            if not os.path.exists(png):
                os.makedirs(out_dir, exist_ok=True)
                try:
                    subprocess.run(["qlmanage", "-t", "-s", "1800", "-o", out_dir, p],
                                   capture_output=True, timeout=30)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            if not os.path.exists(png):
                return self.get_thumb(p, name)
            self.send_file(png, "image/png")

        def get_text(self, p, name):
            with open(p, "rb") as f:
                raw = f.read(300_000)
            return self.send(200, json.dumps({"text": raw.decode("utf-8", "replace")}, ensure_ascii=False))

        def do_POST(self):
            if self.headers.get("X-Token") != token:
                return self.send(403, json.dumps({"error": "token"}))
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self.send(400, "{}")
            u = urllib.parse.urlparse(self.path)
            if u.path == "/undo":
                return self.send(200, json.dumps(undo_last(), ensure_ascii=False))
            if u.path not in ("/trash", "/open"):
                return self.send(404, "{}")
            done, failed, moved = [], [], []
            for name in body.get("files") or []:
                p = safe_path(name)
                if not p:
                    failed.append(name)
                    continue
                if u.path == "/open":
                    r = subprocess.run(["open", p], capture_output=True)
                    (done if r.returncode == 0 else failed).append(name)
                    continue
                # -v indique où le fichier atterrit dans la Corbeille : c'est ce qui permet d'annuler.
                r = subprocess.run(["trash", "-v", p], capture_output=True, text=True)
                m = re.search(r'to "(.*)"\s*$', r.stdout.strip())
                if r.returncode == 0 and not os.path.exists(p):
                    done.append(name)
                    moved.append({"name": name, "from": p, "to": m.group(1) if m else None})
                else:
                    failed.append(name)
            if u.path == "/trash" and done:
                with lock:
                    stack = load_undo()
                    stack.setdefault(folder, []).append({"time": f"{dt.datetime.now():%Y-%m-%d %H:%M}", "items": moved})
                    stack[folder] = stack[folder][-50:]
                    save_undo(stack)
                with open(os.path.join(STATE, "trash-log.txt"), "a") as f:
                    for name in done:
                        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}\t{os.path.join(folder, name)}\n")
            self.send(200, json.dumps({"done": done, "failed": failed, "undo": undo_count()}, ensure_ascii=False))

    import threading
    lock = threading.Lock()
    undo_file = os.path.join(STATE, "undo.json")

    def load_undo():
        try:
            with open(undo_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_undo(stack):
        os.makedirs(STATE, exist_ok=True)
        tmp = undo_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(stack, f, ensure_ascii=False)
        os.replace(tmp, undo_file)

    def undo_count():
        return len(load_undo().get(folder, []))

    def undo_last():
        """Remet à leur place les fichiers de la dernière mise à la Corbeille."""
        with lock:
            stack = load_undo()
            ops = stack.get(folder, [])
            if not ops:
                return {"restored": [], "failed": [], "undo": 0}
            op = ops.pop()
            restored, failed = [], []
            for it in op["items"]:
                src, dst = it.get("to"), it["from"]
                if not src or not os.path.exists(src):
                    failed.append(it["name"])  # déjà sorti ou Corbeille vidée
                    continue
                if os.path.exists(dst):
                    # Un fichier du même nom est apparu entre-temps : on ne l'écrase pas.
                    base, ext = os.path.splitext(dst)
                    dst = f"{base} (restauré){ext}"
                    if os.path.exists(dst):
                        failed.append(it["name"])
                        continue
                try:
                    shutil.move(src, dst)
                    restored.append(os.path.basename(dst))
                except OSError:
                    failed.append(it["name"])
            save_undo(stack)
            return {"restored": restored, "failed": failed, "undo": len(ops)}

    return H


def main():
    ap = argparse.ArgumentParser(prog="screentri", description="Trie les fichiers d'un dossier, en local.")
    ap.add_argument("dossier", nargs="?", default=".")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="ne pas ouvrir le navigateur")
    ap.add_argument("--reanalyze", action="store_true", help="refaire l'analyse sans le cache")
    args = ap.parse_args()

    folder = os.path.realpath(os.path.expanduser(args.dossier))
    if not os.path.isdir(folder):
        sys.exit(f"Dossier introuvable : {folder}")
    if not shutil.which("trash"):
        sys.exit("La commande « trash » de macOS est introuvable.")
    chk = engine("check", {})
    if not chk or not chk[0].get("ok"):
        sys.exit("Le modèle d'IA d'Apple n'est pas disponible (Apple Intelligence doit être activé).")

    log(f"Dossier : {folder}")
    if not list_files(folder):
        sys.exit("Aucun fichier dans ce dossier.")

    with open(os.path.join(HERE, "ui.html"), encoding="utf-8") as f:
        page = f.read()
    token = secrets.token_hex(16)
    live = Live(folder)
    handler = make_handler(folder, live, token, page)
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            continue
    else:
        sys.exit("Aucun port libre.")
    url = f"http://127.0.0.1:{port}/"
    log(f"Interface : {url}  (Ctrl+C pour arrêter)")

    # L'interface s'ouvre tout de suite et se remplit pendant que l'analyse avance.
    def run():
        try:
            data = analyze(folder, live, args.reanalyze)
            counts = {g["id"]: sum(len(i["files"]) for i in g["items"]) for g in data["groups"]}
            log("Résultat : " + ", ".join(f"{counts[c]} {c}" for c in ("supprimer", "verifier", "garder", "sensible")))
        except Exception as e:
            log(f"Erreur pendant l'analyse : {e}")
            live.publish(phase="Erreur", error=str(e), finished=True)

    import threading
    threading.Thread(target=run, daemon=True).start()
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\nArrêté.")


if __name__ == "__main__":
    main()
