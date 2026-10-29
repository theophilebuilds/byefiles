// screentri-analyze : extraction (texte + aperçu Quick Look) et analyse par le modèle d'Apple.
// Tout tourne sur le Mac, rien n'est envoyé sur internet.
// Modes (entrée JSON sur stdin, sortie JSON ligne par ligne sur stdout) :
//   check     -> dit si le modèle d'Apple est disponible
//   extract   -> {"files":[chemins],"thumbs":dossier} => {"path","kind","text","thumb"} par ligne
//   classify  -> {"today","parallel","items":[{"id","text","date","meta"}]} => {"id","sujet","type","aVenir"} par ligne
import AppKit
import Foundation
import FoundationModels
import PDFKit
import QuickLookThumbnailing
import UniformTypeIdentifiers
import Vision
import CryptoKit

func emit(_ obj: [String: Any]) {
    if let d = try? JSONSerialization.data(withJSONObject: obj),
       let s = String(data: d, encoding: .utf8) {
        print(s)
        fflush(stdout)
    }
}

func readInput() -> [String: Any] {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
}

// MARK: - Type de fichier

func kindOf(_ url: URL) -> String {
    let ext = url.pathExtension.lowercased()
    guard let t = UTType(filenameExtension: ext) else { return "other" }
    if t.conforms(to: .pdf) { return "pdf" }
    if t.conforms(to: .image) { return "image" }
    if t.conforms(to: .movie) || t.conforms(to: .video) { return "video" }
    if t.conforms(to: .audio) { return "audio" }
    if ["docx", "doc", "rtf", "rtfd", "odt", "wordml"].contains(ext) { return "rich" }
    if ["html", "htm", "svg", "xhtml", "webarchive"].contains(ext) { return "html" }
    if t.conforms(to: .text) || t.conforms(to: .sourceCode) || t.conforms(to: .json)
        || ["md", "markdown", "csv", "tsv", "log", "yaml", "yml", "toml", "ini", "env"].contains(ext) { return "text" }
    return "other"
}

// MARK: - OCR

func ocrCG(_ cg: CGImage) -> String {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["fr-FR", "en-US"]
    req.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: cg).perform([req])
    return (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

func cgFromFile(_ path: String) -> CGImage? {
    NSImage(contentsOfFile: path)?.cgImage(forProposedRect: nil, context: nil, hints: nil)
}

// MARK: - Aperçu Quick Look (JPEG)

func thumbnail(_ url: URL, to out: String, size: CGFloat) -> CGImage? {
    let req = QLThumbnailGenerator.Request(fileAt: url, size: CGSize(width: size, height: size),
                                           scale: 1, representationTypes: .thumbnail)
    let sem = DispatchSemaphore(value: 0)
    var result: CGImage?
    QLThumbnailGenerator.shared.generateBestRepresentation(for: req) { rep, _ in
        result = rep?.cgImage
        sem.signal()
    }
    _ = sem.wait(timeout: .now() + 30)
    guard let cg = result else { return nil }
    let rep = NSBitmapImageRep(cgImage: cg)
    if let data = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.8]) {
        try? data.write(to: URL(fileURLWithPath: out))
    }
    return cg
}

// MARK: - Extraction du texte

func extract(_ path: String, thumbsDir: String) -> [String: Any] {
    let url = URL(fileURLWithPath: path)
    let kind = kindOf(url)
    let hash = SHA256.hash(data: Data(path.utf8)).map { String(format: "%02x", $0) }.joined().prefix(24)
    let thumbPath = "\(thumbsDir)/\(hash).jpg"
    var text = ""

    switch kind {
    case "image":
        if let cg = cgFromFile(path) { text = ocrCG(cg) }
    case "pdf":
        if let doc = PDFDocument(url: url) {
            var parts: [String] = []
            for i in 0..<min(doc.pageCount, 5) {
                if let s = doc.page(at: i)?.string { parts.append(s) }
            }
            text = parts.joined(separator: "\n")
            // PDF scanné : pas de texte, on lit la première page en OCR
            if text.trimmingCharacters(in: .whitespacesAndNewlines).count < 20,
               let page = doc.page(at: 0),
               let cg = page.thumbnail(of: CGSize(width: 1800, height: 1800), for: .mediaBox)
                .cgImage(forProposedRect: nil, context: nil, hints: nil) {
                text = ocrCG(cg)
            }
        }
    case "rich":
        if let s = try? NSAttributedString(url: url, options: [:], documentAttributes: nil) { text = s.string }
    case "text", "html":
        if let h = FileHandle(forReadingAtPath: path) {
            let d = h.readData(ofLength: 40_000)
            try? h.close()
            text = String(decoding: d, as: UTF8.self)
        }
    default:
        break
    }
    // Miniature de 300 px pour la grille. Quand il n'y a aucun texte à lire (slides, Pages, vidéo…),
    // on la fait en 900 px pour lire en OCR le texte visible dessus.
    let needsOCR = text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && kind != "image"
    let thumbCG = thumbnail(url, to: thumbPath, size: needsOCR ? 900 : 300)
    if needsOCR, let cg = thumbCG {
        text = ocrCG(cg)
    }
    var r: [String: Any] = ["path": path, "kind": kind, "text": String(text.prefix(20_000))]
    if thumbCG != nil { r["thumb"] = thumbPath }
    return r
}

