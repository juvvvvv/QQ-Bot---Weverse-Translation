"""Exercise the same cached browser tab against two real server processes."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parent.parent


def start(port, directory):
    proc = subprocess.Popen([sys.executable, str(ROOT/'run.py'), '--port', str(port), '--data-dir', directory],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError('server startup failed')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=.5) as response:
                if response.status == 200:
                    return proc
        except OSError:
            time.sleep(.1)
    stop(proc)
    raise RuntimeError('server readiness timed out')


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill();proc.wait()
    proc.stderr.close()


async def main(port, directory):
    proc = start(port, directory)
    base = f'http://127.0.0.1:{port}/'
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=os.environ.get('WEVERSE_BROWSER_EXECUTABLE') or shutil.which('chromium'))
            page = await browser.new_page()
            response = await page.goto(base)
            assert response.headers['cache-control'] == 'no-store'
            await page.locator('#stats .stat').first.wait_for()
            before = await page.context.cookies(base)
            old_cookie = next(c['value'] for c in before if c['name'] == 'wv_session')
            stop(proc);proc = None
            proc = start(port, directory)
            # Normal navigation with the identical URL, no fresh query required.
            response = await page.goto(base)
            assert response.status == 200
            assert response.headers['cache-control'] == 'no-store'
            await page.locator('#stats .stat').first.wait_for()
            after = await page.context.cookies(base)
            assert next(c['value'] for c in after if c['name'] == 'wv_session') != old_cookie
            await page.locator('[data-tab=settings]').click()
            await page.locator('[name=comment_wait_seconds]').fill('60')
            await page.locator('#settings-form button[type=submit]').click()
            await expect(page.locator('#busy')).to_be_hidden()
            await expect(page.locator('#toast')).to_have_text('设置已保存。')
            saved = await page.evaluate("async()=> (await fetch('/api/settings')).json()")
            assert saved['comment_wait_seconds'] == 60
            await browser.close()
            print(json.dumps({'status': 'passed', 'flows': ['same browser + same home URL after server restart',
                                                          'fresh session cookie', 'save settings succeeds'],
                              'secrets_logged': False}))
    finally:
        if proc is not None:
            stop(proc)


if __name__ == '__main__':
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0));port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='plave-ui-session-') as directory:
        asyncio.run(main(port, directory))
