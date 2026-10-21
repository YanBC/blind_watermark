# coding=utf-8
# Web API for blind_watermark, deployable on Vercel (zero-config FastAPI) or locally:
#   uvicorn app:app --reload
from typing import Annotated
import os

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from blind_watermark import WaterMark, bw_notes
from blind_watermark.pdf import (MAX_BITS, PdfBusyError, PdfError, PdfLimitError,
                                 PdfLimits, embed_pdf, extract_pdf)

bw_notes.close()

# Vercel Functions cap request and response bodies at 4.5 MB.
MAX_BODY_BYTES = int(os.getenv('BWM_MAX_RESPONSE_BYTES', '4400000'))
MAX_UPLOAD_BYTES = int(os.getenv('BWM_MAX_UPLOAD_BYTES', '4300000'))
# A 12 MP image peaks around 1.3 GB of memory while embedding; functions get 2 GB.
MAX_PIXELS = 12_500_000
MAX_SEED = 2 ** 32 - 1
PDF_LIMITS = PdfLimits(
    max_pages=int(os.getenv('BWM_PDF_MAX_PAGES', '5')),
    max_page_pixels=int(os.getenv('BWM_PDF_MAX_PAGE_PIXELS', '4000000')),
    max_total_pixels=int(os.getenv('BWM_PDF_MAX_TOTAL_PIXELS', '10000000')),
    # A successful output must also fit a subsequent extraction upload.
    max_output_bytes=min(MAX_BODY_BYTES, MAX_UPLOAD_BYTES),
)
if min(MAX_UPLOAD_BYTES, MAX_BODY_BYTES, PDF_LIMITS.max_pages,
       PDF_LIMITS.max_page_pixels, PDF_LIMITS.max_total_pixels) <= 0:
    raise ValueError('BWM resource limits must be positive integers.')

app = FastAPI(title='Blind Watermark', docs_url='/api/docs', openapi_url='/api/openapi.json')


@app.middleware('http')
async def no_store_api(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def read_upload(upload):
    data = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'File exceeds the {:.1f} MB upload limit.'.format(MAX_UPLOAD_BYTES / 1e6))
    return data


@app.get('/api/config')
@app.get('/api/config/', include_in_schema=False)
def config():
    return {'max_upload_bytes': MAX_UPLOAD_BYTES, 'max_response_bytes': MAX_BODY_BYTES,
            'max_image_pixels': MAX_PIXELS, 'pdf': {
                'max_pages': PDF_LIMITS.max_pages,
                'max_page_pixels': PDF_LIMITS.max_page_pixels,
                'max_total_pixels': PDF_LIMITS.max_total_pixels,
                'max_output_bytes': PDF_LIMITS.max_output_bytes,
                'default_dpi': 144, 'min_dpi': 72, 'max_dpi': 300,
                'max_id_bytes': 256}}


def pdf_operation(operation, *args, **kwargs):
    try:
        return operation(*args, limits=PDF_LIMITS, **kwargs)
    except PdfBusyError as exc:
        raise HTTPException(429, str(exc), headers={'Retry-After': '2'}) from exc
    except PdfLimitError as exc:
        raise HTTPException(413, str(exc)) from exc
    except PdfError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/pdf/embed')
@app.post('/api/pdf/embed/', include_in_schema=False)
def embed_document(pdf: Annotated[UploadFile, File()],
                   text: Annotated[str, Form(min_length=1, max_length=2000)],
                   password: Annotated[int, Form(ge=0, le=MAX_SEED)],
                   dpi: Annotated[int, Form(ge=72, le=300)] = 144):
    output, recipe = pdf_operation(embed_pdf, read_upload(pdf), text, password, dpi)
    return Response(output, media_type='application/pdf', headers={
        'Content-Disposition': 'attachment; filename="watermarked.pdf"',
        'X-Watermark-Bits': str(recipe['bits']),
        'X-Watermark-DPI': str(recipe['dpi']),
        'X-Document-Pages': str(recipe['pages']),
        'Cache-Control': 'no-store',
    })


