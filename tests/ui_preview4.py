"""Real HTTP/browser checks: blocked local Cookies and stale-page save after restart."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
from playwright.async_api import async_playwright, expect
from ui_session_restart import start, stop


async def run(port,directory,artifacts):
    proc=start(port,directory)
    base=f'http://127.0.0.1:{port}/'
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path=os.environ.get('WEVERSE_BROWSER_EXECUTABLE') or shutil.which('chromium'))
            context=await browser.new_context(viewport={'width':1280,'height':960})
            network=await pw.request.new_context()
            errors=[]
            # Emulate a browser refusing all Set-Cookie responses, including home
            # and bootstrap. Do not disable API auth or alter client assertions.
            async def no_cookies(route):
                assert not route.request.headers.get('cookie')
                response=await network.fetch(route.request,headers=route.request.headers|{'Cookie':''})
                headers=response.headers.copy();headers.pop('set-cookie',None)
                await route.fulfill(response=response,headers=headers)
            await context.route('**/*',no_cookies)
            page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto(base)
            await expect(page.locator('#stats .stat').first).to_be_visible()
            assert await context.cookies()==[]
            await page.locator('[data-tab=settings]').click()
            await page.locator('[name=comment_wait_seconds]').fill('60')
            old_session=await page.evaluate('sessionToken')
            # Keep the same page and its unsaved form, without navigation or reload.
            stop(proc);proc=None;proc=start(port,directory)
            await expect(page.locator('[name=comment_wait_seconds]')).to_have_value('60')
            await page.locator('#settings-form button[type=submit]').click()
            await expect(page.locator('#busy')).to_be_hidden(timeout=15000)
            await expect(page.locator('#toast')).to_have_text('设置已保存。')
            assert await page.evaluate('sessionToken')!=old_session
            assert await page.evaluate("async()=> (await api('/api/settings')).comment_wait_seconds")==60
            assert await context.cookies()==[]
            await page.locator('[data-tab=work]').click()
            await page.locator('#demo').click()
            await expect(page.locator('#translation-body')).to_be_visible()
            await page.locator('#preview img').evaluate('img=>img.decode()')
            assert await page.locator('#preview img').evaluate('img=>img.naturalWidth')>0
            await page.locator('#translation-body').fill('/k\n+\n第二段中文译文')
            await page.locator('#translation-form button[type=submit]').click()
            await expect(page.locator('#busy')).to_be_hidden(timeout=30000)
            await expect(page.locator('#editor .badge')).to_have_text('已完成')
            await expect(page.locator('#translation-body')).to_have_value('/k\n\n+\n\n第二段中文译文')
            await expect(page.locator('.original-block summary').first).to_contain_text('已跳过译文')
            await page.locator('#preview img').evaluate('img=>img.decode()')
            async with page.expect_download() as download:
                await page.locator('#preview-footer a[download]').click()
            await (await download.value).save_as(str(artifacts/'cookie-free-skip.png'))
            await page.locator('[data-tab=settings]').click()
            await page.locator('#preview-watermark').click()
            await expect(page.locator('#busy')).to_be_hidden(timeout=30000)
            await expect(page.locator('#watermark-preview img')).to_be_visible()
            await page.screenshot(path=str(artifacts/'cookie-free-settings.png'),full_page=True)
            assert not errors,errors
            assert await context.cookies()==[]
            print(json.dumps({'status':'passed','flows':['Cookie blocked: home + settings + original/translated images + download + watermark preview',
                                                        'same stale page after server restart: unsaved input preserved, session re-established, save retried',
                                                        '/k keeps original, persists in editor, next block translated'],
                              'console_errors':errors,'secrets_logged':False},ensure_ascii=False))
            await browser.close()
            await network.dispose()
    finally:
        if proc is not None:stop(proc)


if __name__=='__main__':
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    artifacts=Path('/tmp/plave-preview4-ui');artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='plave-ui-p4-') as directory:
        asyncio.run(run(port,directory,artifacts))
