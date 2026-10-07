import asyncio
import base64
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

TEST_DATA = tempfile.TemporaryDirectory(prefix='plave-tests-')
os.environ['WEVERSE_DATA_DIR'] = TEST_DATA.name
from PIL import Image, ImageOps, ImageChops
from fastapi.testclient import TestClient
from weverse_bot import store
from weverse_bot.app import app
from weverse_bot.render import compose, band, render_post
from weverse_bot.capture import Browser, weverse_url
from weverse_bot.page_cleanup import reject_optional_consent, prepare_emoji_text
from weverse_bot.qq import QQClient
import websockets


def reset():
    with store.db() as c:
        for table in ('posts', 'memories', 'members', 'events', 'watermarks', 'garbage'):
            c.execute(f'DELETE FROM {table}')
        c.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(store.DEFAULTS),))


def source_image():
    im = Image.new('RGB', (320, 150))
    im.putdata([(x % 256, y % 256, (x + y) % 256) for y in range(150) for x in range(320)])
    out = store.DATA / 'originals' / 'abc123.png'
    im.save(out)
    return im, 'originals/abc123.png'


def create_post(group='123456', fingerprints=False):
    _, original = source_image()
    slots = [{'key': '0', 'label': '正文', 'y': 55, 'text': '原始正文', 'fingerprint': 'post-hash' if fingerprints else None},
             {'key': '1', 'label': '艺人评论 1', 'y': 120, 'text': '原始评论', 'fingerprint': 'comment-hash' if fingerprints else None}]
    return store.add_post('https://weverse.io/plave/artist/1234', '测试动态', group, original, slots, 'weverse')


class ImageTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_pixels_are_preserved_at_multiple_insertion_points(self):
        original, filename = source_image()
        slots = [{'key':'0','label':'正文','y':55},{'key':'1','label':'艺人评论 1','y':120}]
        text = {'0':'这是中文翻译。\n第二行。','1':'很长的评论翻译' * 50}
        output = compose(store.DATA / filename, slots, text, '')
        h1 = band(original.width, '中文翻译 · 正文', text['0'], 24).height
        h2 = band(original.width, '中文翻译 · 艺人评论 1', text['1'], 24).height
        self.assertEqual(output.crop((0,0,320,55)).tobytes(), original.crop((0,0,320,55)).tobytes())
        self.assertEqual(output.crop((0,55+h1,320,120+h1)).tobytes(), original.crop((0,55,320,120)).tobytes())
        self.assertEqual(output.crop((0,120+h1+h2,320,150+h1+h2)).tobytes(), original.crop((0,120,320,150)).tobytes())
        self.assertGreater(output.height, original.height)
        self.assertEqual(output.height, original.height + h1 + h2)
        self.assertEqual(Image.open(store.DATA / filename).tobytes(), original.tobytes())

    def test_plain_translation_has_white_background_and_complete_color_emoji(self):
        image = band(420, '不应显示在图片中的标题', '漂亮的一天🙂', 14)
        ink = ImageOps.invert(image).getbbox()
        self.assertIsNotNone(ink)
        self.assertGreater(ink[1], 0)
        self.assertLess(ink[3], image.height)
        self.assertGreaterEqual(ink[0], 16)
        self.assertLess(image.height, 50)
        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(image.getpixel((419, image.height - 1)), (255, 255, 255))
        self.assertTrue(any(r > 180 and g > 100 and b < 120 for r, g, b in image.getdata()))

    def test_long_translation_and_blank_lines_expand_height(self):
        short = band(240, '', '漂亮的一天🙂', 20)
        spaced = band(240, '', '漂亮的一天🙂\n\n第二段', 20)
        long = band(240, '', '很长的中文译文🙂' * 50, 20)
        self.assertGreater(spaced.height, short.height * 2)
        self.assertGreater(long.height, spaced.height)
        self.assertEqual(long.width, short.width)

    def test_text_watermark_is_faint_centered_and_adds_no_footer(self):
        _, filename = source_image()
        slots = [{'key': '0', 'label': '正文', 'y': 150}]
        base = compose(store.DATA / filename, slots, {'0': '漂亮的一天🙂'}, '')
        marked = compose(store.DATA / filename, slots, {'0': '漂亮的一天🙂'}, '@Plave_PixelDiary 翻译：测试')
        self.assertEqual(marked.size, base.size)
        difference = ImageChops.difference(marked, base)
        box = difference.getbbox()
        self.assertIsNotNone(box)
        self.assertGreater(box[1], marked.height / 2 - 40)
        self.assertLess(box[3], marked.height / 2 + 40)
        self.assertLessEqual(max(channel for pixel in difference.getdata() for channel in pixel), 51)

    def test_partial_completed_and_history_reuse(self):
        p = create_post(fingerprints=True)
        partial = render_post(p['id'], {'0':'正文译文'})
        self.assertEqual(partial['status'], 'partial')
        completed = render_post(p['id'], {'0':'正文译文','1':'评论译文'})
        self.assertEqual(completed['status'], 'translated')
        second = create_post(fingerprints=True)
        reused = render_post(second['id'], {}, reuse=True)
        self.assertEqual(reused['translations'], {'0':'正文译文','1':'评论译文'})
        self.assertEqual(reused['status'], 'translated')
        self.assertTrue((store.DATA / reused['output']).is_file())
        with store.db() as c:
            self.assertIsNotNone(c.execute('SELECT id FROM posts WHERE id=?',(second['id'],)).fetchone())

    def test_history_is_scoped_to_group(self):
        first = create_post(group='123456', fingerprints=True)
        render_post(first['id'], {'0':'本群译文','1':'本群评论译文'})
        other = create_post(group='234567', fingerprints=True)
        with self.assertRaises(ValueError):
            render_post(other['id'], {}, reuse=True)

    def test_qq_patch_preserves_other_translations(self):
        p = create_post()
        render_post(p['id'], {'0':'正文旧译文'})
        saved = render_post(p['id'], {'1':'新评论译文'}, merge=True)
        self.assertEqual(saved['translations'], {'0':'正文旧译文','1':'新评论译文'})
        self.assertEqual(saved['status'], 'translated')

    def test_unknown_slot_and_empty_text_rejected(self):
        p = create_post()
        with self.assertRaises(ValueError):
            render_post(p['id'], {'9':'错误位置'})
        with self.assertRaises(ValueError):
            render_post(p['id'], {})

    def test_group_isolation_and_revocation(self):
        store.save_settings({'owner_qq':'111111','groups':['123456','234567']})
        store.allow('123456','222222')
        self.assertTrue(store.authorized('123456','222222'))
        self.assertFalse(store.authorized('234567','222222'))
        store.allow('123456','222222',False)
        self.assertFalse(store.authorized('123456','222222'))
        self.assertTrue(store.authorized('123456','111111'))
        with self.assertRaises(ValueError):
            store.get_post(create_post()['id'], '234567')

    def test_url_validation(self):
        self.assertEqual(weverse_url('https://weverse.io/plave/artist/1234?secret=x'), 'https://weverse.io/plave/artist/1234')
        for url in ('http://weverse.io/plave/artist/1','https://weverse.io.evil/plave/artist/1',
                    'https://weverse.io/bts/artist/1','https://weverse.io/plave/artist','https://user@weverse.io/plave/artist/1'):
            with self.assertRaises(ValueError):
                weverse_url(url)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None)

    def setUp(self):
        reset()
        self.client.cookies.clear()
        self.client.get('/')
        self.headers={'x-wv-csrf':self.client.get('/api/session').json()['csrf']}

    def test_local_auth_csrf_and_host(self):
        with TestClient(app) as unauth:
            self.assertEqual(unauth.get('/api/posts').status_code,401)
        self.assertEqual(self.client.put('/api/settings',json={'changes':{}}).status_code,403)
        self.assertEqual(self.client.get('/api/posts',headers={'sec-fetch-site':'cross-site'}).status_code,403)
        self.assertEqual(self.client.get('/',headers={'host':'evil.example'}).status_code,400)

    def test_upload_render_download_and_archive_state(self):
        im=Image.new('RGB',(720,500),'white');buf=io.BytesIO();im.save(buf,format='PNG')
        r=self.client.post('/api/upload',headers=self.headers,files={'file':('source.png',buf.getvalue(),'image/png')},
                           data={'title':'截图测试','slots':json.dumps([{'y':120},{'y':350}])})
        self.assertEqual(r.status_code,200,r.text)
        post=r.json()
        self.assertEqual(len(post['slots']),2)
        r=self.client.post('/api/posts/'+post['id']+'/render',headers=self.headers,json={'translations':{'0':'中文\n两行','1':'评论译文'}})
        self.assertEqual(r.status_code,200,r.text)
        result=r.json()
        self.assertEqual(result['status'],'translated')
        image=self.client.get(result['output_url'])
        self.assertEqual(image.status_code,200)
        self.assertGreater(Image.open(io.BytesIO(image.content)).height,500)
        self.assertEqual(self.client.get('/api/posts?status=translated').json()[0]['id'],post['id'])
        r=self.client.delete('/api/warehouse',headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['posts'],1)
        self.assertEqual(self.client.get(result['output_url']).status_code,404)
        self.assertEqual(self.client.get('/api/posts').json(),[])

    def test_token_redaction_settings_and_missing_calibration(self):
        r=self.client.put('/api/settings',headers=self.headers,json={'changes':{'ws_token':'FAKE-TEST-TOKEN','owner_qq':'111111','groups':['123456']}})
        self.assertEqual(r.status_code,200,r.text)
        result=self.client.get('/api/settings').json()
        self.assertTrue(result['ws_token_set'])
        self.assertNotIn('ws_token',result)
        self.assertNotIn('FAKE-TEST-TOKEN',json.dumps(result))
        r=self.client.post('/api/capture',headers=self.headers,json={'url':'https://weverse.io/plave/artist/1234'})
        self.assertEqual(r.status_code,400)
        self.assertIn('尚未校准',r.json()['detail'])
        r=self.client.put('/api/settings',headers=self.headers,json={'changes':{'monitor_enabled':True}})
        self.assertEqual(r.status_code,400)

    def test_bad_image_and_position(self):
        r=self.client.post('/api/upload',headers=self.headers,files={'file':('x.png',b'not an image','image/png')})
        self.assertEqual(r.status_code,400)
        im=Image.new('RGB',(320,200));buf=io.BytesIO();im.save(buf,format='PNG')
        r=self.client.post('/api/upload',headers=self.headers,files={'file':('x.png',buf.getvalue(),'image/png')},data={'slots':'[{"y":999}]'})
        self.assertEqual(r.status_code,400)


class QQTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset()
        self.client=QQClient()
        self.actions=[]
        self.connections=[]
        self.reject=False
        async def mock(ws):
            self.connections.append(ws)
            async for raw in ws:
                request=json.loads(raw)
                self.actions.append(request)
                data={'user_id':999999,'nickname':'模拟QQ'} if request['action']=='get_login_info' else {'message_id':123}
                await ws.send(json.dumps({'status':'failed' if self.reject else 'ok','retcode':100 if self.reject else 0,
                                          'data':data,'echo':request['echo']}))
        self.server=await websockets.serve(mock,'127.0.0.1',0)
        port=self.server.sockets[0].getsockname()[1]
        store.save_settings({'ws_url':f'ws://127.0.0.1:{port}','owner_qq':'111111','groups':['123456']})
        await self.client.start()
        await self.wait_for(lambda:self.client.account=='999999')

    async def asyncTearDown(self):
        await self.client.stop()
        self.server.close()
        await self.server.wait_closed()

    async def wait_for(self,predicate):
        for _ in range(150):
            if predicate():return
            await asyncio.sleep(.02)
        self.fail('OneBot模拟请求未按时完成')

    async def message(self,text,user='111111',mid=1,group='123456',extra=None):
        await self.connections[-1].send(json.dumps({'post_type':'message','message_type':'group','group_id':int(group),
            'user_id':int(user),'message_id':mid,'message':[{'type':'text','data':{'text':text}}]+(extra or [])}))

    def sent(self):
        return [a for a in self.actions if a['action']=='send_group_msg']

    async def test_screenshot_and_one_step_roast_preserve_hashes(self):
        from unittest.mock import patch, AsyncMock
        async def capture(url,group):
            return create_post(group, fingerprints=True)
        with patch('weverse_bot.qq.browser.capture',new=AsyncMock(side_effect=capture)) as grab:
            await self.message('截图 https://weverse.io/plave/artist/1234',mid=1)
            await self.wait_for(lambda:len(self.sent())==2)
            original=base64.b64decode(self.sent()[1]['params']['message'][1]['data']['file'][9:])
            self.assertEqual(Image.open(io.BytesIO(original)).height,150)
            await self.message('截图 https://weverse.io/plave/artist/1234',mid=1)
            await asyncio.sleep(.1)
            self.assertEqual(grab.call_count,1)
            await self.message('烤制 https://weverse.io/plave/artist/1234\n今天我很开心 #PLAVE\n#截图 保留这行\n[评论1]\n评论译文',mid=2)
            await self.wait_for(lambda:len(self.sent())==4)
            saved=store.posts(group='123456')[0]
            self.assertEqual(saved['translations']['0'],'今天我很开心 #PLAVE\n#截图 保留这行')
            self.assertEqual(saved['status'],'translated')
            result=base64.b64decode(self.sent()[3]['params']['message'][1]['data']['file'][9:])
            self.assertGreater(Image.open(io.BytesIO(result)).height,150)

    async def test_level3_can_grant_and_revoke_and_disabled_group(self):
        mention=lambda u:[{'type':'at','data':{'qq':u}}]
        await self.message('help',user='222222',mid=10)
        await self.wait_for(lambda:len(self.sent())==1)
        self.assertIn('权限不足',self.sent()[0]['params']['message'][0]['data']['text'])
        await self.message('设置权限 --level 3 ',mid=11,extra=mention('222222'))
        await self.wait_for(lambda:len(self.sent())==2)
        self.assertEqual(store.permission_level('123456','222222'),3)
        await self.message('设置权限 -l 2 ',user='222222',mid=12,extra=mention('333333'))
        await self.wait_for(lambda:len(self.sent())==3)
        self.assertEqual(store.permission_level('123456','333333'),2)
        await self.message('取消权限 -l 2 ',user='222222',mid=13,extra=mention('333333'))
        await self.wait_for(lambda:len(self.sent())==4)
        self.assertEqual(store.permission_level('123456','333333'),0)
        await self.message('help',group='654321',mid=14)
        await asyncio.sleep(.1)
        self.assertEqual(len(self.sent()),4)

    async def test_help_levels_and_plain_at_rejected(self):
        store.set_level('123456','222222',1)
        await self.message('help',user='222222',mid=20)
        await self.wait_for(lambda:len(self.sent())==1)
        text=self.sent()[0]['params']['message'][0]['data']['text']
        self.assertIn('设置水印',text)
        self.assertNotIn('截图 帖子链接',text)
        await self.message('截图 https://weverse.io/plave/artist/1234',user='222222',mid=21)
        await self.wait_for(lambda:len(self.sent())==2)
        self.assertIn('需要 level 2',self.sent()[1]['params']['message'][0]['data']['text'])
        await self.message('设置权限 -l 2 @333333',mid=22)
        await self.wait_for(lambda:len(self.sent())==3)
        self.assertEqual(store.permission_level('123456','333333'),0)
        await self.message('取消权限 -l 3 ',mid=23,extra=[{'type':'at','data':{'qq':'111111'}}])
        await self.wait_for(lambda:len(self.sent())==4)
        self.assertEqual(store.permission_level('123456','111111'),3)

    async def test_view_always_original_and_removed_commands_ignored(self):
        saved=create_post()
        render_post(saved['id'],{'0':'译文','1':'评论'})
        await self.message('查看 '+saved['id'],mid=30)
        await self.wait_for(lambda:len(self.sent())==1)
        original=base64.b64decode(self.sent()[0]['params']['message'][1]['data']['file'][9:])
        self.assertEqual(Image.open(io.BytesIO(original)).height,150)
        for i,text in enumerate(('查看一下天气','/wv 帮助','恢复 '+saved['id'],'引用 '+saved['id'])):
            await self.message(text,mid=31+i)
        await asyncio.sleep(.1)
        self.assertEqual(len(self.sent()),1)

    async def test_level1_png_upload_and_adjustment(self):
        store.set_level('123456','222222',1)
        im=Image.new('RGBA',(64,32),(200,0,0,128));buf=io.BytesIO();im.save(buf,format='PNG')
        extra=[{'type':'image','data':{'file':'base64://'+base64.b64encode(buf.getvalue()).decode()}}]
        await self.message('设置水印',user='222222',mid=40,extra=extra)
        await self.wait_for(lambda:len(self.sent())==1)
        self.assertTrue((store.DATA/store.watermark('123456')['logo']).is_file())
        self.assertIsNone(store.watermark('234567')['logo'])
        await self.message('调整水印 右下 20 60 10',user='222222',mid=41)
        await self.wait_for(lambda:len(self.sent())==2)
        self.assertEqual(store.watermark('123456')['position'],'bottom-right')
        self.assertEqual(store.watermark('123456')['opacity'],60)

    async def test_level2_clear_warehouse(self):
        saved=create_post(fingerprints=True)
        render_post(saved['id'],{'0':'译文','1':'评论'})
        store.set_level('123456','222222',2)
        await self.message('清空仓库',user='222222',mid=50)
        await self.wait_for(lambda:len(self.sent())==1)
        self.assertEqual(store.posts(group='123456'),[])
        self.assertIsNone(store.memory('post-hash','123456'))
        self.assertIn('123456',store.settings()['monitor_paused_groups'])

    async def test_failed_delivery_is_not_reported_as_success(self):
        self.reject=True
        with self.assertRaises(ValueError):
            await self.client.send('123456','测试')