@app.post('/api/pdf/extract')
@app.post('/api/pdf/extract/', include_in_schema=False)
def extract_document(pdf: Annotated[UploadFile, File()],
                     bits: Annotated[int, Form(gt=0, le=MAX_BITS)],
                     password: Annotated[int, Form(ge=0, le=MAX_SEED)],
                     dpi: Annotated[int, Form(ge=72, le=300)] = 144):
    return pdf_operation(extract_pdf, read_upload(pdf), bits, password, dpi)


def decode_image(data: bytes, flags: int) -> np.ndarray:
    if not data:
        raise HTTPException(400, 'No image uploaded.')
    img = cv2.imdecode(np.frombuffer(data, np.uint8), flags)
    if img is None:
        raise HTTPException(400, 'Could not read the image. Use a PNG, JPEG, WebP or BMP file.')
    if img.shape[0] * img.shape[1] > MAX_PIXELS:
        raise HTTPException(413, 'Image is too large; keep it under {:.1f} megapixels.'.format(MAX_PIXELS / 1e6))
    return img


def watermarker(password: int) -> WaterMark:
    if not 0 <= password <= MAX_SEED:
        raise HTTPException(400, 'Password must be a whole number between 0 and {}.'.format(MAX_SEED))
    # password_wm is what actually scrambles the text (password_img barely affects extraction),
    # so one password seeds both. Python: WaterMark(password_img=p, password_wm=p).
    return WaterMark(password_img=password, password_wm=password)


# The trailing-slash aliases serve the page when it is proxied under a host with `trailingSlash: true`
# (projects.yanbc.info/blind-watermark/), which redirects extension-less paths to their slash form.
@app.post('/api/embed')
@app.post('/api/embed/', include_in_schema=False)
def embed(image: Annotated[UploadFile, File()],
          text: Annotated[str, Form(min_length=1, max_length=2000)],
          password: Annotated[int, Form()]):
    bwm = watermarker(password)
    data = read_upload(image)
    img = decode_image(data, cv2.IMREAD_UNCHANGED)
    if img.ndim == 3 and img.shape[2] == 4 and img[:, :, 3].min() < 255:
        # Keep transparency; the library re-attaches the alpha channel after embedding.
        if img.dtype == np.uint16:
            img = (img >> 8).astype(np.uint8)
    else:
        # IMREAD_COLOR applies EXIF orientation and normalises grayscale, 16-bit and opaque RGBA input.
        img = decode_image(data, cv2.IMREAD_COLOR)

    bwm.read_img(img=img)
    bwm.read_wm(text, mode='str')
    try:
        out = bwm.embed()
    except AssertionError:
        raise HTTPException(400, 'The text is too long for this image. Use a shorter text or a larger image.')
    out = out.astype(np.uint8)

    # Lossless PNG when it fits; otherwise JPEG 95, which the watermark survives.
    ok, buf = cv2.imencode('.png', out)
    media_type, ext = 'image/png', 'png'
    if ok and buf.size > MAX_BODY_BYTES:
        ok, buf = cv2.imencode('.jpg', out[:, :, :3], [cv2.IMWRITE_JPEG_QUALITY, 95])
        media_type, ext = 'image/jpeg', 'jpg'
    if not ok or buf.size > MAX_BODY_BYTES:
        raise HTTPException(413, 'The watermarked image is too large to return. Try a smaller image.')

    return Response(buf.tobytes(), media_type=media_type, headers={
        'X-Watermark-Bits': str(len(bwm.wm_bit)),
        'X-Image-Format': ext,
        'Cache-Control': 'no-store',
    })


@app.post('/api/extract')
@app.post('/api/extract/', include_in_schema=False)
def extract(image: Annotated[UploadFile, File()],
            bits: Annotated[int, Form(gt=0)],
            password: Annotated[int, Form()]):
    bwm = watermarker(password)
    img = decode_image(read_upload(image), cv2.IMREAD_COLOR)
    try:
        text = bwm.extract(embed_img=img, wm_shape=bits, mode='str')
    except (AssertionError, ValueError):
        raise HTTPException(400, 'Could not extract a watermark. Check the bit length and the image.')
    return {'text': text}


# Declared after the API routes so they take priority; Vercel serves these files from its CDN.
app.mount('/', StaticFiles(directory='web', html=True), name='web')
