# coding=utf-8
# Web API for blind_watermark, deployable on Vercel (zero-config FastAPI) or locally:
#   uvicorn app:app --reload
from typing import Annotated

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from blind_watermark import WaterMark, bw_notes

bw_notes.close()

# Vercel Functions cap request and response bodies at 4.5 MB.
MAX_BODY_BYTES = 4_400_000
# A 12 MP image peaks around 1.3 GB of memory while embedding; functions get 2 GB.
MAX_PIXELS = 12_500_000
MAX_SEED = 2 ** 32 - 1

app = FastAPI(title='Blind Watermark', docs_url='/api/docs', openapi_url='/api/openapi.json')


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


@app.post('/api/embed')
def embed(image: Annotated[UploadFile, File()],
          text: Annotated[str, Form(min_length=1, max_length=2000)],
          password: Annotated[int, Form()]):
    bwm = watermarker(password)
    data = image.file.read()
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
def extract(image: Annotated[UploadFile, File()],
            bits: Annotated[int, Form(gt=0)],
            password: Annotated[int, Form()]):
    bwm = watermarker(password)
    img = decode_image(image.file.read(), cv2.IMREAD_COLOR)
    try:
        text = bwm.extract(embed_img=img, wm_shape=bits, mode='str')
    except (AssertionError, ValueError):
        raise HTTPException(400, 'Could not extract a watermark. Check the bit length and the image.')
    return {'text': text}


# Declared after the API routes so they take priority; Vercel serves these files from its CDN.
app.mount('/', StaticFiles(directory='web', html=True), name='web')
