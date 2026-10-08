"""HTTP UI checks using captured fanpost/ordinary-only HTML fixtures."""
import base64
import io
import argparse
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
import uuid
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright, expect


async def run(base, artifacts, store):
    from weverse_bot.capture import Browser
    from test_preview6 import STYLE, FIXTURES
    store.save_settings({'post_selector':'.post','text_selector':'.text','artist_selector':'.artist',
                         'author_selector':'.artist','comment_selector':'','max_scrolls':1,
                         'capture_width':420,'capture_scale':1,'headless':True})
    store.save_watermark('',{'enabled':False})
    source=Browser()
    posts=[]
    try:
        context=await source.open()
        for zero,path in [(False,'artist/1234'),(True,'fanpost/1235')]:
            page=await context.new_page()
            await page.set_content(STYLE+(FIXTURES/'weverse-fanpost.html').read_text())
            await page.locator('snapshot-anchor').evaluate_all("nodes=>nodes.forEach(n=>{const a=document.createElement('a');for(const x of n.attributes)a.setAttribute(x.name,x.value);a.append(...n.childNodes);n.replaceWith(a)})")
            if zero:
                await page.locator('.community-fanpost-postId-_-aside').evaluate('(el,html)=>el.innerHTML=html',(FIXTURES/'weverse-ordinary-comments.html').read_text())
            posts.append(await source.extract(page,'https://weverse.io/plave/'+path,'123456',store.settings()))
            await page.close()
    finally:await source.close()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=os.environ.get('WEVERSE_BROWSER_EXECUTABLE') or shutil.which('chromium'))
        page=await browser.new_page(viewport={'width':1440,'height':1080})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(base)
        await page.locator('#stats .stat').first.wait_for()
        for post,count,body in [(posts[0],3,'粉丝正文中文\n+\n第一条评论\n+\n第二条评论/e'),(posts[1],1,'/k')]:
            await page.locator('[data-tab="archive"]').click()
            await page.locator('[data-open="'+post['id']+'"]').click()
            await page.locator('#translation-body').wait_for()
            await expect(page.locator('#translation-targets')).to_contain_text(f'需要 {count} 段')
            await expect(page.locator('#post-comment-counts')).to_contain_text('艺人评论数：'+str(count-1))
            assert await page.locator('#translation-form textarea').count()==1
            await page.locator('#translation-body').fill(body)
            await page.locator('#translation-form button').click()
            await expect(page.locator('#busy')).to_be_hidden(timeout=60000)
            await expect(page.locator('#editor .badge')).to_have_text('已完成')
            latest=store.latest_rendered(post['url'],'123456')
            with Image.open(store.DATA/latest['output']) as output, Image.open(store.DATA/latest['original']) as original:
                assert output.width==original.width==420
                if count==1:assert output.tobytes()==original.tobytes()
                else:assert output.height>original.height and latest['translations']['2']=='第二条评论❤️'
            async with page.expect_download() as download:
                await page.locator('#preview-footer a[download]').click()
            await (await download.value).save_as(str(artifacts/f'fanpost-{count}-segments.png'))
            await page.screenshot(path=str(artifacts/f'fanpost-{count}-segments-ui.png'),full_page=True)
        latest_path=store.DATA/latest['output']
        await page.locator('#translation-body').fill('正文\n+\n多余一段')
        await page.locator('#translation-form button').click()
        await expect(page.locator('#toast')).to_contain_text('需要 1 段')
        assert latest_path.exists()
        assert not errors,errors
        await browser.close()
    print(json.dumps({'status':'passed','flows':['真实样本粉丝原帖+两条艺人评论三段译文','无艺人区域的粉丝原帖一段/k','读取评论数2/0正确展示','中文在完整原文之后','成图下载','错误段数保留成功版本'],'console_errors':errors},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--artifact-dir',default='/tmp/plave-preview6-interaction')
    args=parser.parse_args();artifacts=Path(args.artifact_dir);artifacts.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    with tempfile.TemporaryDirectory(prefix='plave-preview6-ui-') as directory:
        os.environ['WEVERSE_DATA_DIR']=directory
        from weverse_bot import store
        store.save_settings({'owner_qq':'111111','groups':['123456']})
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        process=subprocess.Popen([sys.executable,str(root/'run.py'),'--port',str(port),'--data-dir',directory],cwd=root)
        try:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('server exited')
                try:
                    with urllib.request.urlopen(base,timeout=1) as response:
                        if response.status==200:break
                except Exception:time.sleep(.1)
            else:raise RuntimeError('server not ready')
            asyncio.run(run(base,artifacts,store))
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
