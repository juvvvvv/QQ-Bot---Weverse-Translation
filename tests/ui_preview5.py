"""Isolated UI test for full bake, append, /e, correction and latest-only storage."""
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
    url='https://weverse.io/plave/artist/3-241901329'
    async def seed(extra=False):
        avatar=io.BytesIO();Image.new('RGB',(32,32),'orange').save(avatar,format='PNG')
        avatar='data:image/png;base64,'+base64.b64encode(avatar.getvalue()).decode()
        texts=['第一条💙','第二条🌱']+(['新增✨'] if extra else [])
        models=[{'body':[{'text':text}],'font':'Arial','font_size':14,'line_height':22.4,'color':'#111',
                 'name':'HAMIN' if i==0 else 'YEJUN','badge':None,'time':'09. 30. 03:13',
                 'tools':[],'avatar':avatar,'reply':i>0,'depth':int(i>0),'heading':'艺人评论' if i==0 else ''} for i,text in enumerate(texts)]
        images=await asyncio.to_thread(render_text_images,420,[{'native_card':m} for m in models])
        root=Image.new('RGB',(420,120),'white');ImageDraw.Draw(root).text((24,24),'YEJUN (offline fixture)',fill='black')
        slots=[{'key':'0','label':'正文','y':90,'text':'原文🙂','author':'YEJUN','emojis':['🙂'],'comment_id':'',
                'scale':1,'font_size':14,'x':24,'width':372,'bottom_padding':16}]
        offset=root.height
        for i,(model,image,text) in enumerate(zip(models,images,texts)):
            slots.append({'key':str(i+1),'label':f'艺人评论 {i+1}','y':offset+60,'text':text,'author':model['name'],
                          'emojis':[text[-1]],'comment_id':f'r{i+1}','source_index':len(texts)-i-1,
                          'is_reply':i>0,'parent_id':'r1' if i>0 else None,'native_card':model,
                          'fragment_top':offset,'fragment_height':image.height,'scale':1,'font_size':14,
                          'x':60,'width':340,'bottom_padding':16})
            offset+=image.height
        slots[0]['comment_counts']={'artist':{'display':str(len(models)),'value':len(models),'approximate':False},'warnings':[]}
        path='originals/'+uuid.uuid4().hex+'.png'
        image=Image.new('RGB',(420,offset),'white');image.paste(root);offset=root.height
        for fragment in images:image.paste(fragment,(0,offset));offset+=fragment.height
        image.save(store.DATA/path)
        return store.add_post(url,'离线交互验证 · 非真实 Weverse 截图','123456',path,slots,'weverse','受控原生评论模型：验证工作台生成和复用。')
    from weverse_bot.text_image import render_text_images
    first=await seed()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(executable_path=os.environ.get('WEVERSE_BROWSER_EXECUTABLE') or shutil.which('chromium'))
        page=await browser.new_page(viewport={'width':1440,'height':1080})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(base)
        await page.locator('#stats .stat').first.wait_for()
        await page.locator('[data-tab="archive"]').click()
        await page.locator('[data-open="'+first['id']+'"]').click()
        await page.locator('#translation-body').wait_for()
        assert await page.locator('#translation-form textarea').count()==1
        await page.locator('#translation-body').fill('正文/e\n+\n根评论/e\n+\n楼中楼/e')
        await page.locator('#translation-form button').click()
        await expect(page.locator('#busy')).to_be_hidden(timeout=60000)
        await expect(page.locator('#editor .badge')).to_have_text('已完成')
        latest=store.latest_rendered(url,'123456')
        with Image.open(store.DATA/latest['output']) as image:
            assert image.width==420 and image.height>Image.open(store.DATA/latest['original']).height
        async with page.expect_download() as download:
            await page.locator('#preview-footer a[download]').click()
        await (await download.value).save_as(str(artifacts/'native-comments-translated.png'))
        assert latest['translations']=={'0':'正文🙂','1':'根评论💙','2':'楼中楼🌱'}
        old_path=store.DATA/latest['output']
        fresh=await seed(extra=True)
        await page.locator('[data-tab="archive"]').click()
        await page.locator('[data-open="'+fresh['id']+'"]').click()
        await page.locator('#translation-mode').select_option('append')
        await expect(page.locator('#translation-targets')).to_contain_text('需要 1 段')
        await page.locator('#translation-body').fill('新增评论/e')
        await page.locator('#translation-form button').click()
        await expect(page.locator('#busy')).to_be_hidden(timeout=60000)
        await expect(page.locator('#editor .badge')).to_have_text('已完成')
        latest=store.latest_rendered(url,'123456')
        assert latest['translations']['3']=='新增评论✨'
        assert not old_path.exists()
        assert len(store.posts(group='123456'))==1
        old_path=store.DATA/latest['output']
        await page.locator('#translation-body').fill((await page.locator('#translation-body').input_value()).replace('正文','正文订正',1))
        await page.locator('#translation-form button').click()
        await expect(page.locator('#busy')).to_be_hidden()
        assert store.latest_rendered(url,'123456')['translations']['0']=='正文订正🙂'
        assert not old_path.exists()
        await page.screenshot(path=str(artifacts/'统一输入与最新存档.png'),full_page=True)
        await page.locator('#translation-body').fill('段数错误')
        await page.locator('#translation-form button').click()
        await expect(page.locator('#toast')).to_contain_text('需要 4 段')
        assert store.latest_rendered(url,'123456')['translations']['0']=='正文订正🙂'
        assert not errors,errors
        await browser.close()
    print(json.dumps({'status':'passed','flows':['原生评论卡片完整烤制和下载','原生楼中楼补充、重烤、最终水印','单一译文输入框','/e逐段引用','+补充模式识别新增评论','旧版本覆盖','完整模式修正错字','段数错误保留成功存档'],'console_errors':errors},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--artifact-dir',default='/tmp/plave-preview5-interaction')
    args=parser.parse_args();artifacts=Path(args.artifact_dir);artifacts.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    with tempfile.TemporaryDirectory(prefix='plave-preview5-ui-') as directory:
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
