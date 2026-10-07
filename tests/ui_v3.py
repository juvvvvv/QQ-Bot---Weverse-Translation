"""Isolated UI test for full bake, append, /e, correction and latest-only storage."""
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
    def seed(extra=False):
        slots=[{'key':'0','label':'正文','y':90,'text':'原文🙂','author':'YEJUN','emojis':['🙂'],'comment_id':''},
               {'key':'1','label':'艺人评论 1','y':210,'text':'第一条💙','author':'HAMIN','emojis':['💙'],'comment_id':'r1'},
               {'key':'2','label':'艺人评论 2','y':340,'text':'第二条🌱','author':'YEJUN','emojis':['🌱'],'comment_id':'r2','is_reply':True,'parent_id':'r1'}]
        if extra: slots.append({'key':'3','label':'新评论','y':470,'text':'新增✨','author':'EUNHO','emojis':['✨'],'comment_id':'r3','is_reply':True,'parent_id':'r1'})
        slots[0]['comment_counts']={'artist':{'display':str(len(slots)-1),'value':len(slots)-1,'approximate':False},'warnings':[]}
        for slot in slots:slot.update(scale=1,font_size=14,x=24,width=372,bottom_padding=16)
        path='originals/'+uuid.uuid4().hex+'.png'
        image=Image.new('RGB',(420,520 if extra else 400),'white')
        draw=ImageDraw.Draw(image)
        for i,slot in enumerate(slots):
            draw.ellipse((16,slot['y']-60,40,slot['y']-36),fill='#6b9b88')
            draw.text((48,slot['y']-60),slot['author']+' (offline fixture)',fill='black')
        image.save(store.DATA/path)
        return store.add_post(url,'离线交互验证 · 非真实 Weverse 截图','123456',path,slots,'weverse','受控模拟：验证译文输入和最新存档。')
    first=seed()
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
        await expect(page.locator('#editor .badge')).to_have_text('已完成')
        latest=store.latest_rendered(url,'123456')
        assert latest['translations']=={'0':'正文🙂','1':'根评论💙','2':'楼中楼🌱'}
        old_path=store.DATA/latest['output']
        fresh=seed(extra=True)
        await page.locator('[data-tab="archive"]').click()
        await page.locator('[data-open="'+fresh['id']+'"]').click()
        await page.locator('#translation-mode').select_option('append')
        await expect(page.locator('#translation-targets')).to_contain_text('需要 1 段')
        await page.locator('#translation-body').fill('新增评论/e')
        await page.locator('#translation-form button').click()
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
    print(json.dumps({'status':'passed','flows':['单一译文输入框','/e逐段引用','+补充模式识别新增评论','旧版本覆盖','完整模式修正错字','段数错误保留成功存档'],'console_errors':errors},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--artifact-dir',default='/tmp/plave-v3-interaction')
    args=parser.parse_args();artifacts=Path(args.artifact_dir);artifacts.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    with tempfile.TemporaryDirectory(prefix='plave-v3-ui-') as directory:
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
