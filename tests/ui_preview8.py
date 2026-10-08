"""HTTP editor/download checks with a long multi-photo original and artist comments."""
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
    from test_preview7 import STYLE as ARTIST_STYLE, TOOLBAR
    store.save_settings({'post_selector':'.post','text_selector':'.text','artist_selector':'.artist',
                         'author_selector':'.artist','comment_selector':'','max_scrolls':1,
                         'capture_width':420,'capture_scale':1,'headless':True,'comment_wait_seconds':6})
    store.save_watermark('',{'enabled':False})
    source=Browser()
    posts=[]
    try:
        context=await source.open()
        for zero,path in [(False,'artist/1234'),(True,'artist/1235')]:
            page=await context.new_page()
            await page.set_content(STYLE+(FIXTURES/'weverse-fanpost.html').read_text())
            await page.locator('snapshot-anchor').evaluate_all("nodes=>nodes.forEach(n=>{const a=document.createElement('a');for(const x of n.attributes)a.setAttribute(x.name,x.value);a.append(...n.childNodes);n.replaceWith(a)})")
            if zero:
                await page.set_content(ARTIST_STYLE+'<article class="post"><div class="artist">YEJUN</div><div class="community-artist-postId-_-translate"><button>查看翻译</button></div><p class="text">예쁜하루☺️</p><div class="media" role="img"></div></article>'+TOOLBAR)
            if zero:
                images=[]
                for color in ((180,20,80),(20,110,180)):
                    stream=io.BytesIO();picture=Image.new('RGB',(388,620),color)
                    ImageDraw.Draw(picture).rectangle((30,30,358,590),outline='white',width=4)
                    picture.save(stream,format='PNG');images.append('data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())
                await page.locator('.media').evaluate('(el,images)=>{el.style.height="auto";el.removeAttribute("role");for(const src of images){const img=document.createElement("img");img.src=src;img.style.cssText="display:block;width:100%;margin-bottom:8px";el.append(img)}}',images)
            posts.append(await source.extract(page,'https://weverse.io/plave/'+path,'123456',store.settings()))
            if zero:
                bar=await page.locator('[data-wvbot-owned-toolbar] .toolbar-_-container').bounding_box()
                rootbox=await page.locator('.post').bounding_box()
                with Image.open(store.DATA/posts[-1]['original']) as image:
                    assert image.height>1000
                    assert image.height>=bar['y']+bar['height']-rootbox['y']+15
                assert not await page.locator('.community-artist-postId-_-translate').is_visible()
                assert await page.locator('.post [data-wvbot-owned-toolbar] button').all_inner_texts()==['10K+','2.1K','']
            await page.close()
    finally:await source.close()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=os.environ.get('WEVERSE_BROWSER_EXECUTABLE') or shutil.which('chromium'))
        page=await browser.new_page(viewport={'width':1440,'height':1080})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(base)
        await page.locator('#stats .stat').first.wait_for()
        for post,count,body in [(posts[0],3,'粉丝正文中文\n+\n第一条评论\n+\n第二条评论/e'),(posts[1],1,'漂亮的一天/e')]:
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
                if count==1:assert output.height>original.height and latest['translations']=={'0':'漂亮的一天☺️'}
                else:assert output.height>original.height and latest['translations']['2']=='第二条评论❤️'
            async with page.expect_download() as download:
                await page.locator('#preview-footer a[download]').click()
            await (await download.value).save_as(str(artifacts/f'fanpost-{count}-segments.png'))
            await page.screenshot(path=str(artifacts/f'fanpost-{count}-segments-ui.png'),full_page=True)
        await page.locator('#translation-body').fill('/k')
        await page.locator('#translation-form button').click()
        await expect(page.locator('#busy')).to_be_hidden(timeout=60000)
        await expect(page.locator('#editor .badge')).to_have_text('已完成')
        latest=store.latest_rendered(posts[1]['url'],'123456')
        with Image.open(store.DATA/latest['output']) as output, Image.open(store.DATA/latest['original']) as original:
            assert output.size==original.size and output.tobytes()==original.tobytes()
        latest_path=store.DATA/latest['output']
        await page.locator('#translation-body').fill('正文\n+\n多余一段')
        await page.locator('#translation-form button').click()
        await expect(page.locator('#toast')).to_contain_text('需要 1 段')
        assert latest_path.exists()
        assert not errors,errors
        await browser.close()
    print(json.dumps({'status':'passed','flows':['真实样本粉丝原帖+两条艺人评论三段译文','双配图长动态一段译文和/k','读取评论数2/0正确展示','中文在完整原文之后','多图底部完整互动栏10K+/2.1K保留','查看翻译入口已移除','成图下载','错误段数保留成功版本'],'console_errors':errors},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--artifact-dir',default='/tmp/plave-preview8-interaction')
    args=parser.parse_args();artifacts=Path(args.artifact_dir);artifacts.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    with tempfile.TemporaryDirectory(prefix='plave-preview8-ui-') as directory:
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
