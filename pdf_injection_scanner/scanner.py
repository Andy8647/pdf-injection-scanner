#!/usr/bin/env python3
"""PDF Prompt Injection Scanner - Detect hidden prompt injections in PDF files."""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import click
import pdfplumber
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.table import Table

console = Console()

# Common prompt injection patterns — English
INJECTION_PATTERNS_EN = [
    (r"(?i)if\s+you\s+are\s+(an?\s+)?ai\b", "AI identity check"),
    (r"(?i)ignore\s+(all\s+)?previous\s+instructions?", "Instruction override"),
    (r"(?i)you\s+are\s+(a\s+)?(language\s+model|llm|ai\s+assistant|chatbot)", "AI identity assertion"),
    (r"(?i)do\s+not\s+follow\s+(the\s+)?(user|original)", "Instruction hijack"),
    (r"(?i)system\s*prompt", "System prompt reference"),
    (r"(?i)disregard\s+(all\s+)?(prior|previous|above)", "Instruction override"),
    (r"(?i)new\s+instructions?\s*:", "New instruction injection"),
    (r"(?i)act\s+as\s+if", "Behavior override"),
    (r"(?i)pretend\s+(you|that)", "Behavior override"),
    (r"(?i)override\s+(your|the)\s+(instructions?|rules?|guidelines?)", "Rule override"),
    (r"(?i)from\s+now\s+on\s+(you|ignore|forget)", "Persistent override"),
    (r"(?i)forget\s+(everything|all|your)", "Memory wipe attempt"),
    (r"(?i)<\s*system\s*>", "System tag injection"),
    (r"(?i)\[INST\]", "Instruction tag injection"),
    (r"(?i)###\s*(system|instruction|human|assistant)", "Role tag injection"),
    (r"(?i)if\s+you\s+are\s+(a\s+)?(gpt|claude|gemini|copilot|llama)", "Model-specific check"),
    (r"(?i)(please\s+)?include\s+(the\s+)?(word|phrase|sentence)\s+.{1,40}\s+in\s+your", "Canary word injection"),
]

# Common prompt injection patterns — Chinese
INJECTION_PATTERNS_CN = [
    (r"如果你是.{0,4}(AI|人工智能|语言模型|大模型|机器人|助手)", "AI身份检测"),
    (r"忽略.{0,4}(之前|以上|前面|上面|先前|所有).{0,4}(指令|指示|规则|要求|提示)", "指令覆盖"),
    (r"(无视|忽视|跳过|丢弃).{0,4}(之前|以上|前面|先前|所有).{0,4}(指令|规则|要求|内容)", "指令覆盖"),
    (r"(请|你)?在.{0,6}(回答|回复|响应|输出).{0,6}(包含|加上|提到|引用|加入).{0,20}(这个词|一词|这句话)", "暗号词注入"),
    (r"(请|你)?以.{0,10}(开头|开始|作为开头)", "行为指定"),
    (r"(请|你)?(假装|假设|扮演|伪装).{0,6}(你是|自己是|成为)", "行为覆盖"),
    (r"(你现在是|你的角色是|你扮演).{0,10}(不受限|无限制|没有约束)", "角色劫持"),
    (r"(输出|显示|告诉我|泄露).{0,6}(系统提示|系统指令|system\s*prompt)", "系统提示泄露"),
    (r"(忘记|抛弃|放弃).{0,4}(之前|以上|所有|一切).{0,4}(规则|指令|设定|限制)", "记忆清除"),
    (r"(不要|禁止|不准|不许).{0,4}(遵守|遵循|执行).{0,6}(之前|以上|原始|原来)", "指令劫持"),
    (r"(新的?指令|新的?规则|新的?要求)\s*[:：]", "新指令注入"),
    (r"(AI|人工智能|大模型|语言模型).{0,4}(注意|请注意|须知)", "AI定向指令"),
    (r"(从现在开始|即刻起|此刻起).{0,6}(你|忽略|忘记|不再)", "持续覆盖"),
    (r"(回答|回复).{0,6}(结尾|末尾|最后).{0,6}(加上|添加|写上)", "暗号词注入"),
]

INJECTION_PATTERNS = INJECTION_PATTERNS_EN + INJECTION_PATTERNS_CN


@dataclass
class Finding:
    page: int
    finding_type: str
    description: str
    content: str
    location: str = ""
    severity: str = "medium"


