"""Whole-page PDF watermarks: one identifier, repeated on every page.

The payload uses WaterMark's existing string encoding (no PDF metadata payload).
Output pages are lossless raster images, not native PDF text or vector content.
"""
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import dataclass
from io import BytesIO
import math
import threading
import unicodedata

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .blind_watermark import WaterMark


DEFAULT_DPI = 144
MAX_ID_BYTES = 256
MAX_BITS = MAX_ID_BYTES * 8
NOTICE = ('A decoded ID is a candidate only. Matching page readings do not '
          'authenticate the document or prove that a watermark exists.')
# PDFium is not thread-safe, even for different documents. Serialize the entire
# operation, also bounding the watermark working set to one PDF per process.
_PDF_LOCK = threading.Lock()


class PdfError(ValueError):
    """Invalid or unsupported PDF input or watermark parameters."""


class PdfLimitError(PdfError):
    """The operation exceeds a configured resource budget."""


class PdfBusyError(PdfError):
    """Another PDF operation is using this process."""


@dataclass(frozen=True)
class PdfLimits:
    max_pages: int = 5
    max_page_pixels: int = 4_000_000
    max_total_pixels: int = 10_000_000
    max_output_bytes: int = 4_400_000


def identifier_bits(text):
    """Validate a single-line ID and keep compatibility with WaterMark(str)."""
    if (not text or not text.strip() or
            any(unicodedata.category(c).startswith('C') or
                unicodedata.category(c) in ('Zl', 'Zp') for c in text)):
        raise PdfError('Document ID must be nonempty, single-line text without control characters.')
    encoded = text.encode('utf-8')
    if len(encoded) > MAX_ID_BYTES:
        raise PdfError('Document ID must be at most {} UTF-8 bytes.'.format(MAX_ID_BYTES))
    return int.from_bytes(encoded, 'big').bit_length()


def _validate_parameters(password, dpi, bits):
    if not isinstance(password, int) or not 0 <= password <= 2 ** 32 - 1:
        raise PdfError('Password must be a whole number between 0 and 4294967295.')
    if not isinstance(dpi, int) or not 72 <= dpi <= 300:
        raise PdfError('DPI must be a whole number between 72 and 300.')
    if not isinstance(bits, int) or not 1 <= bits <= MAX_BITS:
        raise PdfError('Bit length must be between 1 and {}.'.format(MAX_BITS))


@contextmanager
def _document(data, dpi, bits, limits):
    if not data or not data.lstrip().startswith(b'%PDF-'):
        raise PdfError('Upload a PDF file with a valid PDF header.')
    if not _PDF_LOCK.acquire(blocking=False):
        raise PdfBusyError('Another PDF is being processed. Try again shortly.')
    doc = None
    try:
        try:
            doc = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise PdfError('Could not open PDF. Malformed or password-protected PDFs are unsupported.') from exc
        if pdfium.raw.FPDF_GetSecurityHandlerRevision(doc) != -1:
            raise PdfError('Encrypted PDFs are unsupported. Upload an unencrypted copy.')
        if not 1 <= len(doc) <= limits.max_pages:
            raise PdfLimitError('PDF must have between 1 and {} pages.'.format(limits.max_pages))
        # Preflight ALL pages before rendering any, including extraction. Never
        # silently lower DPI: extraction depends on exactly the same pixel grid.
        sizes = []
        total = 0
        for index in range(len(doc)):
            with closing(doc[index]) as page:
                width, height = page.get_size()
            if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                raise PdfError('Page {} has invalid dimensions.'.format(index + 1))
            w, h = math.ceil(width * dpi / 72), math.ceil(height * dpi / 72)
            pixels = w * h
            if pixels > limits.max_page_pixels:
                raise PdfLimitError('Page {} exceeds the {:.1f} MP page limit at {} DPI.'.format(
                    index + 1, limits.max_page_pixels / 1e6, dpi))
            total += pixels
            if total > limits.max_total_pixels:
                raise PdfLimitError('PDF exceeds the {:.1f} MP total rendering limit at {} DPI.'.format(
                    limits.max_total_pixels / 1e6, dpi))
            if bits >= ((w + 1) // 2 // 4) * ((h + 1) // 2 // 4):
                raise PdfError('Page {} is too small for this bit length at {} DPI.'.format(index + 1, dpi))
            sizes.append((width, height))
        doc.init_forms()
        yield doc, sizes
    except pdfium.PdfiumError as exc:
        raise PdfError('Could not read or render this PDF.') from exc
    finally:
        try:
            if doc is not None:
                doc.close()
        finally:
            _PDF_LOCK.release()


def _render_page(doc, index, dpi):
    with closing(doc[index]) as page:
        with closing(page.render(scale=dpi / 72, draw_annots=True)) as bitmap:
            with bitmap.to_pil().convert('RGB') as rgb:
                return cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)


