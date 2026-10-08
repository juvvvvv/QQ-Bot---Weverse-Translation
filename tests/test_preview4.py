"""Regression cases for the user's preview3 layout and session feedback."""
import asyncio
import base64
import io
import json
import unittest
from PIL import Image
from fastapi.testclient import TestClient
import test_workflow as workflow
import test_v3 as fixtures
from weverse_bot import store
from weverse_bot.app import app
from weverse_bot.capture_layout import measure_card
from weverse_bot.artist_comments import collect_artist_comments, stage_comment, TEXT, artist_avatar_sources
from weverse_bot.page_cleanup import reject_optional_consent
from weverse_bot.translations import render_body, latest_context, prepare
from weverse_bot.watermark import text_job


def data_image(color):
    stream=io.BytesIO();Image.new('RGBA',(32,32),color).save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()


FC = '''<div class="fc-consent-root"><div class="fc-dialog-overlay" style="position:fixed;inset:0;z-index:50000;background:#3338"></div>
<div class="fc-dialog-container"><div class="fc-dialog fc-choice-dialog" role="dialog" style="position:fixed;top:20px;left:20px;z-index:50001;background:white">
<h1>Weverse asks for your consent</h1>
<button class="fc-button fc-cta-do-not-consent fc-secondary-button" aria-label="Do not consent"
onclick="window.order=(window.order||[]).concat('central');this.parentElement.style.display='none';setTimeout(()=>{document.querySelector('.fc-dialog-overlay').style.display='none'},450)"><p class="fc-button-label">Do not consent</p></button>
<button onclick="window.accepted=true">Consent</button></div>
<div class="fc-dialog fc-data-preferences-dialog" role="dialog" style="display:none">Preferences</div></div></div>'''
COOKIE = '''<div class="_w_bottom_fixed_13jy1_4" style="position:fixed;bottom:0;z-index:30000;background:#222">
<a href="https://weverse.io/policies/cookie">查看详情</a>
<button class="_w_button_continue_13jy1_29" onclick="window.order=(window.order||[]).concat('bottom');this.parentElement.remove()">不同意并继续</button>
<button onclick="window.accepted=true">可选同意</button><button onclick="window.accepted=true">全部同意</button></div>'''