def is_white_or_near_white(color, threshold=0.9):
    """Check if a color is white or near-white."""
    if color is None:
        return False
    if isinstance(color, (int, float)):
        # Grayscale: 1.0 = white
        return color > threshold
    if isinstance(color, (list, tuple)):
        if len(color) == 1:
            return color[0] > threshold
        if len(color) == 3:
            # RGB: (1,1,1) = white
            return all(c > threshold for c in color)
        if len(color) == 4:
            # CMYK: (0,0,0,0) = white
            return all(c < (1 - threshold) for c in color)
    return False


def is_same_as_bg(color, bg_color):
    """Check if text color matches background color (camouflaged)."""
    if color is None or bg_color is None:
        return False
    if type(color) != type(bg_color):
        return False
    if isinstance(color, (int, float)):
        return abs(color - bg_color) < 0.05
    if isinstance(color, (list, tuple)) and len(color) == len(bg_color):
        return all(abs(a - b) < 0.05 for a, b in zip(color, bg_color))
    return False


def bg_color_at(page, x, y):
    """Return the fill color of the topmost shape covering point (x, y), or None.

    pdfminer yields objects in content-stream (draw) order, so the last
    matching filled shape is the one drawn on top.
    """
    color = None
    for shape in list(page.rects) + list(page.curves):
        if not shape.get("fill"):
            continue
        fill = shape.get("non_stroking_color")
        if fill is None:
            continue
        if (shape["x0"] - 1 <= x <= shape["x1"] + 1
                and shape["top"] - 1 <= y <= shape["bottom"] + 1):
            color = fill
    return color


def group_chars_into_segments(chars):
    """Group nearby characters into readable text segments."""
    if not chars:
        return []

    segments = []
    current = [chars[0]]

    for char in chars[1:]:
        prev = current[-1]
        same_line = abs(char.get("top", 0) - prev.get("top", 0)) < 3
        close_x = (char.get("x0", 0) - prev.get("x1", 0)) < prev.get("size", 12) * 0.5

        if same_line and close_x:
            current.append(char)
        else:
            segments.append(current)
            current = [char]

    segments.append(current)
    return segments


def segment_text(segment):
    """Extract text from a character segment."""
    return "".join(c.get("text", "") for c in segment).strip()


def segment_location(segment):
    """Get human-readable location string."""
    c = segment[0]
    return f"x={c.get('x0', 0):.0f}, y={c.get('top', 0):.0f}"


def scan_hidden_text(chars, page_num, page=None, bg_threshold=0.8):
    """Detect white/invisible text.

    White text is only invisible when it sits on a white (or absent)
    background. White text on a dark filled shape — e.g. a black title
    banner — is a legitimate design choice and must not be flagged.
    """
    findings = []
    white_chars = []
    for c in chars:
        if not is_white_or_near_white(c.get("non_stroking_color")):
            continue
        if page is not None:
            cx = (c.get("x0", 0) + c.get("x1", 0)) / 2
            cy = (c.get("top", 0) + c.get("bottom", 0)) / 2
            bg = bg_color_at(page, cx, cy)
            if bg is not None and not is_white_or_near_white(bg, bg_threshold):
                # Contrasting (dark) background: the text is visible, skip it.
                continue
        white_chars.append(c)

    for segment in group_chars_into_segments(white_chars):
        text = segment_text(segment)
        if len(text) > 2:
            findings.append(Finding(
                page=page_num,
                finding_type="White/Invisible Text",
                description="White or near-white text, invisible to readers but extractable by AI",
                content=text,
                location=segment_location(segment),
                severity="high",
            ))
    return findings, set(id(c) for c in white_chars)


def scan_tiny_text(chars, page_num, exclude_ids, threshold=2.0):
    """Detect extremely small text."""
    findings = []
    tiny_chars = [
        c for c in chars
        if c.get("size") is not None
        and c["size"] < threshold
        and id(c) not in exclude_ids
    ]

    for segment in group_chars_into_segments(tiny_chars):
        text = segment_text(segment)
        if len(text) > 2:
            size = segment[0].get("size", 0)
            findings.append(Finding(
                page=page_num,
                finding_type="Tiny Text",
                description=f"Text at {size:.1f}pt — too small to see, but parseable by tools",
                content=text,
                location=segment_location(segment),
                severity="high",
            ))
    return findings


def scan_offpage_text(chars, page_num, page_width, page_height):
    """Detect text positioned outside the visible page area."""
    findings = []
    offpage_chars = [
        c for c in chars
        if c.get("x1", 0) < 0
        or c.get("x0", 0) > page_width
        or c.get("bottom", 0) < 0
        or c.get("top", 0) > page_height
    ]

    for segment in group_chars_into_segments(offpage_chars):
        text = segment_text(segment)
        if len(text) > 2:
            findings.append(Finding(
                page=page_num,
                finding_type="Off-Page Text",
                description="Text placed outside the visible page boundaries",
                content=text,
                location=segment_location(segment),
                severity="high",
            ))
    return findings