FIXTURE = '''<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#fafafa;font-family:sans-serif}.card{margin:12px;padding:22px;width:620px;background:white;border:1px solid #eee}
.artist{color:green}.text{white-space:pre-wrap;font-size:22px;margin:20px 0}.media{height:160px;background:#e5eedc}button{border:0}
</style><article class="card post"><span class="artist">ARTIST 演示作者</span><div class="text">完整正文 ❤\n第二行原文</div><div class="media">原始图片区域</div><p>点赞 123 · 评论 3</p></article>
<div class="card comment"><span class="artist">ARTIST 演示作者</span><div class="text">这是很长的艺人评论。\n''' + ('完整评论行\n'*25) + '''</div><p>点赞 28</p></div>
<div class="card comment"><span>粉丝作者</span><div class="text">普通粉丝评论，应当被过滤。</div></div>
'''


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset()
        self.browser=Browser()
        executable=os.environ.get('WEVERSE_BROWSER_EXECUTABLE')
        if not executable and shutil.which('chromium'):
            os.environ['WEVERSE_BROWSER_EXECUTABLE']=shutil.which('chromium')
        try:
            ctx=await self.browser.open()
        except Exception as exc:
            await self.browser.close()
            self.skipTest('Chromium未安装：'+type(exc).__name__)
        self.page=await ctx.new_page()
        await self.page.set_content(FIXTURE)
        self.cfg=store.settings()|{'post_selector':'.post','text_selector':'.text','artist_selector':'.artist','author_selector':'.artist',
                                 'comment_selector':'.comment','comment_text_selector':'.text'}

    async def asyncTearDown(self):
        await self.browser.close()

    async def test_artist_filter_long_comments_and_history(self):
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)
        self.assertEqual(len(post['slots']),2)
        self.assertNotIn('普通粉丝',str(post['slots']))
        self.assertEqual(post['slots'][1]['text'].count('完整评论行'),25)
        self.assertGreater(post['slots'][1]['y'],post['slots'][0]['y'])
        first=render_post(post['id'],{'0':'正文译文','1':'完整评论译文'})
        self.assertEqual(first['status'],'translated')
        again=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)
        reused=render_post(again['id'],{},True)
        self.assertEqual(reused['translations'],first['translations'])

    async def test_different_author_cannot_reuse_identical_comment(self):
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)
        render_post(post['id'],{'0':'正文译文','1':'A作者评论译文'})
        await self.page.locator('.comment .artist').evaluate("el=>el.textContent='ARTIST 另一位艺人'")
        newer=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)
        reused=render_post(newer['id'],{},True)
        self.assertEqual(reused['translations'],{'0':'正文译文'})
        self.assertEqual(reused['status'],'partial')

    async def test_unverified_author_rejected(self):
        await self.page.locator('.post .artist').evaluate('(el)=>el.remove()')
        with self.assertRaisesRegex(ValueError,'艺人标识'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)

    async def test_post_must_not_include_comment_list(self):
        await self.page.locator('.post').evaluate("el=>{const c=document.createElement('div');c.className='comment';el.appendChild(c)}")
        with self.assertRaisesRegex(ValueError,'包含评论列表'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)

    async def test_clipped_text_rejected(self):
        await self.page.locator('.post .text').evaluate("el=>{el.style.height='10px';el.style.overflow='hidden'}")
        with self.assertRaisesRegex(ValueError,'截断'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)

    async def test_emoji_graphemes_preserved_and_repeat_preparation_is_stable(self):
        text = '예쁜하루☺️ 👨‍👩‍👧‍👦 🇰🇷 1️⃣ 👍🏽 ©︎'
        node = self.page.locator('.post .text')
        await node.evaluate('(el,text)=>el.textContent=text', text)
        expected = ['☺️', '👨‍👩‍👧‍👦', '🇰🇷', '1️⃣', '👍🏽']
        self.assertEqual(await prepare_emoji_text(node), expected)
        self.assertEqual(await node.inner_text(), text)
        html = await node.inner_html()
        self.assertEqual(await prepare_emoji_text(node), expected)
        self.assertEqual(await node.inner_html(), html)
        self.assertEqual(await node.locator('[data-wvbot-emoji]').count(), 5)

    async def test_text_without_emoji_keeps_its_layout(self):
        node = self.page.locator('.post .text')
        await node.evaluate('(el,html)=>el.innerHTML=html', '<span>普通正文</span>\n第二行')
        html = await node.evaluate('el=>el.outerHTML')
        box = await node.bounding_box()
        self.assertEqual(await prepare_emoji_text(node), [])
        self.assertEqual(await node.evaluate('el=>el.outerHTML'), html)
        self.assertEqual(await node.bounding_box(), box)

    async def test_emoji_ink_fits_inside_tight_line_after_preparation(self):
        await self.page.set_content('''<style>body{margin:0;background:white}
        p{margin:0;font-size:32px;line-height:12px;overflow:hidden;width:300px;color:black}
        </style><p>☺️</p>''')
        node = self.page.locator('p')
        before = Image.open(io.BytesIO(await node.screenshot())).convert('RGB')
        self.assertEqual(await prepare_emoji_text(node), ['☺️'])
        await self.page.evaluate('document.fonts.ready')
        after = Image.open(io.BytesIO(await node.screenshot())).convert('RGB')
        ink = ImageOps.invert(after).getbbox()
        self.assertIsNotNone(ink)
        self.assertGreater(ink[1], 0)
        self.assertLess(ink[3], after.height)
        self.assertEqual(before.width, after.width)
        self.assertGreater(after.height, before.height)
        self.assertEqual(await node.inner_text(), '☺️')

    async def test_emoji_capture_keeps_width_media_and_insertion_below_original(self):
        text = '예쁜하루☺️ 👨‍👩‍👧‍👦'
        node = self.page.locator('.post .text')
        await node.evaluate('(el,text)=>el.textContent=text', text)
        media_before = await self.page.locator('.post .media').bounding_box()
        card_before = await self.page.locator('.post').bounding_box()
        cfg = self.cfg | {'comment_selector': ''}
        post = await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',cfg)
        media_after = await self.page.locator('.post .media').bounding_box()
        card_after = await self.page.locator('.post').bounding_box()
        emoji_box = await node.locator('[data-wvbot-emoji]').last.bounding_box()
        slot = post['slots'][0]
        self.assertEqual(slot['text'], text)
        self.assertEqual(card_before['width'], card_after['width'])
        self.assertEqual(media_before['width'], media_after['width'])
        self.assertEqual(media_before['height'], media_after['height'])
        self.assertGreaterEqual(slot['y'], emoji_box['y'] + emoji_box['height'] - card_after['y'] - 1)
        self.assertLessEqual(slot['y'], media_after['y'] - card_after['y'] + 1)
        with Image.open(store.DATA / post['original']) as image:
            self.assertEqual(image.width, round(card_before['width']))

    async def test_translation_aligns_with_original_and_preserves_entire_photo(self):
        store.save_settings({'watermark': ''})
        await self.page.set_content('''<style>
        body{margin:0}.post{width:420px;background:white;box-sizing:border-box;padding:16px}
        .artist{height:40px}.text{margin:16px 0;font-size:14px;white-space:pre-wrap}
        .media{height:400px;background:rgb(160,20,80)}
        </style><article class="post"><div class="artist">YEJUN</div>
        <p class="text">예쁜하루☺️</p><div class="media"></div></article>''')
        cfg = self.cfg | {'comment_selector': ''}
        post = await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',cfg)
        slot = post['slots'][0]
        self.assertEqual(slot['x'], 16)
        self.assertEqual(slot['width'], 388)
        self.assertEqual(slot['font_size'], 14)
        translated = await asyncio.to_thread(render_post, post['id'], {'0': '漂亮的一天🙂'})
        with Image.open(store.DATA / post['original']) as original, Image.open(store.DATA / translated['output']) as result:
            added = result.height - original.height
            self.assertGreater(added, 0)
            self.assertEqual(result.width, original.width)
            self.assertEqual(result.crop((0, 0, 420, slot['y'])).tobytes(),
                             original.crop((0, 0, 420, slot['y'])).tobytes())
            self.assertEqual(result.crop((0, slot['y'] + added, 420, result.height)).tobytes(),
                             original.crop((0, slot['y'], 420, original.height)).tobytes())
            inserted = result.crop((0, slot['y'], 420, slot['y'] + added))
            self.assertGreaterEqual(ImageOps.invert(inserted).getbbox()[0], 16)
            self.assertEqual(translated['translations']['0'], '漂亮的一天🙂')

    async def test_consent_and_site_chrome_do_not_cover_author_or_media(self):
        avatar = io.BytesIO()
        Image.new('RGB',(32,32),(255,165,0)).save(avatar,format='PNG')
        image = 'data:image/png;base64,'+base64.b64encode(avatar.getvalue()).decode()
        await self.page.set_content('''<style>
        body{margin:0}.post{width:620px;padding:12px;background:white;box-sizing:border-box}
        .community-artist-postId-_-header{display:none;height:48px}
        img{width:32px;height:32px;vertical-align:top}.artist{display:inline-block}
        .text{margin:16px 0}.media{height:1200px;background:rgb(235,235,235)}
        .global-_-header{position:fixed;top:0;width:100%;height:100px;background:magenta;z-index:30}
        .login-required-bottom-layer-_-login_required_wrap{position:fixed;bottom:0;width:100%;height:100px;background:red;z-index:30}
        #consent{position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.7)}
        </style><nav class="global-_-header">Weverse PLAVE 菜单</nav>
        <article class="post"><div class="community-artist-postId-_-header">
        <span class="avatar-decorator-_-image"><img src="'''+image+'''"></span>
        <span class="artist avatar-decorator-_-title">YEJUN</span></div>
        <p class="text">예쁜하루☺️</p><div class="media"></div><p>发布时间</p></article>
        <div class="login-required-bottom-layer-_-login_required_wrap">登录后查看</div>
        <div id="consent" role="dialog"><p>Weverse asks for your consent to use your personal data</p>
        <button onclick="window.choice='reject';document.getElementById('consent').remove()">Do not consent</button>
        <button onclick="window.choice='accept'">Consent</button></div>''')
        cfg=self.cfg|{'comment_selector':'','author_selector':'.avatar-decorator-_-title'}
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',cfg)
        self.assertEqual(await self.page.evaluate('window.choice'),'reject')
        self.assertEqual(post['slots'][0]['author'],'YEJUN')
        self.assertEqual(post['slots'][0]['text'],'예쁜하루☺️')
        self.assertTrue(await self.page.locator('.avatar-decorator-_-title').is_visible())
        self.assertFalse(await self.page.locator('.global-_-header').is_visible())
        self.assertFalse(await self.page.locator('.login-required-bottom-layer-_-login_required_wrap').is_visible())
        im=Image.open(store.DATA/post['original']).convert('RGB')
        self.assertGreater(im.height,1000)
        self.assertEqual(im.getpixel((16,16)),(255,165,0))
        colors={color for _,color in im.getcolors(maxcolors=im.width*im.height)}
        self.assertNotIn((255,0,255),colors)
        self.assertNotIn((255,0,0),colors)

    async def test_consent_in_iframe_is_rejected_even_when_frame_closes(self):
        await self.page.set_content('<iframe id="cmp"></iframe>')
        frame=await (await self.page.locator('#cmp').element_handle()).content_frame()
        await frame.set_content('''<p>Weverse asks for your consent</p>
        <button onclick="parent.choice='reject';frameElement.remove()">Do not consent</button>
        <button onclick="parent.choice='accept'">Consent</button>''')
        await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('window.choice'),'reject')
        self.assertEqual(await self.page.locator('#cmp').count(),0)

    async def test_consent_without_rejection_is_not_accepted_or_hidden(self):
        await self.page.set_content(FIXTURE+'''<div id="consent" role="dialog">
        <p>Weverse asks for your consent</p><button onclick="window.choice='accept'">Consent</button></div>''')
        with self.assertRaisesRegex(ValueError,'未找到明确的拒绝按钮'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)
        self.assertIsNone(await self.page.evaluate('window.choice'))
        self.assertTrue(await self.page.locator('#consent').is_visible())
        self.assertEqual(store.posts(),[])

    async def test_delayed_consent_is_rejected(self):
        await self.page.evaluate('''() => setTimeout(() => {
            const dialog=document.createElement('div');
            dialog.innerHTML='<p>Weverse asks for your consent</p><button>Do not consent</button>';
            dialog.querySelector('button').onclick=()=>{window.choice='reject';dialog.remove()};
            document.body.appendChild(dialog);
        }, 100)''')
        await reject_optional_consent(self.page,wait_ms=800)
        self.assertEqual(await self.page.evaluate('window.choice'),'reject')

    async def test_rejection_does_not_allow_a_remaining_backdrop(self):
        await self.page.set_content('''<div id="backdrop" style="position:fixed;inset:0;background:rgba(0,0,0,.7)">
        <p>Weverse asks for your consent</p>
        <button onclick="window.choice='reject';this.previousElementSibling.remove();this.remove()">Do not consent</button>
        </div>''')
        with self.assertRaisesRegex(ValueError,'关闭弹窗失败'):
            await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('window.choice'),'reject')
        self.assertTrue(await self.page.locator('#backdrop').is_visible())

    async def test_hidden_author_is_not_saved_as_an_incomplete_screenshot(self):
        await self.page.locator('.post .artist').evaluate("el=>el.style.visibility='hidden'")
        with self.assertRaisesRegex(ValueError,'作者名称.*不可见'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)
        self.assertEqual(store.posts(),[])

    async def test_missing_avatar_is_not_saved_as_an_incomplete_screenshot(self):
        await self.page.locator('.post .artist').evaluate('''el => {
            const header=document.createElement('div');
            header.className='community-artist-postId-_-header';
            el.before(header);header.appendChild(el);
        }''')
        with self.assertRaisesRegex(ValueError,'发帖者头像.*不可见'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg)
        self.assertEqual(store.posts(),[])

    async def test_consent_preference_survives_persistent_browser_restart(self):
        fixture='''<script>
        if(!document.cookie.includes('optional_consent=denied')){
            document.write('<div id="consent"><p>Weverse asks for your consent</p><button>Do not consent</button></div>');
            document.querySelector('button').onclick=()=>{
                document.cookie='optional_consent=denied; Path=/; Max-Age=86400';
                document.querySelector('#consent').remove();
            };
        }</script>'''
        url='https://weverse.io/plave/artist/1234'
        await self.page.route(url,lambda route:route.fulfill(body=fixture,content_type='text/html'))
        await self.page.goto(url)
        await reject_optional_consent(self.page)
        self.assertIn('optional_consent=denied',await self.page.evaluate('document.cookie'))
        await self.browser.close()
        context=await self.browser.open()
        self.page=await context.new_page()
        await self.page.route(url,lambda route:route.fulfill(body=fixture,content_type='text/html'))
        await self.page.goto(url)
        self.assertEqual(await self.page.locator('#consent').count(),0)


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_dedup_multi_group_and_new_comments(self):
        from unittest.mock import patch, AsyncMock
        from weverse_bot.app import monitor_once
        import uuid
        reset()
        store.save_settings({'groups':['123456','234567']})
        source_image()
        slots=[{'key':'0','label':'正文','y':55,'text':'正文','fingerprint':'original'}]
        async def capture(url,group):
            name='originals/'+uuid.uuid4().hex+'.png'
            shutil.copyfile(store.DATA/'originals/abc123.png',store.DATA/name)
            return store.add_post(url,'测试',group,name,slots,'weverse')
        mock_capture=AsyncMock(side_effect=capture)
        with patch('weverse_bot.app.browser.discover',new=AsyncMock(return_value=['https://weverse.io/plave/artist/1234'])), \
             patch('weverse_bot.app.browser.capture',new=mock_capture):
            await monitor_once()
            self.assertEqual(len(store.posts()),2)
            self.assertEqual(mock_capture.call_count,1)
            first=store.posts(group='123456')[0]
            store.update_post(first['id'],status='ignored')
            await monitor_once()
            self.assertEqual(len(store.posts()),2)
            self.assertEqual(store.get_post(first['id'])['status'],'ignored')
            for p in store.posts():
                self.assertTrue((store.DATA/p['original']).is_file())
            slots.append({'key':'1','label':'艺人评论 1','y':120,'text':'新评论','fingerprint':'new-comment'})
            await monitor_once()
            self.assertEqual(len(store.posts()),4)
            self.assertEqual(len(store.posts(group='123456')[0]['slots']),2)
            self.assertEqual(mock_capture.call_count,3)


if __name__=='__main__':
    unittest.main()