class SkipAndSessionTests(unittest.TestCase):
    def setUp(self):workflow.reset();store.save_watermark('',{'enabled':False})

    def test_skip_all_keeps_original_size_and_pixels_and_is_a_successful_version(self):
        post=fixtures.draft();before=Image.open(store.DATA/post['original']).convert('RGB')
        result=render_body(post['id'],'/k\n+\n/k')
        after=Image.open(store.DATA/result['output']).convert('RGB')
        self.assertEqual(before.size,after.size);self.assertEqual(before.tobytes(),after.tobytes())
        self.assertEqual(result['status'],'translated');self.assertEqual(result['translations'],{'0':'/k','1':'/k'})

    def test_skip_is_reused_by_comment_id_and_can_be_overwritten_later(self):
        first=fixtures.draft();render_body(first['id'],'/k\n+\n/k')
        fresh=fixtures.draft(extra=True)
        self.assertEqual(latest_context(fresh)[1],{'0':'/k','2':'/k'})
        result=render_body(fresh['id'],'+\n新增/e')
        self.assertEqual(result['translations'],{'0':'/k','2':'/k','1':'新增👨‍👩‍👧‍👦'})
        corrected=render_body(result['id'],'正文\n+\n新增\n+\n评论')
        self.assertEqual(corrected['translations'],{'0':'正文','1':'新增','2':'评论'})

    def test_skip_does_not_shift_following_translation_or_use_emoji(self):
        post=fixtures.draft(extra=True)
        self.assertEqual(prepare(post,'/k\n+\n/k\n+\n末条/e'),{'0':'/k','1':'/k','2':'末条🙂'})
        with self.assertRaisesRegex(ValueError,'空白译文段'):prepare(post,'正文\n+\n\n+\n最后')

    def test_cookie_free_session_headers_and_scoped_media_link(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/bootstrap').status_code,403)
            self.assertEqual(client.get('/api/bootstrap',headers={'x-wv-bootstrap':'1','sec-fetch-site':'cross-site'}).status_code,403)
            self.assertEqual(client.get('/api/bootstrap',headers={'x-wv-bootstrap':'1','origin':'http://localhost:9999'}).status_code,403)
            bootstrap=client.get('/api/bootstrap',headers={'x-wv-bootstrap':'1'})
            self.assertEqual(bootstrap.headers['cache-control'],'no-store')
            session=bootstrap.json();client.cookies.clear()
            headers={'x-wv-session':session['session'],'x-wv-csrf':session['csrf']}
            self.assertEqual(client.put('/api/settings',headers=headers,json={'changes':{'comment_wait_seconds':60}}).status_code,200)
            self.assertEqual(client.put('/api/settings',headers={'x-wv-session':session['session']},json={'changes':{}}).status_code,403)
            post=fixtures.draft()
            info=client.get('/api/posts/'+post['id'],headers=headers).json()
            self.assertEqual(client.get(info['original_url']).status_code,200)
            bad=info['original_url'].replace('abc123.png','different.png')
            self.assertEqual(client.get(bad).status_code,401)
            self.assertEqual(client.get('/api/settings').status_code,401)

    def test_watermark_is_arial_without_changing_translation_font(self):
        job=text_job(420,store.WATERMARK_DEFAULTS,24,1)
        self.assertTrue(job['font_family'].startswith('Arial,'))


class LayoutAndConsentTests(unittest.IsolatedAsyncioTestCase):
    fixture=fixtures.NativeCommentTests.fixture
    async def asyncSetUp(self):await workflow.BrowserTests.asyncSetUp(self)
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def test_original_lines_are_complete_before_comment_translation_insertion(self):
        await self.fixture()
        await self.page.add_style_tag(content='''.line-clamp-node-view-_-wrap{height:18px!important;overflow:visible!important}
        .line-clamp-node-view-_-container{height:18px!important;max-height:18px!important;overflow:visible!important;position:absolute!important;transform:translateY(2px)}''')
        records=await collect_artist_comments(self.page,self.cfg|{'comment_selector':''})
        stage=await stage_comment(self.page,records[0],420)
        box=await measure_card(stage,TEXT)
        bottom=await stage.locator(TEXT).evaluate('''el=>{const range=document.createRange();range.selectNodeContents(el);return Math.max(...[...range.getClientRects()].map(r=>r.bottom))-el.closest('[data-wvbot-stage]').getBoundingClientRect().top}''')
        self.assertGreater(box['y'],bottom-1)
        self.assertGreater(await stage.locator(TEXT).evaluate('el=>el.getBoundingClientRect().height'),80)
        self.assertEqual(await stage.locator(TEXT).inner_text(),'예 주니형\n쁜 라인\n하,,은호형까지\n루 미큐브 다들어와')
        # Capture and bake all cards; every original pixel preceding the first
        # comment translation remains intact, with a whole original block above it.
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'comment_selector':''})
        before=Image.open(store.DATA/post['original']).convert('RGB')
        body='\n+\n'.join('/k' if i!=1 else '第一条完整中文译文' for i in range(15))
        result=await asyncio.to_thread(render_body,post['id'],body)
        after=Image.open(store.DATA/result['output']).convert('RGB')
        y=post['slots'][1]['y']
        self.assertEqual(before.crop((0,0,420,y)).tobytes(),after.crop((0,0,420,y)).tobytes())
        self.assertGreater(after.height,before.height);self.assertEqual(after.width,before.width)
        after.save('/tmp/plave-preview4-native-translated.png')

    async def test_avatar_placeholders_and_hidden_styles_resolve_only_same_artist(self):
        await self.fixture()
        blue=data_image((0,110,220,255));blank=data_image((0,0,0,0))
        await self.page.locator('.post').evaluate('''(el,src)=>{const h=document.createElement('div');h.className='community-artist-postId-_-header';h.innerHTML='<div class="avatar-decorator-_-image"><img width="32" height="32"></div>';h.querySelector('img').src=src;el.prepend(h)}''',blue)
        await self.page.locator('.comment-item-_-container').evaluate_all('''(cards,src)=>cards.forEach(card=>{if(card.querySelector('.comment-item-header-profile-name-_-name').textContent.includes('YEJUN')||card.getAttribute('data-wev-comment-id')==='3-511575106')card.querySelector('img').src=src})''',blank)
        await self.page.add_style_tag(content='.comment-item-_-image_area img{display:none!important;visibility:hidden!important;opacity:0!important;transform:scale(0)!important}')
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'comment_selector':''})
        before=Image.open(store.DATA/post['original']).convert('RGB')
        # YEJUN is blue from the original post; HAMIN retains the orange source
        # in the supplied sanitized fixture. No avatar is borrowed across artists.
        colors=set(before.getdata());self.assertIn((0,110,220),colors);self.assertIn((255,165,0),colors)
        self.assertEqual(len(post['slots']),15)
        before.save('/tmp/plave-preview4-native-original.png')

    async def test_missing_real_avatar_stops_instead_of_saving_blank_avatar(self):
        await self.fixture()
        await self.page.locator('.comment-item-_-image_area img').evaluate_all('(images,src)=>images.forEach(img=>img.src=src)',data_image((0,0,0,0)))
        with self.assertRaisesRegex(ValueError,'头像仍是空白占位图'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'comment_selector':''})

    async def test_comment_pseudo_curves_and_extra_icons_are_removed_but_toolbar_remains(self):
        await self.fixture()
        await self.page.add_style_tag(content='''.avatar-_-image_wrap::before,.comment-item-_-text_area::after{content:"extra";position:absolute;border:1px solid #ddd;border-radius:50%;width:300px;height:300px}''')
        records=await collect_artist_comments(self.page,self.cfg|{'comment_selector':''})
        stage=await stage_comment(self.page,records[0],420)
        self.assertEqual(await stage.locator('.avatar-_-image_wrap').evaluate("el=>getComputedStyle(el,'::before').display"),'none')
        self.assertEqual(await stage.locator('.comment-item-_-text_area').evaluate("el=>getComputedStyle(el,'::after').display"),'none')
        self.assertTrue(await stage.locator('.comment-item-_-interaction').is_visible())

    async def test_main_toolbar_full_height_counts_even_when_svg_is_aria_hidden(self):
        await self.page.set_content('''<style>body{margin:0}.post{width:420px;padding:16px;background:white;box-sizing:border-box}
        .toolbar-_-container{height:40px;padding:8px 0;box-sizing:border-box}</style>
        <article class="post"><div class="artist">YEJUN</div><p class="text">原文</p>
        <div class="toolbar-_-container"><svg aria-hidden="true" width="24" height="24"><rect width="24" height="24" fill="#cc2244"/></svg></div></article>''')
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'capture_artist_comments':False,'comment_selector':''})
        image=Image.open(store.DATA/post['original']).convert('RGB')
        pixels=image.load();rows=[y for y in range(image.height) if pixels[20,y]==(204,34,68)]
        self.assertEqual(len(rows),24);self.assertGreaterEqual(image.height-1-max(rows),16)

    async def test_fc_sibling_overlay_closes_before_cookie_choice(self):
        await self.page.set_content(FC+COOKIE)
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.order'),['central','bottom'])
        self.assertIsNone(await self.page.evaluate('window.accepted'))
        self.assertFalse(await self.page.locator('.fc-dialog-overlay').is_visible())
        # The root and hidden preferences stay mounted; they are not a blocker.
        self.assertEqual(await self.page.locator('.fc-consent-root').count(),1)

    async def test_central_choice_is_prioritized_even_if_bottom_is_not_covered(self):
        await self.page.set_content((FC+COOKIE).replace('position:fixed;inset:0;z-index:50000;background:#3338','display:none'))
        await self.page.locator('.fc-cta-do-not-consent').evaluate('el=>{el.disabled=true;setTimeout(()=>el.disabled=false,700)}')
        await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('window.order'),['central','bottom'])

    async def test_explicit_fc_reject_class_is_independent_of_label_language(self):
        await self.page.set_content((FC+COOKIE).replace('Do not consent','拒绝收集').replace('Weverse asks for your consent','隐私设置'))
        await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('window.order'),['central','bottom'])

    async def test_remaining_fc_overlay_is_never_hidden_to_take_screenshot(self):
        html=FC.replace("setTimeout(()=>{document.querySelector('.fc-dialog-overlay').style.display='none'},450)","window.overlayRemains=true")
        await self.page.set_content(html+COOKIE)
        with self.assertRaisesRegex(ValueError,'关闭弹窗失败'):
            await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('window.order'),['central'])
        self.assertTrue(await self.page.locator('.fc-dialog-overlay').is_visible())