def _embed_page(img, text, password):
    bwm = WaterMark(password_img=password, password_wm=password)
    bwm.read_img(img=img)
    bwm.read_wm(text, mode='str')
    return bwm.embed().astype(np.uint8)


def embed_pdf(data, document_id, password, dpi=DEFAULT_DPI, limits=None):
    """Return (PDF bytes, recipe). Every page carries the identical document ID."""
    bits = identifier_bits(document_id)
    _validate_parameters(password, dpi, bits)
    limits = limits or PdfLimits()
    with _document(data, dpi, bits, limits) as (doc, sizes):
        output = BytesIO()
        pdf = canvas.Canvas(output, pageCompression=1)
        # Do not put the document ID/password into PDF metadata.
        pdf.setTitle('Watermarked document')
        for index, size in enumerate(sizes):
            image = _render_page(doc, index, dpi)
            marked = _embed_page(image, document_id, password)
            del image
            with Image.fromarray(cv2.cvtColor(marked, cv2.COLOR_BGR2RGB)) as rgb:
                pdf.setPageSize(size)
                pdf.drawImage(ImageReader(rgb), 0, 0, width=size[0], height=size[1])
                pdf.showPage()
            del marked
        pdf.save()
        if output.tell() > limits.max_output_bytes:
            raise PdfLimitError('Watermarked PDF exceeds the {:.1f} MB response limit. '
                                'Use the CLI or a host configured for larger PDFs.'.format(
                                    limits.max_output_bytes / 1e6))
        result = output.getvalue()
        # Capacity alone does not guarantee recovery (especially on small,
        # nearly blank pages). Reopen the SAVED PDF and require the intended ID
        # from every page before reporting embedding success. Keep the same lock.
        verification = pdfium.PdfDocument(result)
        try:
            for index in range(len(sizes)):
                image = _render_page(verification, index, dpi)
                try:
                    recovered = _extract_page(image, bits, password)
                except (ValueError, AssertionError):
                    recovered = None
                finally:
                    del image
                if recovered != document_id:
                    raise PdfError('Could not recover the intended ID from saved page {}. '
                                   'Try a higher DPI or a shorter document ID.'.format(index + 1))
        finally:
            verification.close()
        return result, {'bits': bits, 'dpi': dpi, 'pages': len(sizes)}


def _extract_page(img, bits, password):
    bwm = WaterMark(password_img=password, password_wm=password)
    values = bwm.extract(embed_img=img, wm_shape=bits, mode='bit')
    value = int(''.join('1' if bit >= 0.5 else '0' for bit in values), 2)
    # Strict UTF-8 and canonical bit length avoid presenting replacement text or
    # lost leading zeroes as IDs. This is not a watermark-presence detector.
    text = value.to_bytes((bits + 7) // 8, 'big').decode('utf-8', errors='strict')
    if identifier_bits(text) != bits:
        raise PdfError('Noncanonical identifier encoding.')
    return text


def extract_pdf(data, bits, password, dpi=DEFAULT_DPI, limits=None):
    """Return ONE candidate ID, or null on disagreement/no readable candidates.

    Readable pages must agree. Failed decodes are reported, never majority-voted
    into a new payload. Diagnostics do not expose a separate watermark per image.
    """
    _validate_parameters(password, dpi, bits)
    limits = limits or PdfLimits()
    readings = Counter()
    failed_pages = []
    with _document(data, dpi, bits, limits) as (doc, sizes):
        for index in range(len(sizes)):
            image = _render_page(doc, index, dpi)
            try:
                text = _extract_page(image, bits, password)
            except (ValueError, AssertionError):
                failed_pages.append(index + 1)
            else:
                readings[text] += 1
            finally:
                del image
    document_id = next(iter(readings)) if len(readings) == 1 else None
    status = ('conflict' if len(readings) > 1 else 'unreadable' if not readings
              else 'partial' if failed_pages else 'consistent')
    return {'document_id': document_id, 'status': status, 'pages': len(sizes),
            'readable_pages': sum(readings.values()), 'failed_pages': failed_pages,
            'agreeing_pages': readings[document_id] if document_id is not None else 0,
            'distinct_readings': len(readings), 'bits': bits, 'dpi': dpi,
            'notice': NOTICE}
