"""Document-level PDF behavior and regression checks for the image API."""
from dataclasses import replace
from io import BytesIO
import shutil
import subprocess

import cv2
from fastapi.testclient import TestClient
import numpy as np
from pypdf import PdfReader, PdfWriter
import pytest
from reportlab.pdfgen import canvas

import app as web
from blind_watermark import pdf as pdfwm
from examples.pdf_watermark import sample_pdf


@pytest.fixture
def client():
    return TestClient(web.app)


@pytest.fixture(scope='module')
def marked():
    original = sample_pdf()
    response = TestClient(web.app).post('/api/pdf/embed/',
        files={'pdf': ('source.pdf', original, 'application/pdf')},
        data={'text': 'DOC-2026-001', 'password': 1234, 'dpi': 144})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-watermark-bits'] == '95'
    assert response.headers['x-watermark-dpi'] == '144'
    assert response.headers['x-document-pages'] == '3'
    return original, response.content


def select_pages(data, indices):
    reader = PdfReader(BytesIO(data))
    writer = PdfWriter()
    for index in indices:
        writer.add_page(reader.pages[index])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def small_pdf(size=(180, 180)):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=size)
    pdf.drawString(20, 80, 'PDF test')
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def test_web_document_roundtrip(client, marked):
    response = client.post('/api/pdf/extract/',
        files={'pdf': ('marked.pdf', marked[1], 'application/pdf')},
        data={'bits': 95, 'password': 1234, 'dpi': 144})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    report = response.json()
    assert report['document_id'] == 'DOC-2026-001'
    assert report['status'] == 'consistent'
    assert report['agreeing_pages'] == report['pages'] == 3
    assert report['distinct_readings'] == 1
    assert report['failed_pages'] == []
    assert 'authenticate' in report['notice']
    assert 'text' not in report  # No list of image-level payloads.


def test_saved_second_page_recovers_same_id(marked):
    report = pdfwm.extract_pdf(select_pages(marked[1], [1]), 95, 1234)
    assert report['document_id'] == 'DOC-2026-001'
    assert report['pages'] == report['agreeing_pages'] == 1


def test_output_keeps_sizes_but_flattens_content(marked):
    original, output = (PdfReader(BytesIO(data)) for data in marked)
    assert len(original.pages) == len(output.pages) == 3
    for before, after in zip(original.pages, output.pages):
        assert list(before.mediabox) == list(after.mediabox)
        assert before.extract_text()
        assert not after.extract_text()
        assert len(after.images) == 1
    assert 'DOC-2026-001' not in str(output.metadata)
    assert '1234' not in str(output.metadata)


def test_poppler_reads_every_page(marked, tmp_path):
    if not shutil.which('pdftoppm'):
        pytest.skip('Poppler is optional; install poppler-utils to run this cross-renderer check.')
    source = tmp_path / 'marked.pdf'
    source.write_bytes(marked[1])
    subprocess.run(['pdftoppm', '-r', '144', '-png', str(source), str(tmp_path / 'page')],
                   check=True, capture_output=True, timeout=60)
    pages = sorted(tmp_path.glob('page-*.png'))
    assert len(pages) == 3
    for page in pages:
        assert pdfwm._extract_page(cv2.imread(str(page)), 95, 1234) == 'DOC-2026-001'


def test_conflicting_ids_return_no_document_id(marked):
    other, _ = pdfwm.embed_pdf(select_pages(marked[0], [0]), 'DOC-2026-002', 1234)
    writer = PdfWriter()
    for data in (marked[1], other):
        writer.add_page(PdfReader(BytesIO(data)).pages[0])
    mixed = BytesIO()
    writer.write(mixed)
    report = pdfwm.extract_pdf(mixed.getvalue(), 95, 1234)
    assert report['document_id'] is None
    assert report['status'] == 'conflict'
    assert report['distinct_readings'] == 2
    assert report['agreeing_pages'] == 0


def test_partial_and_unreadable_are_explicit(monkeypatch):
    readings = iter(['DOC-2026-001', None, 'DOC-2026-001'])
    def read_page(*args):
        text = next(readings)
        if text is None:
            raise UnicodeError('Unreadable page')
        return text
    monkeypatch.setattr(pdfwm, '_extract_page', read_page)
    report = pdfwm.extract_pdf(sample_pdf(), 95, 1234)
    assert report['document_id'] == 'DOC-2026-001'
    assert report['status'] == 'partial'
    assert report['agreeing_pages'] == 2
    assert report['failed_pages'] == [2]
    monkeypatch.setattr(pdfwm, '_extract_page', lambda *args: (_ for _ in ()).throw(ValueError()))
    report = pdfwm.extract_pdf(small_pdf(), 95, 1234)
    assert report['document_id'] is None
    assert report['status'] == 'unreadable'


def test_unicode_id_and_legacy_string_encoding():
    text = '文档-001'
    output, recipe = pdfwm.embed_pdf(select_pages(sample_pdf(), [0]), text, 1234)
    assert recipe['bits'] == int.from_bytes(text.encode(), 'big').bit_length()
    assert pdfwm.extract_pdf(output, recipe['bits'], 1234)['document_id'] == text


@pytest.mark.parametrize('field,value', [('bits', 0), ('bits', 2049), ('password', -1),
                                      ('password', 2 ** 32), ('dpi', 71), ('dpi', 301)])
def test_invalid_parameters(client, field, value):
    fields = {'bits': 95, 'password': 1234, 'dpi': 144, field: value}
    response = client.post('/api/pdf/extract', files={'pdf': ('test.pdf', small_pdf())}, data=fields)
    assert response.status_code == 422


