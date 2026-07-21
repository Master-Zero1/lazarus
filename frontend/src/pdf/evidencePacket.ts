export interface EvidencePdfSection {
  title: string;
  path: string;
  content: string;
  format: "markdown" | "json";
}

export interface EvidencePdfOptions {
  runId: string;
  sections: EvidencePdfSection[];
}

const PALETTE = {
  ink: [26, 26, 26] as const,
  paper: [245, 240, 232] as const,
  paperDeep: [238, 233, 224] as const,
  yellow: [255, 204, 0] as const,
  blue: [0, 85, 255] as const,
  red: [230, 59, 46] as const,
  muted: [90, 88, 83] as const,
};

function normalizePdfText(value: string): string {
  /** Normalize common Markdown typography to characters in built-in PDF fonts. */

  return value
    .replace(/\r\n?/g, "\n")
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"')
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/\u2026/g, "...")
    .replace(/\u2192/g, "->")
    .replace(/\u2713/g, "[x]")
    .replace(/[^\x20-\x7E\n\t]/g, "?");
}

function stripMarkdown(value: string): string {
  return normalizePdfText(value)
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1");
}

function safeFileStem(runId: string): string {
  return runId.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 64) || "evidence";
}

/**
 * Build a searchable, client-side PDF evidence packet. The deliberate visual
 * choices mirror the dashboard's paper/ink/yellow/blue neo-brutalist system,
 * while base PDF fonts keep the export reliable on every browser.
 */
