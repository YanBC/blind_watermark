"""PDF CLI and reproducible whole-page watermark demonstration.

Run from the repository root: python -m examples.pdf_watermark demo
"""
import argparse
from io import BytesIO
import json
from pathlib import Path

from reportlab.pdfgen import canvas

from blind_watermark import bw_notes
from blind_watermark.pdf import PdfError, PdfLimits, embed_pdf, extract_pdf

bw_notes.close()


def sample_pdf():
    """Three mixed-content pages with portrait, landscape and fractional sizes."""
    output = BytesIO()
    pdf = canvas.Canvas(output)
    for index, size in enumerate(((595, 842), (842, 595), (420.5, 595.25)), 1):
        width, height = size
        pdf.setPageSize(size)
        pdf.setFont('Helvetica-Bold', 24)
        pdf.drawString(40, height - 60, 'Whole-page PDF watermark demo')
        pdf.setFont('Helvetica', 14)
        pdf.drawString(40, height - 90, 'Page {}: text, vector graphics, and white space.'.format(index))
        pdf.setFillColorRGB(0.1, 0.4, 0.7)
        pdf.rect(40, 90, width - 80, 140, fill=1, stroke=0)
        pdf.setFillColorRGB(0.9, 0.6, 0.2)
        pdf.circle(width / 2, 280, 40, fill=1, stroke=0)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    demo = commands.add_parser('demo')
    demo.add_argument('--output-dir', type=Path, default=Path('output/pdf'))
    for command in ('embed', 'extract'):
        sub = commands.add_parser(command)
        sub.add_argument('input', type=Path)
        if command == 'embed':
            sub.add_argument('output', type=Path)
            sub.add_argument('--text', required=True, help='One document identifier for all pages')
        else:
            sub.add_argument('--bits', required=True, type=int)
        sub.add_argument('--password', required=True, type=int)
        sub.add_argument('--dpi', type=int, default=144)
        sub.add_argument('--max-pages', type=int, default=50)
        sub.add_argument('--max-output-mb', type=int, default=100)
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            args.output_dir.mkdir(parents=True, exist_ok=True)
            original = sample_pdf()
            marked, recipe = embed_pdf(original, 'DOC-2026-001', 1234)
            report = extract_pdf(marked, recipe['bits'], 1234, recipe['dpi'])
            report['expected_document_id'] = 'DOC-2026-001'
            report['passed'] = (report['document_id'] == 'DOC-2026-001' and
                                report['status'] == 'consistent' and report['agreeing_pages'] == 3)
            (args.output_dir / 'original.pdf').write_bytes(original)
            (args.output_dir / 'watermarked.pdf').write_bytes(marked)
            (args.output_dir / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2))
            return 0 if report['passed'] else 1
        limits = PdfLimits(max_pages=args.max_pages, max_total_pixels=args.max_pages * 4_000_000,
                           max_output_bytes=args.max_output_mb * 1_000_000)
        data = args.input.read_bytes()
        if args.command == 'embed':
            marked, recipe = embed_pdf(data, args.text, args.password, args.dpi, limits)
            args.output.write_bytes(marked)
            print(json.dumps(recipe))
            return 0
        report = extract_pdf(data, args.bits, args.password, args.dpi, limits)
        print(json.dumps(report, indent=2))
        return 0 if report['document_id'] is not None else 1
    except (PdfError, OSError) as exc:
        parser.exit(2, 'Error: {}\n'.format(exc))


if __name__ == '__main__':
    raise SystemExit(main())
