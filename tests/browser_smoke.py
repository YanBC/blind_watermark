"""Optional browser smoke check; starts its own temporary local server.

pip install playwright uvicorn
python -m playwright install chromium
python tests/browser_smoke.py

Set BWM_BROWSER_EXECUTABLE to use an already installed Chromium executable.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.pdf_watermark import sample_pdf


def main():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    url = 'http://127.0.0.1:{}'.format(port)
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host',
                               '127.0.0.1', '--port', str(port)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                with urlopen(url + '/api/config/', timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError(server.stderr.read().decode())
                time.sleep(0.05)
        else:
            raise RuntimeError('Server did not start')
        with TemporaryDirectory() as directory, sync_playwright() as playwright:
            source = Path(directory) / 'source.pdf'
            source.write_bytes(sample_pdf())
            browser = playwright.chromium.launch(
                executable_path=os.getenv('BWM_BROWSER_EXECUTABLE') or None,
                args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1100, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/_vercel/insights/script.js', lambda route: route.fulfill(body=''))
            page.goto(url)
            embed = page.locator('#embed-form')
            embed.locator('input[type=file]').set_input_files(source)
            expect(embed.get_by_label('Document ID', exact=True)).to_be_visible()
            expect(embed.get_by_label('PDF rendering DPI')).to_have_value('144')
            embed.locator('[name=text]').fill('DOC-2026-001')
            embed.locator('[name=password]').fill('1234')
            embed.get_by_role('button', name='Embed watermark').click()
            expect(page.locator('#embed-result')).to_be_visible(timeout=60000)
            expect(page.locator('#embed-bits')).to_have_text('95')
            expect(page.locator('#embed-pages')).to_have_text('3')
            expect(page.locator('#embed-preview')).to_be_hidden()
            with page.expect_download() as download:
                page.locator('#embed-download').click()
            saved = Path(directory) / 'watermarked.pdf'
            download.value.save_as(saved)
            assert saved.read_bytes().startswith(b'%PDF-')
            with page.expect_download() as settings_download:
                page.locator('#embed-settings').click()
            settings_file = Path(directory) / 'settings.json'
            settings_download.value.save_as(settings_file)
            settings = json.loads(settings_file.read_text())
            assert settings['bits'] == 95 and settings['dpi'] == 144
            assert 'password' not in settings
            page.locator('#embed-try').click()
            extract = page.locator('#extract-form')
            expect(extract.locator('[name=bits]')).to_have_value('95')
            expect(extract.locator('[name=password]')).to_have_value('1234')
            expect(extract.locator('[name=dpi]')).to_have_value('144')
            extract.get_by_role('button', name='Extract watermark').click()
            expect(page.locator('#extract-result')).to_be_visible(timeout=60000)
            expect(page.locator('#extract-text')).to_have_text('DOC-2026-001')
            expect(page.locator('#extract-diagnostics')).to_contain_text('3 of 3 pages agree')
            expect(page.locator('#extract-notice')).to_contain_text('do not authenticate')
            # File selection clears the previous result; malformed input is an error.
            bad = Path(directory) / 'bad.pdf'
            bad.write_bytes(b'not a PDF')
            extract.locator('input[type=file]').set_input_files(bad)
            expect(page.locator('#extract-result')).to_be_hidden()
            extract.get_by_role('button', name='Extract watermark').click()
            expect(extract.locator('.status')).to_contain_text('valid PDF header')
            # Exercise the existing image path in the browser, too.
            page.locator('#tab-embed').click()
            embed.locator('input[type=file]').set_input_files(ROOT / 'examples/pic/ori_img.jpeg')
            expect(embed.locator('.pdf-options')).to_be_hidden()
            embed.locator('[name=text]').fill('IMAGE-123')
            embed.get_by_role('button', name='Embed watermark').click()
            expect(page.locator('#embed-result')).to_be_visible(timeout=60000)
            expect(page.locator('#embed-preview')).to_be_visible()
            page.locator('#embed-try').click()
            expect(extract.locator('.pdf-options')).to_be_hidden()
            extract.get_by_role('button', name='Extract watermark').click()
            expect(page.locator('#extract-text')).to_have_text('IMAGE-123', timeout=60000)
            expect(page.locator('#extract-diagnostics')).to_have_text('')
            # Mobile layout stays within its viewport.
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            browser.close()
            print('PASS: PDF upload/embed/download/settings/try-extract, error handling, '
                  'image roundtrip, mobile width; no JavaScript page errors.')
    finally:
        server.terminate()
        server.communicate(timeout=10)


if __name__ == '__main__':
    main()
