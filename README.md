


# blind-watermark

Blind watermark based on DWT-DCT-SVD.


[![PyPI](https://img.shields.io/pypi/v/blind_watermark)](https://pypi.org/project/blind_watermark/)
[![Build Status](https://travis-ci.com/guofei9987/blind_watermark.svg?branch=master)](https://travis-ci.com/guofei9987/blind_watermark)
[![codecov](https://codecov.io/gh/guofei9987/blind_watermark/branch/master/graph/badge.svg)](https://codecov.io/gh/guofei9987/blind_watermark)
[![License](https://img.shields.io/pypi/l/blind_watermark.svg)](https://github.com/guofei9987/blind_watermark/blob/master/LICENSE)
![Python](https://img.shields.io/badge/python->=3.5-green.svg)
![Platform](https://img.shields.io/badge/platform-windows%20|%20linux%20|%20macos-green.svg)
[![stars](https://img.shields.io/github/stars/guofei9987/blind_watermark.svg?style=social)](https://github.com/guofei9987/blind_watermark/)
[![fork](https://img.shields.io/github/forks/guofei9987/blind_watermark?style=social)](https://github.com/guofei9987/blind_watermark/fork)
[![Downloads](https://pepy.tech/badge/blind-watermark)](https://pepy.tech/project/blind-watermark)
[![Discussions](https://img.shields.io/badge/discussions-green.svg)](https://github.com/guofei9987/blind_watermark/discussions)
<a href="https://hellogithub.com/repository/guofei9987/blind_watermark" target="_blank"><img src="https://abroad.hellogithub.com/v1/widgets/recommend.svg?rid=3834302ff46a40f188a651ef8bd26ff5&claim_uid=se0WHo8cbiLv2w1&theme=small" alt="Featured｜HelloGitHub" /></a>

- **Documentation:** [https://BlindWatermark.github.io/blind_watermark/#/en/](https://BlindWatermark.github.io/blind_watermark/#/en/)
- **文档：** [https://BlindWatermark.github.io/blind_watermark/#/zh/](https://BlindWatermark.github.io/blind_watermark/#/zh/)  
- **中文 readme** [README_cn.md](README_cn.md)
- **Source code:** [https://github.com/guofei9987/blind_watermark](https://github.com/guofei9987/blind_watermark)



# install
```bash
pip install blind-watermark
```

For the current developer version:
```bach
git clone git@github.com:guofei9987/blind_watermark.git
cd blind_watermark
pip install .
```

# How to use

## PDF support in the web app and CLI

A PDF has **one document identifier**. The same payload is embedded in each
complete rendered page, including its text and graphics. Extraction returns one
candidate `document_id` plus page agreement diagnostics, not a watermark for
every embedded image. The DWT/DCT/SVD image algorithm is unchanged.

PDF output is **rasterized**. Selectable text, vector objects, links, forms,
accessibility structure and digital signatures are not preserved. Page sizes and
orientation are retained, but flat colors and white areas may show watermark
texture, and files can become much larger. Print/scan, cropping and recompression
survival have not been tested. Keep the original PDF.

Install and run from the repository root with Python 3.10 or newer:

```bash
pip install -r requirements.txt -r examples/requirements-pdf.txt
pip install uvicorn
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, choose an image or PDF, and use Embed or Extract.
For PDFs, enter one single-line document ID (up to 256 UTF-8 bytes) and a numeric
password. Save the returned **bit length**, **DPI** and **password**. Downloadable
extraction settings contain the bit length and DPI; the password is kept separate.
The "Try extracting" button fills these settings and uses the downloaded PDF.
The existing image endpoints and payloads remain compatible.

PDF embedding reopens the saved output and checks that **every page recovers the
intended ID at the selected DPI** before returning success. Nominal bit capacity
alone is insufficient, particularly for small or nearly blank pages; these can
fail verification. Try a higher DPI within the rendering limits or a shorter ID.
This check is not an authenticity or future robustness guarantee.

### Reproduce the demo or use the CLI

```bash
python -m examples.pdf_watermark demo
python -m examples.pdf_watermark embed input.pdf watermarked.pdf --text DOC-123 --password 1234
python -m examples.pdf_watermark extract watermarked.pdf --bits 55 --dpi 144 --password 1234
```

The demo creates `output/pdf/original.pdf`, `watermarked.pdf` and
`verification.json`: three pages carrying `DOC-2026-001`, password 1234, 144 DPI,
95 bits. It exits nonzero if recovery fails. `DOC-123` uses 55 bits with the
existing string encoding; always use the bit length returned by embedding.
The ID is not stored as PDF metadata. Encrypted PDFs are rejected, including PDFs
that open with an empty user password; use an unencrypted source copy.

The CLI shares the web implementation, defaults to a 50-page/100 MB output
budget, and accepts `--max-pages` and `--max-output-mb`. Its per-page pixel limit
is still 4 MP. For package-only use, `pip install '.[pdf]'` installs PDF extras;
import `embed_pdf` and `extract_pdf` from `blind_watermark.pdf`.

### Document extraction results

| Status | `document_id` | Meaning |
| --- | --- | --- |
| `consistent` | One candidate ID | Every page decoded to the same ID; includes one-page PDFs. |
| `partial` | One candidate ID | Readable pages agree, but `failed_pages` lists undecodable pages. |
| `conflict` | `null` | Readable pages contain different IDs; no majority vote. |
| `unreadable` | `null` | No page decoded as a valid identifier. |

Strict UTF-8 decoding filters some invalid readings. It is **not** a presence
detector: a wrong password, wrong bit length/DPI or an unwatermarked PDF may
still produce plausible text. Matching readings do not authenticate a document,
prove a watermark exists, or prove that pages have not been inserted or removed.
An isolated saved page can recover the same document ID when the pixel grid is
unchanged. Save/extract with the same password, DPI and bit length.

PDF API requests use multipart form data:

```bash
curl -D headers.txt -o watermarked.pdf -F pdf=@input.pdf -F text=DOC-123 -F password=1234 -F dpi=144 http://127.0.0.1:8000/api/pdf/embed/
curl -F pdf=@watermarked.pdf -F bits=55 -F password=1234 -F dpi=144 http://127.0.0.1:8000/api/pdf/extract/
```

Embedding returns `application/pdf` with `X-Watermark-Bits`, `X-Watermark-DPI`
and `X-Document-Pages` headers. Extraction returns a JSON object containing
`document_id`, `status`, `pages`, `readable_pages`, `agreeing_pages`,
`failed_pages` (1-based page numbers), `distinct_readings`, `bits`, `dpi`, and a
notice. Malformed/encrypted PDFs and verification failures return HTTP 400;
resource limits return 413; invalid form parameters return 422. One PDF operation
is allowed per process, including rendering and verification, because PDFium is
not thread-safe; a concurrent PDF request gets 429 with `Retry-After: 2`.
Image requests continue to use `/api/embed/` and `/api/extract/` with `image`.

### Web limits and larger PDFs

Defaults keep a small synchronous PDF workflow within the existing hosting
budgets. Limits apply at the selected DPI; the app never silently changes DPI,
resizes PDF pages, or falls back to lossy JPEG compression for PDFs.

| Environment variable | Default | Purpose |
| --- | ---: | --- |
| `BWM_MAX_UPLOAD_BYTES` | 4300000 | Uploaded file size; leaves room for multipart fields. |
| `BWM_MAX_RESPONSE_BYTES` | 4400000 | Response size budget. |
| `BWM_PDF_MAX_PAGES` | 5 | Maximum pages per web request. |
| `BWM_PDF_MAX_PAGE_PIXELS` | 4000000 | Maximum rendered pixels on any page. |
| `BWM_PDF_MAX_TOTAL_PIXELS` | 10000000 | Total rendered pixels across the document. |

The effective PDF output budget is the smaller of upload and response budgets,
so downloaded PDFs can be uploaded again for extraction. All page dimensions,
pixel budgets and payload capacities are checked before rendering. Pages are
watermarked sequentially; the PDF writer still buffers compressed page content.
The frontend reads active limits from `/api/config/`.

On Vercel, increasing these environment variables does **not** bypass platform
body limits or the configured 60-second function duration. Pixel/page limits
bound work but cannot guarantee a rendering time for arbitrary PDFs. For larger
documents use the CLI, or configure a longer-lived host and its reverse-proxy
request limits. Large-scale browser support would need queued jobs and external
file storage/downloads. No persistent server-side output storage is added here;
multipart uploads may use framework-managed temporary files during a request.

### Tests

```bash
pip install -r requirements.txt -r tests/requirements.txt
python -m pytest tests -q
```

Install Poppler (`poppler-utils` on Ubuntu) for the cross-renderer test; otherwise
that test is skipped. Tests cover multipage API round trips, isolated-page
recovery, Poppler rendering, conflicting IDs, partial/unreadable diagnostics,
UTF-8 payloads, saved-output verification failures, resource budgets, malformed
and encrypted inputs, wrong settings, and the existing image API.

An optional browser check starts its own server and exercises PDF upload,
download, extraction settings, "Try extracting", invalid input, the image
workflow, and mobile width:

```bash
pip install playwright uvicorn
python -m playwright install chromium
python tests/browser_smoke.py
```


## Use in bash


```bash
# embed watermark into image:
blind_watermark --embed --pwd 1234 examples/pic/ori_img.jpeg "watermark text" examples/output/embedded.png
# extract watermark from image:
blind_watermark --extract --pwd 1234 --wm_shape 111 examples/output/embedded.png
```



## Use in Python

Original Image + Watermark = Watermarked Image

![origin_image](docs/原图.jpeg) + '@guofei9987 开源万岁！' = ![打上水印的图](docs/打上水印的图.jpg)


See the [codes](/examples/example_str.py)

Embed watermark:
```python
from blind_watermark import WaterMark

bwm1 = WaterMark(password_img=1, password_wm=1)
bwm1.read_img('pic/ori_img.jpg')
wm = '@guofei9987 开源万岁！'
bwm1.read_wm(wm, mode='str')
bwm1.embed('output/embedded.png')
len_wm = len(bwm1.wm_bit)
print('Put down the length of wm_bit {len_wm}'.format(len_wm=len_wm))
```

Extract watermark:
```python
bwm1 = WaterMark(password_img=1, password_wm=1)
wm_extract = bwm1.extract('output/embedded.png', wm_shape=len_wm, mode='str')
print(wm_extract)
```
Output:
>@guofei9987 开源万岁！

### attacks on Watermarked Image


|attack method|image after attack|extracted watermark|
|--|--|--|
|Rotate 45 Degrees|![旋转攻击](docs/旋转攻击.jpg)|'@guofei9987 开源万岁！'|
|Random crop|![截屏攻击](docs/截屏攻击2_还原.jpg)|'@guofei9987 开源万岁！'|
|Masks| ![多遮挡攻击](docs/多遮挡攻击.jpg) |'@guofei9987 开源万岁！'|
|Vertical cut|![横向裁剪攻击](docs/横向裁剪攻击_填补.jpg)|'@guofei9987 开源万岁！'|
|Horizontal cut|![纵向裁剪攻击](docs/纵向裁剪攻击_填补.jpg)|'@guofei9987 开源万岁！'|
|Resize|![缩放攻击](docs/缩放攻击.jpg)|'@guofei9987 开源万岁！'|
|Pepper Noise|![椒盐攻击](docs/椒盐攻击.jpg)|'@guofei9987 开源万岁！'|
|Brightness 10% Down|![亮度攻击](docs/亮度攻击.jpg)|'@guofei9987 开源万岁！'|






### embed images

embed watermark:
```python
from blind_watermark import WaterMark

bwm1 = WaterMark(password_wm=1, password_img=1)
# read original image
bwm1.read_img('pic/ori_img.jpg')
# read watermark
bwm1.read_wm('pic/watermark.png')
# embed
bwm1.embed('output/embedded.png')
```


Extract watermark:
```python
bwm1 = WaterMark(password_wm=1, password_img=1)
# notice that wm_shape is necessary
bwm1.extract(filename='output/embedded.png', wm_shape=(128, 128), out_wm_name='output/extracted.png', )
```


|attack method|image after attack|extracted watermark|
|--|--|--|
|Rotate 45 Degrees|![旋转攻击](docs/旋转攻击.jpg)|![](docs/旋转攻击_提取水印.png)|
|Random crop|![截屏攻击](docs/截屏攻击2_还原.jpg)|![多遮挡_提取水印](docs/多遮挡攻击_提取水印.png)|
|Mask| ![多遮挡攻击](docs/多遮挡攻击.jpg) |![多遮挡_提取水印](docs/多遮挡攻击_提取水印.png)|


### embed array of bits

See it [here](/examples/example_bit.py)


As demo, we embed 6 bytes data:
```python
wm = [True, False, True, True, True, False]
```

Embed:
```python
from blind_watermark import WaterMark

bwm1 = WaterMark(password_img=1, password_wm=1)
bwm1.read_ori_img('pic/ori_img.jpg')
bwm1.read_wm([True, False, True, True, True, False], mode='bit')
bwm1.embed('output/embedded.png')
```

Extract:
```python
bwm1 = WaterMark(password_img=1, password_wm=1, wm_shape=6)
wm_extract = bwm1.extract('output/打上水印的图.png', mode='bit')
print(wm_extract)
```
Notice that `wm_shape` (shape of watermark) is necessary

The output `wm_extract` is an array of float. set a threshold such as 0.5.


# Concurrency

```python
WaterMark(..., processes=None)
```
- `processes` number of processes, can be integer. Default `None`, which means using all processes.  


# Web app (Vercel)

`app.py` is a FastAPI app that serves a browser UI (`web/`) plus `POST /api/embed` and `POST /api/extract`.
Vercel deploys it with zero configuration: import the repo as a new project and deploy. To run it locally:

```bash
pip install -r requirements.txt uvicorn
uvicorn app:app --reload
```

The single password seeds both `password_img` and `password_wm`, so to extract in Python use
`WaterMark(password_img=p, password_wm=p)`. Vercel limits request and response bodies to 4.5 MB, so the page
re-encodes larger uploads as JPEG, and the API returns JPEG (quality 95) when the lossless PNG would not fit.
Images are capped at 12.5 megapixels to stay within the function's 2 GB of memory.

## Related Project

- text_blind_watermark (Embed message into text): [https://github.com/guofei9987/text_blind_watermark](https://github.com/guofei9987/text_blind_watermark)  
- HideInfo（hide as image, hide as sounds, hide as text）：[https://github.com/guofei9987/HideInfo](https://github.com/guofei9987/HideInfo)
