# screentri

**Sort a messy folder (Downloads, Desktop, screenshots…) with Apple's on-device AI. Nothing leaves your Mac.**

[Version française](README.fr.md)

screentri reads every file in a folder (screenshots, PDFs, Office and Pages documents, text, videos, installers…), works out what each one is about, and sorts it into four categories:

| | |
|---|---|
| 🗑️ **Deletable** | finished paperwork, old inspiration, screenshot bursts, settings, purchases, old installers |
| 🤔 **To check** | projects from a few weeks or months ago, personal photos, unreadable files |
| ✅ **Keep** | recent projects, upcoming tickets and appointments, invoices, contracts, CVs… |
| ⚠️ **Sensitive** | ID documents, IBANs, API keys, passwords, recovery codes |

You review everything in a local web page: previews, one-click selection, move to Trash, undo.

## Requirements

- Apple Silicon Mac, macOS 26 or later, **Apple Intelligence turned on**
- Python 3 (standard library only)
- Xcode command line tools: `xcode-select --install`

## Install

```sh
git clone https://github.com/theophilebuilds/screentri.git ~/Projects/screentri
cd ~/Projects/screentri
mkdir -p bin && swiftc -O analyze.swift -o bin/screentri-analyze

# optional: a `screentri` command
mkdir -p ~/.local/bin
printf '#!/bin/bash\nexec python3 -I "$HOME/Projects/screentri/screentri.py" "$@"\n' > ~/.local/bin/screentri
chmod +x ~/.local/bin/screentri
```

## Usage

```sh
screentri                 # current folder
screentri ~/Downloads     # another folder
screentri --reanalyze     # redo this folder from scratch
```

Options: `--port N` (default 8765), `--no-open` (don't open the browser). Only files at the top of the folder are sorted, not subfolders.

**Be patient on the first run.** The AI takes about 1 second per file: a 400-file Downloads folder takes around 5 to 7 minutes. The page opens right away and fills in as files are sorted, so you can start sorting before it finishes.

**Tip: do it in several sittings.** Stop at any time with Ctrl+C. Results are cached, so the next run picks up where you left off and files already read show up instantly.

## How it works

1. **Read** (Swift): text extraction with PDFKit, OCR with Vision, Quick Look previews, and a visual fingerprint for images.
2. **Group** (Python): files that show the same thing get analysed once: screenshot bursts, copies like `file (1).pdf`, near-identical images. Installers, photos without text and sensitive files are sorted by rules, without the AI.
3. **Analyse** (Swift): Apple's on-device model (Foundation Models) returns a topic, a content type and whether there is an upcoming date. It starts while files are still being read.
4. **Sort** (Python): fixed, readable rules in `decide()` pick the category from the type and the file's age. **The AI describes, the rules decide**, so every choice comes with a reason (e.g. "paperwork from 142 days ago, probably done").

Sensitive data (IBAN, passport, API keys, private keys, tokens…) is detected with regular expressions, before the AI and independently of it.

**Privacy and safety:** the server only listens on `127.0.0.1`, actions require a per-launch token, and files from the folder are never executed in the page. Deleting means macOS Trash, and Undo puts files back.

## Files

| | |
|---|---|
| `analyze.swift` | native engine: text, OCR, previews, Apple's model (compiled to `bin/screentri-analyze`) |
| `screentri.py` | cache, grouping, sorting rules, local web server, Trash and undo |
| `ui.html` | the interface |
| `~/.screentri/` | local cache, previews and Trash history |

To customise: delays and categories in `decide()`, sensitive patterns in `SENSITIVE` (`screentri.py`), AI content types in `Analyse` and `instructionsClassify` (`analyze.swift`, then recompile).

## Limits

- macOS only, on Macs that support Apple Intelligence.
- Apple's model is small: it sometimes gets the topic wrong or refuses a file. Refused files land in "To check".
- Rules and labels are designed for French content.
- Nothing is ever deleted for good, but look over "Deletable" before emptying the Trash.

## License

MIT