def scan_suspicious_patterns(full_text, page_num):
    """Detect known prompt injection keyword patterns."""
    findings = []
    for pattern, label in INJECTION_PATTERNS:
        for match in re.finditer(pattern, full_text):
            start = max(0, match.start() - 40)
            end = min(len(full_text), match.end() + 40)
            context = full_text[start:end].replace("\n", " ")
            findings.append(Finding(
                page=page_num,
                finding_type=f"Suspicious Pattern",
                description=f"{label} — matches: {pattern}",
                content=f"...{context}...",
                severity="medium",
            ))
    return findings


def scan_page(page, page_num):
    """Scan a single page for all types of injection attacks."""
    findings = []
    chars = page.chars

    if not chars:
        return findings

    # Hidden text detections
    white_findings, white_ids = scan_hidden_text(chars, page_num, page)
    findings.extend(white_findings)
    findings.extend(scan_tiny_text(chars, page_num, white_ids))
    findings.extend(scan_offpage_text(chars, page_num, page.width, page.height))

    # Content pattern detection
    full_text = page.extract_text() or ""
    findings.extend(scan_suspicious_patterns(full_text, page_num))

    return findings


def deduplicate(findings):
    """Remove duplicate findings on the same page with same content."""
    seen = set()
    unique = []
    for f in findings:
        key = (f.page, f.finding_type, f.content)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


SEVERITY_COLORS = {"high": "red", "medium": "yellow", "low": "blue"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def print_findings(findings, verbose):
    """Pretty-print findings to the terminal."""
    if not findings:
        console.print(Panel(
            "[bold green]No prompt injection attacks detected.[/bold green]",
            title="Result",
        ))
        return

    high = sum(1 for f in findings if f.severity == "high")
    med = sum(1 for f in findings if f.severity == "medium")
    summary = f"[bold red]{len(findings)} potential injection(s)[/bold red]"
    if high:
        summary += f"  [red]({high} high)[/red]"
    if med:
        summary += f"  [yellow]({med} medium)[/yellow]"
    console.print(Panel(summary, title="Result"))

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", width=3, justify="right")
    table.add_column("Page", width=5, justify="center")
    table.add_column("Severity", width=8, justify="center")
    table.add_column("Type", width=22)
    table.add_column("Content", max_width=60)

    findings.sort(key=lambda f: (f.page, SEVERITY_ORDER.get(f.severity, 9)))

    for i, f in enumerate(findings, 1):
        color = SEVERITY_COLORS.get(f.severity, "white")
        content_preview = f.content[:100] + ("..." if len(f.content) > 100 else "")
        table.add_row(
            str(i),
            str(f.page),
            f"[{color}]{f.severity.upper()}[/{color}]",
            escape(f.finding_type),
            escape(content_preview),
        )

    console.print(table)

    if verbose:
        console.print("\n[bold]Detailed Findings:[/bold]\n")
        for i, f in enumerate(findings, 1):
            color = SEVERITY_COLORS.get(f.severity, "white")
            console.print(f"[bold]#{i}[/bold] [{color}]{escape(f.finding_type)}[/{color}]")
            console.print(f"  Page: {f.page}")
            console.print(f"  Severity: {f.severity}")
            console.print(f"  Description: {escape(f.description)}")
            console.print(f"  Content: {escape(f.content)}")
            console.print()


@click.command()
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--json", "output_json", is_flag=True, help="Output results as JSON")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed findings")
def main(pdf_path: Path, output_json: bool, verbose: bool):
    """Scan a PDF file for hidden prompt injection attacks.

    Detects white/invisible text, tiny text, off-page text,
    and suspicious prompt injection patterns (EN + CN).
    """
    console.print(f"\n[bold]Scanning:[/bold] {pdf_path}\n")

    all_findings = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total} pages"),
                console=console,
            ) as progress:
                task = progress.add_task("Scanning", total=total)
                for i, page in enumerate(pdf.pages, 1):
                    all_findings.extend(scan_page(page, i))
                    progress.update(task, advance=1)
    except Exception as e:
        console.print(f"[red]Error reading PDF: {e}[/red]")
        sys.exit(1)

    all_findings = deduplicate(all_findings)

    if output_json:
        data = [
            {
                "page": f.page,
                "type": f.finding_type,
                "description": f.description,
                "content": f.content,
                "severity": f.severity,
            }
            for f in all_findings
        ]
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print_findings(all_findings, verbose)


if __name__ == "__main__":
    main()
