"""Fanpost and ordinary-only samples plus continuous browser recovery."""
import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from PIL import Image
from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeout
import test_workflow as workflow
from weverse_bot import store
from weverse_bot.artist_comments import collect_artist_comments
from weverse_bot.translations import render_body, latest_context
from weverse_bot.capture import Browser
from test_preview4 import data_image, FC, COOKIE

FIXTURES=Path(__file__).with_name('fixtures')
STYLE='''<style>body{margin:0;font-family:Arial}.blind{display:none}
.community-fanpost-postId-_-main,.post{width:420px;padding:16px;box-sizing:border-box;background:white}
.community-fanpost-postId-_-aside{width:420px}.community-fanpost-postId-_-sticky_aside{position:static!important;width:auto!important}
.WeverseViewer,.text{font-size:14px;line-height:1.6}.p{margin:14px 0;white-space:pre-wrap}
.avatar-decorator-_-wrap{display:flex;gap:8px}.avatar-decorator-_-image img{width:32px;height:32px;border-radius:50%}
.avatar-decorator-_-title_area{display:flex;align-items:center}.avatar-decorator-_-subtitle{font-size:12px;color:#999}
.icon-_-icon{display:inline-flex}.icon-_-icon svg{width:100%;height:100%;display:block}.badge{width:16px;height:16px}
.comment-item-_-container{display:flex}.line-clamp-node-view-_-container{font-size:14px;line-height:1.6}
.comment-item-_-image_area img{width:32px;height:32px}.toolbar-_-left{display:flex;gap:12px}
.community-fanpost-postId-_-action_bar{position:fixed;bottom:0;left:0;width:420px;background:white}
</style>'''


class PostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):await workflow.BrowserTests.asyncSetUp(self);store.save_watermark('',{'enabled':False})
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def fan(self,zero=False):
        await self.page.set_content(STYLE+(FIXTURES/'weverse-fanpost.html').read_text())
        # Recreate real nested links via DOM, preserving React's actual structure.
        await self.page.locator('snapshot-anchor').evaluate_all('''nodes=>nodes.forEach(n=>{const a=document.createElement('a');for(const x of n.attributes)a.setAttribute(x.name,x.value);a.append(...n.childNodes);n.replaceWith(a)})''')
        if zero:
            html=(FIXTURES/'weverse-ordinary-comments.html').read_text()
            await self.page.locator('.community-fanpost-postId-_-aside').evaluate('(el,html)=>el.innerHTML=html',html)

    async def extract(self,path='artist/2-180803593',**extra):
        return await self.browser.extract(self.page,'https://weverse.io/plave/'+path,'123456',self.cfg|{'comment_selector':'','max_scrolls':1}|extra)

    async def test_fan_original_with_two_artist_comments_is_three_segments_and_excludes_members(self):
        await self.fan()
        post=await self.extract()
        self.assertEqual(len(post['slots']),3)
        self.assertEqual(post['slots'][0]['post_kind'],'fan')
        self.assertEqual(post['slots'][0]['author'],'粉丝原帖作者')
        self.assertTrue(post['slots'][0]['text'].startswith('도은호 아프지마'))
        self.assertEqual([s['author'] for s in post['slots'][1:]],['EUNHO','EUNHO'])
        self.assertEqual([s['published'] for s in post['slots'][1:]],['10. 06. 04:58','10. 06. 04:59'])
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],2)
        original=Image.open(store.DATA/post['original']).convert('RGB')
        self.assertEqual(original.width,420);self.assertIn((0,110,220),set(original.getdata()))
        self.assertIn((255,165,0),set(original.getdata()))
        result=await asyncio.to_thread(render_body,post['id'],'粉丝正文中文\n+\n第一条评论\n+\n第二条评论/e')
        self.assertTrue(result['translations']['2'].endswith('❤️'))
        translated=Image.open(store.DATA/result['output']);self.assertEqual(translated.width,420);self.assertGreater(translated.height,original.height)
        original.save('/tmp/plave-preview6-fan-original.png');translated.save('/tmp/plave-preview6-fan-translated.png')

    async def test_ordinary_only_fanpost_is_one_segment_without_comment_heading(self):
        await self.fan(zero=True)
        post=await self.extract(path='fanpost/12345')
        self.assertEqual(len(post['slots']),1);self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],0)
        self.assertEqual(await self.page.locator('[data-wvbot-counts]').count(),0)
        original=Image.open(store.DATA/post['original']).convert('RGB')
        result=await asyncio.to_thread(render_body,post['id'],'/k')
        after=Image.open(store.DATA/result['output']).convert('RGB')
        self.assertEqual(original.tobytes(),after.tobytes());self.assertEqual(original.size,after.size)
        with self.assertRaisesRegex(ValueError,'需要 1 段'):
            await asyncio.to_thread(render_body,post['id'],'正文\n+\n多余')

    async def test_artist_original_with_ordinary_only_region_is_also_one_segment(self):
        await self.page.set_content(STYLE+'<article class="post"><div class="artist">YEJUN</div><p class="text">예쁜하루☺️</p></article>'+(FIXTURES/'weverse-ordinary-comments.html').read_text())
        post=await self.extract()
        self.assertEqual(len(post['slots']),1);self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],0)
        self.assertEqual(post['slots'][0]['post_kind'],'artist')
        result=await asyncio.to_thread(render_body,post['id'],'漂亮的一天/e')
        self.assertEqual(result['translations'],{'0':'漂亮的一天☺️'})
        self.assertEqual(result['status'],'translated')

    async def test_late_artist_region_is_not_misread_as_zero(self):
        await self.fan()
        await self.page.evaluate('''()=>{const region=document.querySelector('.base-comment-artist-count-and-toggle-_-container').closest('.comment-shape-by-item-type-_-container');region.remove();setTimeout(()=>document.querySelector('.sticky-comment-_-comment_list').prepend(region),1000)}''')
        rows=await collect_artist_comments(self.page,self.cfg|{'comment_selector':'','comment_wait_seconds':5})
        self.assertEqual(len(rows),2)

    async def test_busy_normal_region_and_malformed_artist_header_are_never_zero(self):
        await self.fan(zero=True)
        await self.page.locator('.community-fanpost-postId-_-aside .comment-shape-by-item-type-_-container').first.evaluate("el=>el.setAttribute('aria-busy','true')")
        with self.assertRaisesRegex(ValueError,'加载等待超时'):
            await collect_artist_comments(self.page,self.cfg|{'comment_selector':'','comment_wait_seconds':.6})
        await self.fan();await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate('el=>el.remove()')
        with self.assertRaisesRegex(ValueError,'未识别艺人评论计数'):
            await collect_artist_comments(self.page,self.cfg|{'comment_selector':'','comment_wait_seconds':.6})

    async def test_legacy_selector_cannot_add_member_comments_to_confirmed_zero(self):
        await self.fan(zero=True)
        post=await self.extract(comment_selector='.comment-item-_-container',artist_selector='.comment-item-header-_-inline-badge',comment_text_selector='.line-clamp-node-view-_-container')
        self.assertEqual(len(post['slots']),1)

    async def test_fan_name_matching_artist_cannot_supply_an_artist_avatar(self):
        await self.fan()
        await self.page.locator('.community-fanpost-postId-_-header .avatar-decorator-_-title').evaluate("el=>el.textContent='EUNHO'")
        await self.page.locator('.comment-list-by-artists-_-comment_list img').evaluate_all('(images,src)=>images.forEach(img=>img.src=src)',data_image((0,0,0,0)))
        with self.assertRaisesRegex(ValueError,'头像仍是空白占位图'):
            await self.extract()

    async def test_fan_media_and_multiple_paragraphs_stay_after_original_translation_position(self):
        await self.fan(zero=True)
        await self.page.locator('.WeverseViewer').evaluate('''el=>{el.querySelector('.blind').remove();const p=document.createElement('p');p.className='p';p.textContent='第二段原文🙂';const media=document.createElement('div');media.className='WidgetMedia';media.setAttribute('role','img');media.style.cssText='height:100px;background:rgb(180,20,80)';el.append(p,media)}''')
        post=await self.extract()
        slot=post['slots'][0];self.assertIn('第二段原文🙂',slot['text'])
        root=await self.page.locator('.community-fanpost-postId-_-main').bounding_box();media=await self.page.locator('.WidgetMedia').bounding_box()
        self.assertLessEqual(slot['y'],media['y']-root['y']+1)
        before=Image.open(store.DATA/post['original']).convert('RGB')
        result=await asyncio.to_thread(render_body,post['id'],'第一段中文\n第二段中文/e')
        after=Image.open(store.DATA/result['output']).convert('RGB')
        self.assertEqual(sum(p==(180,20,80) for p in before.getdata()),sum(p==(180,20,80) for p in after.getdata()))

    async def test_fanpost_rejects_both_consent_prompts_and_hides_login_banner(self):
        await self.fan()
        await self.page.evaluate('(html)=>document.body.insertAdjacentHTML("beforeend",html)',FC+COOKIE+'<div class="login-required-bottom-layer-_-login_required_wrap" style="position:fixed;bottom:0;background:red">登录后查看</div>')
        post=await self.extract()
        self.assertEqual(await self.page.evaluate('window.order'),['central','bottom'])
        self.assertFalse(await self.page.locator('.login-required-bottom-layer-_-login_required_wrap').is_visible())
        self.assertEqual(len(post['slots']),3)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):await workflow.BrowserTests.asyncSetUp(self);store.save_settings({k:self.cfg[k] for k in ('post_selector','text_selector','artist_selector','author_selector')})
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def test_closed_persistent_context_recovers_and_keeps_written_rejection(self):
        old=self.browser.context
        await old.add_cookies([{'name':'test_rejection','value':'reject','domain':'weverse.io','path':'/','expires':time.time()+86400}])
        await old.close()
        context=await self.browser.open()
        self.assertIsNot(context,old)
        self.assertTrue(any(c['name']=='test_rejection' and c['value']=='reject' for c in await context.cookies()))
        self.assertEqual(await (await context.new_page()).evaluate('1'),1)

    async def test_timeout_is_retried_once_and_success_does_not_duplicate_archive(self):
        ready=AsyncMock(side_effect=[BrowserTimeout('synthetic timeout'),self.page]);extract=AsyncMock(return_value={'id':'success'})
        with patch.object(self.browser,'ready_page',ready),patch.object(self.browser,'extract',extract),patch.object(self.browser,'reset_connection',AsyncMock()) as reset:
            result=await self.browser.capture('https://weverse.io/plave/artist/1234')
            self.assertEqual(result['id'],'success');self.assertEqual(ready.await_count,2);self.assertEqual(extract.await_count,1);reset.assert_awaited_once()

    async def test_repeat_timeout_stops_after_two_attempts_and_log_excludes_raw_url_token(self):
        with patch.object(self.browser,'ready_page',AsyncMock(side_effect=BrowserTimeout('https://weverse.io/?token=PRIVATE_TEST')) ) as ready,patch.object(self.browser,'reset_connection',AsyncMock()):
            with self.assertRaisesRegex(ValueError,'网页打开.*超时'):await self.browser.capture('https://weverse.io/plave/artist/1234')
            self.assertEqual(ready.await_count,2)
        with store.db() as c:logs=' '.join(r['message'] for r in c.execute('SELECT message FROM events'))
        self.assertIn('阶段：网页打开',logs);self.assertNotIn('PRIVATE_TEST',logs)

    async def test_validation_error_is_not_retried(self):
        with patch.object(self.browser,'ready_page',AsyncMock(return_value=self.page)) as ready,patch.object(self.browser,'extract',AsyncMock(side_effect=ValueError('选择器不唯一'))),patch.object(self.browser,'reset_connection',AsyncMock()) as reset:
            with self.assertRaisesRegex(ValueError,'选择器不唯一'):await self.browser.capture('https://weverse.io/plave/artist/1234')
            self.assertEqual(ready.await_count,1);reset.assert_not_awaited()

    async def test_three_continuous_reads_after_browser_closed_keep_profile_and_results(self):
        store.save_settings({'headless':True,'capture_width':420,'capture_scale':1,'max_scrolls':1,'comment_selector':''})
        original_open=self.browser.open
        async def routed_open(*args,**kwargs):
            context=await original_open(*args,**kwargs)
            if not getattr(context,'_test_routes',False):
                async def route(request):
                    if request.request.url.endswith('/1234'):
                        html=STYLE+(FIXTURES/'weverse-fanpost.html').read_text()
                        html+='''<script>document.querySelectorAll('snapshot-anchor').forEach(n=>{const a=document.createElement('a');for(const x of n.attributes)a.setAttribute(x.name,x.value);a.append(...n.childNodes);n.replaceWith(a)})</script>'''
                    else:
                        html=STYLE+'<article class="post"><div class="artist">YEJUN</div><p class="text">예쁜하루☺️</p></article>'+(FIXTURES/'weverse-ordinary-comments.html').read_text()
                    await request.fulfill(status=200,content_type='text/html',body=html)
                await context.route('**/*',route);context._test_routes=True
            return context
        with patch.object(self.browser,'open',routed_open):
            first=await self.browser.capture('https://weverse.io/plave/artist/1234','123456')
            second=await self.browser.capture('https://weverse.io/plave/artist/1235','123456')
            await self.browser.context.close()
            third=await self.browser.capture('https://weverse.io/plave/artist/1236','123456')
        self.assertEqual([len(p['slots']) for p in (first,second,third)],[3,1,1])
        self.assertEqual(len(store.posts(group='123456')),3)
        self.assertFalse(self.browser.last_error)
        self.assertTrue(all((store.DATA/p['original']).exists() for p in (first,second,third)))