func runExtract(_ input: [String: Any]) {
    let files = input["files"] as? [String] ?? []
    let thumbsDir = input["thumbs"] as? String ?? NSTemporaryDirectory()
    try? FileManager.default.createDirectory(atPath: thumbsDir, withIntermediateDirectories: true)
    let lock = NSLock()
    let queue = OperationQueue()
    queue.maxConcurrentOperationCount = 4
    for f in files {
        queue.addOperation {
            let r = autoreleasepool { extract(f, thumbsDir: thumbsDir) }
            lock.lock(); emit(r); lock.unlock()
        }
    }
    queue.waitUntilAllOperationsAreFinished()
}

// MARK: - Analyse par le modèle d'Apple

@Generable
struct Analyse {
    @Guide(description: "Sujet précis et réutilisable, 2 à 5 mots en français. Exemples : 'Parcoursup', 'Projet Plumb', 'Notes Pronote', 'Facture EDF', 'Installeur Cursor', 'Cours de maths'")
    var sujet: String
    @Guide(description: "Type de contenu", .anyOf([
        "projet_travail", "cours_etudes", "administratif", "document_a_conserver", "identite_banque",
        "inspiration", "reseaux_sociaux", "achats", "divertissement", "reglages_systeme",
        "conversation_ia", "photo_perso", "installeur_archive", "autre",
    ]))
    var type: String
    @Guide(description: "true seulement si le contenu montre un billet, un rendez-vous, une réservation ou une échéance dont la date est après aujourd'hui")
    var aVenir: Bool
}

let instructionsClassify = """
Tu aides à trier les fichiers d'un dossier (captures d'écran, PDF, documents, photos, vidéos, installeurs…).
Tu reçois le nom du fichier, son type, sa taille et le texte qu'il contient (parfois de l'OCR bruité, parfois vide).
Les noms d'onglets et de favoris du navigateur en haut d'une capture ne sont pas le sujet : concentre-toi sur le contenu principal.
Types :
- projet_travail : code, design, app, site, client, entreprise, prospection, outils de dev
- cours_etudes : cours, exercices, examens, révisions, notes scolaires
- administratif : démarches, admissions, candidatures, logement, assurance, énergie, transports, billets
- document_a_conserver : facture, contrat, bail, attestation, diplôme, relevé, bulletin de paie, avis d'impôt, CV, lettre officielle
- identite_banque : pièce d'identité, passeport, RIB, banque, prêt, mot de passe, clé d'API, données personnelles
- inspiration : posts, vidéos, articles ou idées d'autres personnes gardés comme référence
- reseaux_sociaux : profils, messages, fils d'actualité
- achats : boutiques, commandes, produits
- divertissement : films, séries, musique, jeux
- reglages_systeme : préférences du Mac, fenêtres système
- conversation_ia : discussions avec un assistant IA (Claude, ChatGPT, Gemini)
- photo_perso : photo prise avec un appareil ou un téléphone (personnes, lieux, objets), sans texte à l'écran
- installeur_archive : .dmg, .pkg, .zip, .iso, installeurs d'applications, archives compressées
- autre : rien de tout ça
"""

struct Resultat: Sendable {
    let id: String
    let sujet: String
    let type: String
    let aVenir: Bool
    let erreur: String?

    var json: [String: Any] {
        var r: [String: Any] = ["id": id, "sujet": sujet, "type": type, "aVenir": aVenir]
        if let erreur { r["error"] = erreur }
        return r
    }
}

func classifyOne(id: String, text: String, date: String, meta: String, today: String) async -> Resultat {
    let prompt = """
    Aujourd'hui : \(today). Fichier créé le : \(date).
    \(meta)
    Texte :
    \(String(text.prefix(1400)))
    """
    do {
        let session = LanguageModelSession(instructions: instructionsClassify)
        let r = try await session.respond(to: prompt, generating: Analyse.self,
                                          options: GenerationOptions(temperature: 0))
        let a = r.content
        return Resultat(id: id, sujet: a.sujet, type: a.type, aVenir: a.aVenir, erreur: nil)
    } catch {
        // Le modèle refuse parfois (garde-fous) ou dépasse sa taille de contexte.
        return Resultat(id: id, sujet: "", type: "autre", aVenir: false, erreur: String(describing: error))
    }
}

// Plusieurs fichiers sont analysés en même temps (« parallel » à la fois, 1 = un par un).
// Les résultats sortent dans l'ordre où ils finissent : Python les retrouve grâce à leur id.
func runClassify(_ input: [String: Any]) async {
    let today = input["today"] as? String ?? ""
    let parallel = max(1, input["parallel"] as? Int ?? 1)
    let items = (input["items"] as? [[String: Any]] ?? []).map {
        (id: "\($0["id"] ?? "")", text: $0["text"] as? String ?? "",
         date: $0["date"] as? String ?? "", meta: $0["meta"] as? String ?? "")
    }
    await withTaskGroup(of: Resultat.self) { group in
        var next = 0
        func launch() {
            let it = items[next]
            next += 1
            group.addTask { await classifyOne(id: it.id, text: it.text, date: it.date, meta: it.meta, today: today) }
        }
        while next < min(parallel, items.count) { launch() }
        for await r in group {
            emit(r.json)
            if next < items.count { launch() }
        }
    }
}

// MARK: - Point d'entrée

let mode = CommandLine.arguments.dropFirst().first ?? ""
switch mode {
case "check":
    let a = SystemLanguageModel.default.availability
    if case .available = a { emit(["ok": true]) } else { emit(["ok": false, "raison": String(describing: a)]) }
case "extract":
    runExtract(readInput())
case "classify":
    await runClassify(readInput())
default:
    FileHandle.standardError.write("usage: screentri-analyze check|extract|classify\n".data(using: .utf8)!)
    exit(2)
}
