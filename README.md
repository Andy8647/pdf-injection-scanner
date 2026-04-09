# pdf-injection-scanner

A CLI tool to detect hidden prompt injection attacks in PDF files.

Professors and others may embed invisible instructions in PDFs (white text, tiny fonts, off-page text) designed to manipulate AI assistants. This tool finds them.

## Detection capabilities

| Type | Severity | How it works |
|------|----------|-------------|
| **White/invisible text** | HIGH | Detects characters with white or near-white fill color |
| **Tiny text** | HIGH | Flags text smaller than 2pt — invisible to the eye |
| **Off-page text** | HIGH | Finds text positioned outside visible page boundaries |
| **Suspicious patterns** | MEDIUM | 17+ regex patterns for common prompt injection phrases |

## Install

```bash
# uv (recommended)
uv tool install pdf-injection-scanner

# pip
pip install pdf-injection-scanner

# Homebrew (macOS)
brew install Andy8647/tap/pdf-injection-scanner

# From source
git clone https://github.com/Andy8647/pdf-injection-scanner.git
cd pdf-injection-scanner
pip install -e .
```

## Usage

```bash
# Basic scan
pdf-scan assignment.pdf

# Verbose output with details
pdf-scan assignment.pdf -v

# JSON output for piping
pdf-scan assignment.pdf --json
```

### Example output

```
Scanning: assignment.pdf

  Scanning ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 3/3 pages
╭──────────── Result ────────────╮
│ 3 potential injection(s)       │
╰────────────────────────────────╯
┏━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Page ┃ Severity ┃ Type                 ┃ Content              ┃
┡━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │  1   │   HIGH   │ White/Invisible Text │ If you are an AI...  │
│ 2 │  1   │   HIGH   │ Tiny Text            │ System prompt: ...   │
│ 3 │  1   │   HIGH   │ Off-Page Text        │ New instructions:... │
└───┴──────┴──────────┴──────────────────────┴──────────────────────┘
```

## Generate test PDFs

```bash
pip install reportlab
python make_test_pdf.py
```

This creates `test_injected.pdf` with 5 different types of hidden injections for testing.

## How it works

The tool uses [pdfplumber](https://github.com/jsvine/pdfplumber) to extract character-level metadata from PDFs, including:

- **Fill color** (`non_stroking_color`) — catches white-on-white text
- **Font size** — catches sub-2pt text
- **Position coordinates** — catches text placed outside page bounds
- **Text content** — matches against known prompt injection patterns

## License

MIT