@pytest.mark.parametrize('text', ['', ' ', 'ID\nSECOND', '\x00ID', 'ID\u2028SECOND', '文' * 86])
def test_invalid_document_ids(text):
    with pytest.raises(pdfwm.PdfError):
        pdfwm.embed_pdf(small_pdf(), text, 1234)


@pytest.mark.parametrize('limit', [dict(max_pages=2), dict(max_page_pixels=100),
                                  dict(max_total_pixels=100)])
def test_limits_checked_before_rendering(monkeypatch, limit):
    def unexpected_render(*args):
        pytest.fail('Must reject before rendering a bitmap')
    monkeypatch.setattr(pdfwm, '_render_page', unexpected_render)
    with pytest.raises(pdfwm.PdfLimitError):
        pdfwm.embed_pdf(sample_pdf(), 'DOC-1', 1234, limits=replace(pdfwm.PdfLimits(), **limit))


def test_tiny_page_capacity_checked_before_rendering(monkeypatch):
    monkeypatch.setattr(pdfwm, '_render_page', lambda *args: pytest.fail('Rendered tiny page'))
    with pytest.raises(pdfwm.PdfError, match='too small'):
        pdfwm.embed_pdf(small_pdf((5, 5)), 'DOC-1', 1234)


def test_unreliable_small_page_is_not_reported_as_success(client):
    response = client.post('/api/pdf/embed', files={'pdf': ('small.pdf', small_pdf())},
                           data={'text': '文档-001', 'password': 1234})
    assert response.status_code == 400
    assert 'Could not recover the intended ID from saved page 1' in response.json()['detail']


def test_output_budget_and_lock_cleanup():
    with pytest.raises(pdfwm.PdfLimitError, match='response limit'):
        pdfwm.embed_pdf(small_pdf(), 'DOC-1', 1234, limits=replace(pdfwm.PdfLimits(), max_output_bytes=100))
    # A failed operation must release the process-wide PDFium lock.
    assert pdfwm._PDF_LOCK.acquire(blocking=False)
    pdfwm._PDF_LOCK.release()


def test_busy_is_retryable(client):
    with pdfwm._PDF_LOCK:
        response = client.post('/api/pdf/embed/', files={'pdf': ('test.pdf', small_pdf())},
                               data={'text': 'DOC-1', 'password': 1234})
    assert response.status_code == 429
    assert response.headers['retry-after'] == '2'


@pytest.mark.parametrize('data', [b'', b'not a PDF', b'%PDF-1.7\ninvalid'])
def test_bad_pdf_is_a_client_error(client, data):
    response = client.post('/api/pdf/embed', files={'pdf': ('bad.pdf', data)},
                           data={'text': 'DOC-1', 'password': 1234})
    assert response.status_code == 400


@pytest.mark.parametrize('password', ['secret', ''])
def test_encrypted_pdf_rejected(client, password):
    writer = PdfWriter()
    writer.add_page(PdfReader(BytesIO(small_pdf())).pages[0])
    writer.encrypt(user_password=password, owner_password='owner')
    output = BytesIO()
    writer.write(output)
    response = client.post('/api/pdf/embed', files={'pdf': ('encrypted.pdf', output.getvalue())},
                           data={'text': 'DOC-1', 'password': 1234})
    assert response.status_code == 400
    assert any(term in response.json()['detail'].lower() for term in ('encrypted', 'password-protected'))


def test_upload_and_response_limits_are_http_errors(client, monkeypatch):
    data = small_pdf()
    monkeypatch.setattr(web, 'MAX_UPLOAD_BYTES', len(data) - 1)
    response = client.post('/api/pdf/embed', files={'pdf': ('test.pdf', data)},
                           data={'text': 'DOC-1', 'password': 1234})
    assert response.status_code == 413
    monkeypatch.setattr(web, 'MAX_UPLOAD_BYTES', len(data) + 1)
    monkeypatch.setattr(web, 'PDF_LIMITS', replace(web.PDF_LIMITS, max_output_bytes=100))
    response = client.post('/api/pdf/embed', files={'pdf': ('test.pdf', data)},
                           data={'text': 'DOC-1', 'password': 1234})
    assert response.status_code == 413


@pytest.mark.parametrize('field,value', [('password', 4321), ('bits', 94), ('dpi', 120)])
def test_wrong_extraction_settings_do_not_crash(client, marked, field, value):
    fields = {'bits': 95, 'password': 1234, 'dpi': 144, field: value}
    response = client.post('/api/pdf/extract/',
        files={'pdf': ('marked.pdf', select_pages(marked[1], [0]))}, data=fields)
    assert response.status_code == 200
    assert 'document_id' in response.json()
    # Deliberately no guarantee that arbitrary readings prove absence/presence.


def test_image_roundtrip_unchanged(client):
    image = np.random.default_rng(4).integers(0, 255, (320, 400, 3), dtype=np.uint8)
    image_bytes = cv2.imencode('.png', image)[1].tobytes()
    response = client.post('/api/embed/', files={'image': ('photo.png', image_bytes)},
                           data={'text': 'IMAGE-123', 'password': 1234})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    result = client.post('/api/extract/', files={'image': ('marked.png', response.content)},
                         data={'bits': response.headers['x-watermark-bits'], 'password': 1234})
    assert result.status_code == 200
    assert result.json() == {'text': 'IMAGE-123'}


def test_config_reflects_server_limits_and_static_page(client):
    settings = client.get('/api/config/').json()
    assert settings['max_upload_bytes'] == web.MAX_UPLOAD_BYTES
    assert settings['pdf']['max_pages'] == web.PDF_LIMITS.max_pages
    assert 'Image or PDF' in client.get('/').text
