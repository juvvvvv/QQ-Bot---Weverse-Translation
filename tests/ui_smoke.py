import asyncio, json, pathlib, argparse, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from playwright.async_api import async_playwright, expect

async def main():
 async with async_playwright() as pw:
  browser=await pw.chromium.launch(executable_path=os.environ.get('WEVERSE_BROWSER_EXECUTABLE') or shutil.which('chromium'),headless=True)
  page=await browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(BASE_URL+'/')
  await page.locator('#stats .stat').first.wait_for()
  await page.screenshot(path=str(ARTIFACT_DIR / '工作台-初始界面.png'),full_page=True)
  await page.locator('#demo').click()
  await page.locator('[data-translation-key="0"]').wait_for()
  await page.locator('[data-translation-key="0"]').fill('今天也谢谢大家的陪伴。\n希望你们度过幸福的一天。')
  await page.locator('[data-translation-key="1"]').fill('明天再见！')
  await page.locator('#translation-form button[type=submit]').click()
  await expect(page.locator('#editor .badge')).to_have_text('已完成')
  await page.locator('#preview-footer a[download]').wait_for()
  await page.evaluate('window.scrollTo(0,0)')
  await page.screenshot(path=str(ARTIFACT_DIR / '工作台-翻译成图.png'),full_page=True)
  async with page.expect_download() as download:
   await page.locator('#preview-footer a[download]').click()
  await (await download.value).save_as(str(ARTIFACT_DIR / '示例翻译成图.png'))
  await page.locator('[data-tab="archive"]').click()
  await page.locator('.archive-item').first.wait_for()
  assert await page.locator('.archive-item .badge').first.inner_text()=='已完成'
  await page.locator('[data-open]').first.click()
  await page.locator('#show-original').click()
  await expect(page.locator('#busy')).to_be_hidden()
  assert await page.locator('#preview img').evaluate('(el)=>el.naturalHeight')==900
  await page.locator('[data-tab="settings"]').click()
  await page.locator('[name="owner_qq"]').fill('111111')
  await page.locator('[name="groups"]').fill('123456')
  await page.locator('#settings-form button[type=submit]').click()
  await expect(page.locator('#busy')).to_be_hidden()
  await page.locator('[data-tab="members"]').click()
  await page.locator('#member-form [name="group_id"]').select_option('123456')
  await page.locator('#member-form [name="user_id"]').fill('222222')
  await page.locator('#member-form [name="level"]').select_option('3')
  await page.locator('#member-form button[type=submit]').click()
  await page.locator('[data-revoke="222222"]').wait_for()
  assert 'level 3' in await page.locator('.member-row').inner_text()
  await page.locator('[data-revoke="222222"]').click()
  await expect(page.locator('#member-count')).to_have_text('0 人 / 群组合')
  await page.locator('[data-tab="settings"]').click()
  await page.locator('#watermark-group').select_option('123456')
  await expect(page.locator('#logo-preview')).to_contain_text('尚未上传')
  import io
  from PIL import Image
  logo=io.BytesIO();Image.new('RGBA',(80,40),(180,40,80,150)).save(logo,format='PNG')
  await page.locator('#logo-file').set_input_files({'name':'test-logo.png','mimeType':'image/png','buffer':logo.getvalue()})
  await page.locator('#upload-logo').click()
  await page.locator('#logo-preview img').wait_for()
  await expect(page.locator('#busy')).to_be_hidden()
  await page.locator('#logo-opacity').fill('55')
  await page.locator('#save-logo').click()
  await expect(page.locator('#busy')).to_be_hidden()
  await expect(page.locator('#commands-help')).to_contain_text('烤制 帖子链接')
  await page.locator('[data-tab="work"]').click()
  await page.locator('[data-source="upload"]').click()
  await page.locator('#file').set_input_files(str(ROOT / 'static/demo.png'))
  await page.locator('#upload-preview').wait_for()
  await page.locator('#upload-preview').evaluate('(el) => el.decode()')
  image=await page.locator('#upload-preview').bounding_box()
  await page.locator('#upload-preview').click(position={'x':image['width']/2,'y':image['height']*292/900})
  assert abs(int(await page.locator('[data-position-y="0"]').input_value())-292)<=2
  await page.locator('#add-position').click()
  image=await page.locator('#upload-preview').bounding_box()
  await page.locator('#upload-preview').click(position={'x':image['width']/2,'y':image['height']*735/900})
  assert abs(int(await page.locator('[data-position-y="1"]').input_value())-735)<=2
  await page.locator('#upload-title').fill('手动选点界面测试')
  await page.locator('#upload-form [name="group_id"]').select_option('123456')
  await page.locator('#upload-form button[type=submit]').click()
  await expect(page.locator('#editor .editor-title')).to_have_text('手动选点界面测试')
  await page.locator('[data-translation-key="0"]').fill('只翻译正文，评论暂留空。')
  await page.locator('#translation-form button[type=submit]').click()
  await expect(page.locator('#editor .badge')).to_have_text('部分翻译')
  await page.reload()
  await page.locator('#stats .stat').first.wait_for()
  await page.locator('[data-tab="archive"]').click()
  await page.locator('.archive-item').first.wait_for()
  assert await page.locator('.archive-item').count()==2
  page.on('dialog',lambda dialog:dialog.accept())
  await page.locator('#clear-group').select_option('123456')
  await page.locator('#clear-warehouse').click()
  await expect(page.locator('#busy')).to_be_hidden()
  await expect(page.locator('.archive-item')).to_have_count(1)
  await page.locator('#clear-group').select_option('')
  await page.locator('#clear-warehouse').click()
  await expect(page.locator('#busy')).to_be_hidden()
  await expect(page.locator('.archive-item')).to_have_count(0)
  await page.set_viewport_size({'width':390,'height':844})
  await page.locator('[data-tab="work"]').click()
  await page.screenshot(path=str(ARTIFACT_DIR / '工作台-窄屏.png'),full_page=True)
  overflow=await page.evaluate('document.documentElement.scrollWidth > innerWidth')
  assert not overflow, '窄屏横向溢出'
  await page.goto(BASE_URL+'/manual')
  assert await page.locator('h1').count()==1
  assert await page.locator('section').count()==13
  assert not errors, errors
  print(json.dumps({'status':'passed','flows':['演示截图→两段译文→完成→PNG下载','查看原图','保存设置','level3授权→取消','上传→原图点选两个位置→部分翻译','刷新后档案保留','390px窄屏无横向溢出','HTML说明书13节','PNG上传与调整','按群永久清空且保留其他仓库'],'console_errors':errors},ensure_ascii=False))
  await browser.close()

if __name__=='__main__':
 parser=argparse.ArgumentParser(description='Isolated browser UI smoke test; never uses real QQ/Weverse accounts')
 parser.add_argument('--artifact-dir',default='/tmp/plave-ui-artifacts')
 args=parser.parse_args()
 ROOT=pathlib.Path(__file__).resolve().parent.parent
 ARTIFACT_DIR=pathlib.Path(args.artifact_dir).resolve()
 ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 BASE_URL=f'http://127.0.0.1:{port}'
 with tempfile.TemporaryDirectory(prefix='plave-ui-') as data_dir:
  proc=subprocess.Popen([sys.executable,str(ROOT/'run.py'),'--port',str(port),'--data-dir',data_dir],cwd=ROOT)
  try:
   for _ in range(100):
    if proc.poll() is not None: raise RuntimeError('Local server failed to start')
    try:
     with urllib.request.urlopen(BASE_URL+'/',timeout=1) as response:
      if response.status==200: break
    except Exception: time.sleep(.1)
   else: raise RuntimeError('Local server did not become ready')
   asyncio.run(main())
  finally:
   proc.terminate()
   try: proc.wait(timeout=10)
   except subprocess.TimeoutExpired:
    proc.kill();proc.wait()