export async function downloadEvidencePdf({ runId, sections }: EvidencePdfOptions): Promise<void> {
  const { jsPDF } = await import("jspdf");
  const doc = new jsPDF({
    orientation: "portrait",
    unit: "pt",
    format: "a4",
    compress: true,
  });

  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 44;
  const footerY = pageHeight - 28;
  const contentWidth = pageWidth - margin * 2;
  let y = 92;

  const setColor = (color: readonly [number, number, number]) => doc.setTextColor(...color);
  const setFill = (color: readonly [number, number, number]) => doc.setFillColor(...color);
  const setDraw = (color: readonly [number, number, number]) => doc.setDrawColor(...color);

  const drawPageChrome = () => {
    setFill(PALETTE.paper);
    doc.rect(0, 0, pageWidth, pageHeight, "F");
    setFill(PALETTE.ink);
    doc.rect(0, 0, pageWidth, 28, "F");
    doc.setFont("courier", "bold");
    doc.setFontSize(8);
    setColor(PALETTE.paper);
    doc.text("LAZARUS // REVIVAL EVIDENCE PACKET", margin, 18);
    setDraw(PALETTE.ink);
    doc.setLineWidth(1.5);
    doc.line(margin, 66, pageWidth - margin, 66);
    y = 92;
  };

  const newPage = () => {
    doc.addPage();
    drawPageChrome();
  };

  const ensureSpace = (height: number) => {
    if (y + height > footerY - 16) newPage();
  };

  const writeWrapped = (
    text: string,
    options: {
      x?: number;
      width?: number;
      font?: "helvetica" | "courier";
      style?: "normal" | "bold";
      size?: number;
      color?: readonly [number, number, number];
      leading?: number;
      after?: number;
    } = {},
  ) => {
    const {
      x = margin,
      width = contentWidth,
      font = "helvetica",
      style = "normal",
      size = 9.5,
      color = PALETTE.ink,
      leading = size * 1.45,
      after = 8,
    } = options;
    const lines = doc.splitTextToSize(normalizePdfText(text), width) as string[];
    let index = 0;
    doc.setFont(font, style);
    doc.setFontSize(size);
    setColor(color);
    while (index < lines.length) {
      const availableLines = Math.max(1, Math.floor((footerY - 16 - y) / leading));
      const chunk = lines.slice(index, index + availableLines);
      doc.text(chunk, x, y);
      y += chunk.length * leading;
      index += chunk.length;
      if (index < lines.length) newPage();
    }
    y += after;
  };

  const writeSectionTitle = (title: string, path: string) => {
    ensureSpace(58);
    setFill(PALETTE.yellow);
    doc.rect(margin, y - 14, contentWidth, 26, "F");
    doc.setFont("helvetica", "bold");
    doc.setFontSize(14);
    setColor(PALETTE.ink);
    doc.text(normalizePdfText(title.toUpperCase()), margin + 9, y + 3);
    y += 23;
    writeWrapped(path, {
      font: "courier",
      size: 7.5,
      color: PALETTE.muted,
      leading: 10,
      after: 12,
    });
  };

  const writeHeading = (text: string, level: number) => {
    const size = level === 1 ? 18 : level === 2 ? 13.5 : 11;
    const color = level >= 3 ? PALETTE.blue : PALETTE.ink;
    const lines = doc.splitTextToSize(stripMarkdown(text), contentWidth) as string[];
    ensureSpace(lines.length * size * 1.55 + size * 0.8);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(size);
    setColor(color);
    doc.text(lines, margin, y);
    y += lines.length * size * 1.55;
    if (level === 1) {
      setDraw(PALETTE.ink);
      doc.setLineWidth(1.25);
      doc.line(margin, y - 5, pageWidth - margin, y - 5);
      y += 8;
    }
  };

  const writeBullet = (text: string, numbered?: string) => {
    const bulletX = margin;
    const textX = margin + 15;
    const textWidth = contentWidth - 15;
    const cleaned = stripMarkdown(text);
    const lines = doc.splitTextToSize(cleaned, textWidth) as string[];
    const leading = 13.5;
    let index = 0;
    while (index < lines.length) {
      const availableLines = Math.max(1, Math.floor((footerY - 16 - y) / leading));
      const chunk = lines.slice(index, index + availableLines);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(9.5);
      setColor(PALETTE.ink);
      if (index === 0) {
        doc.setFont("courier", "bold");
        doc.text(numbered ?? "-", bulletX, y);
      }
      doc.setFont("helvetica", "normal");
      doc.text(chunk, textX, y);
      y += chunk.length * leading;
      index += chunk.length;
      if (index < lines.length) newPage();
    }
    y += 3;
  };

  const writeCode = (content: string) => {
    const normalized = normalizePdfText(content);
    const codeLines = normalized.split("\n");
    doc.setFont("courier", "normal");
    doc.setFontSize(7.2);
    const leading = 9.5;
    for (const codeLine of codeLines) {
      const wrapped = doc.splitTextToSize(codeLine || " ", contentWidth - 14) as string[];
      const height = Math.max(1, wrapped.length) * leading + 3;
      ensureSpace(height);
      setFill(PALETTE.paperDeep);
      doc.rect(margin, y - 7.5, contentWidth, height, "F");
      setColor(PALETTE.ink);
      doc.text(wrapped, margin + 7, y);
      y += Math.max(1, wrapped.length) * leading;
    }
    y += 10;
  };

  const writeMarkdown = (content: string) => {
    const lines = normalizePdfText(content).split("\n");
    let paragraph: string[] = [];
    let codeBlock: string[] = [];
    let inCodeBlock = false;

    const flushParagraph = () => {
      if (paragraph.length) {
        writeWrapped(stripMarkdown(paragraph.join(" ")));
        paragraph = [];
      }
    };
    const flushCode = () => {
      if (codeBlock.length) {
        writeCode(codeBlock.join("\n"));
        codeBlock = [];
      }
    };

    for (const line of lines) {
      if (line.trimStart().startsWith("```")) {
        flushParagraph();
        if (inCodeBlock) flushCode();
        inCodeBlock = !inCodeBlock;
        continue;
      }
      if (inCodeBlock) {
        codeBlock.push(line);
        continue;
      }
      const heading = /^(#{1,4})\s+(.+)$/.exec(line);
      const bullet = /^\s*-\s+(?:\[[ xX]\]\s+)?(.+)$/.exec(line);
      const numbered = /^\s*(\d+)\.\s+(.+)$/.exec(line);
      if (heading) {
        flushParagraph();
        writeHeading(heading[2], heading[1].length);
      } else if (bullet) {
        flushParagraph();
        writeBullet(bullet[1]);
      } else if (numbered) {
        flushParagraph();
        writeBullet(numbered[2], `${numbered[1]}.`);
      } else if (/^\s*[-*_]{3,}\s*$/.test(line)) {
        flushParagraph();
        ensureSpace(12);
        setDraw(PALETTE.ink);
        doc.setLineWidth(0.8);
        doc.line(margin, y, pageWidth - margin, y);
        y += 12;
      } else if (!line.trim()) {
        flushParagraph();
      } else if (line.trimStart().startsWith(">")) {
        flushParagraph();
        writeWrapped(stripMarkdown(line.replace(/^\s*>\s?/, "")), {
          x: margin + 12,
          width: contentWidth - 12,
          color: PALETTE.muted,
        });
      } else if (line.includes("|") && line.trimStart().startsWith("|")) {
        flushParagraph();
        writeCode(line);
      } else {
        paragraph.push(line.trim());
      }
    }
    flushParagraph();
    flushCode();
  };

  const writeJson = (content: string) => {
    try {
      writeCode(JSON.stringify(JSON.parse(content), null, 2));
    } catch {
      writeCode(content);
    }
  };

  const drawCover = () => {
    setFill(PALETTE.ink);
    doc.rect(0, 0, pageWidth, pageHeight, "F");
    setFill(PALETTE.yellow);
    doc.rect(margin, 100, 11, 290, "F");
    doc.setFont("helvetica", "bold");
    doc.setFontSize(36);
    setColor(PALETTE.paper);
    doc.text("LAZARUS", margin + 28, 150);
    doc.setFont("courier", "bold");
    doc.setFontSize(13);
    setColor(PALETTE.yellow);
    doc.text("REVIVAL EVIDENCE PACKET", margin + 28, 180);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(12);
    setColor(PALETTE.paper);
    doc.text("A detailed, client-generated record of the", margin + 28, 232);
    doc.text("available Lazarus reports and reviewed outputs.", margin + 28, 251);
    doc.setFont("courier", "normal");
    doc.setFontSize(9);
    setColor(PALETTE.paperDeep);
    doc.text(`RUN // ${safeFileStem(runId)}`, margin + 28, 310);
    doc.text(`GENERATED // ${new Date().toISOString()}`, margin + 28, 328);
    doc.text(`INCLUDED SOURCES // ${sections.length}`, margin + 28, 346);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    setColor(PALETTE.paperDeep);
    doc.text("Raw issue/PR snapshots and the private local checkout are intentionally excluded.", margin + 28, 420);
  };

  drawCover();
  newPage();
  writeSectionTitle("Included evidence", "Selected safe artifacts from this Lazarus run");
  for (const section of sections) writeBullet(`${section.title} — ${section.path}`);

  for (const section of sections) {
    newPage();
    writeSectionTitle(section.title, section.path);
    if (section.format === "json") writeJson(section.content);
    else writeMarkdown(section.content);
  }

  const totalPages = doc.getNumberOfPages();
  for (let page = 1; page <= totalPages; page += 1) {
    doc.setPage(page);
    doc.setFont("courier", "normal");
    doc.setFontSize(7);
    if (page === 1) {
      setColor(PALETTE.paperDeep);
      doc.text(`PAGE ${page} / ${totalPages}`, pageWidth - margin, footerY, { align: "right" });
    } else {
      setColor(PALETTE.muted);
      doc.text(`RUN ${safeFileStem(runId)} // PAGE ${page} / ${totalPages}`, margin, footerY);
      doc.text("LAZARUS EVIDENCE", pageWidth - margin, footerY, { align: "right" });
    }
  }

  doc.save(`lazarus-evidence-${safeFileStem(runId)}.pdf`);
}
