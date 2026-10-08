"""Regression cases for the real delayed artist list and Cookie bar reports."""
import asyncio
import unittest
from fastapi.testclient import TestClient
import test_workflow as workflow
import test_v3 as fixtures
from weverse_bot import store
from weverse_bot.app import app
from weverse_bot.artist_comments import collect_artist_comments, machine_time, order_comments
from weverse_bot.capture import weverse_url
from weverse_bot.page_cleanup import reject_optional_consent
from weverse_bot.translations import render_body


COOKIE = '''<div class="_w_bottom_fixed_CHANGED_4" style="position:fixed;bottom:0;left:0;width:100%;z-index:30000;background:#222;color:white;padding:12px">
<div class="_w_container_CHANGED_11"><div class="_w_content_CHANGED_15">
<p class="_w_notice_CHANGED_24">为支持自动登录并体验更加便捷的Weverse服务，请同意收集Cookie。
<a href="https://weverse.io/policies/cookie">查看详情</a></p>
<div class="_w_button_wrap_CHANGED_71">
<button class="_w_button_continue_CHANGED_29" onclick="window.choice='reject';this.closest('[class*=\\'_w_bottom_fixed_\\']').remove()">不同意并继续</button>
<button class="_w_button_deny_CHANGED_38" onclick="window.choice='optional'">可选同意</button>
<button class="_w_button_allow_CHANGED_49" onclick="window.choice='all'">全部同意</button>
</div></div></div></div>'''


class OrderingTests(unittest.TestCase):
    def test_language_independent_source_order_and_same_minute(self):
        for displays in (['Sep 30, 03:49','Sep 30, 03:49','Sep 30, 03:13'],
                         ['09. 30. 03:49','09. 30. 03:49','09. 30. 03:13'],
                         ['9月30日 03:49','9月30日 03:49','9月30日 03:13'],
                         ['unknown locale A','unknown locale B','unknown locale C']):
            rows=[{'comment_id':str(i),'published':v,'source_index':i,'is_reply':False}
                  for i,v in enumerate(displays)]
            self.assertEqual([r['comment_id'] for r in order_comments(rows)],['2','1','0'])

    def test_machine_dates_override_dom_and_handle_year_boundary(self):
        rows=[{'comment_id':'old','timestamp':'2025-12-31T23:59:00Z','source_index':0,'is_reply':False},
              {'comment_id':'new','timestamp':'2026-01-01T00:01:00Z','source_index':1,'is_reply':False}]
        self.assertEqual([r['comment_id'] for r in order_comments(rows)],['old','new'])
        self.assertEqual(machine_time('1767225660000'),machine_time('2026-01-01T00:01:00Z'))
        self.assertIsNone(machine_time('Sep 30, 03:49'))

    def test_url_language_and_share_parameters_are_still_removed(self):
        url='https://weverse.io/plave/artist/3-241901329'
        self.assertEqual(weverse_url(url+'?hl=en&xsec_token=example'),url)
        self.assertEqual(weverse_url(url+'?hl=zh-cn'),url)

    def test_wait_setting_is_saved_and_validated(self):
        workflow.reset()
        with TestClient(app) as client:
            client.get('/');headers={'x-wv-csrf':client.get('/api/session').json()['csrf']}
            self.assertEqual(client.get('/api/settings').json()['comment_wait_seconds'],30)
            self.assertEqual(client.put('/api/settings',headers=headers,json={'changes':{'comment_wait_seconds':60}}).status_code,200)
            for value in (0,121,True):
                self.assertEqual(client.put('/api/settings',headers=headers,json={'changes':{'comment_wait_seconds':value}}).status_code,400)


class LoadingTests(unittest.IsolatedAsyncioTestCase):
    fixture=fixtures.NativeCommentTests.fixture
    async def asyncSetUp(self):await workflow.BrowserTests.asyncSetUp(self)
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def delay_region(self, milliseconds, missing_count=False):
        await self.fixture()
        await self.page.evaluate('''({delay,missingCount})=>{
            const list=document.querySelector('.comment-list-by-artists-_-comment_list');
            const header=document.querySelector('.base-comment-artist-count-and-toggle-_-container');
            list.remove();header.remove();
            setTimeout(()=>{document.body.append(header,list);if(missingCount)header.querySelector('.base-comment-artist-count-and-toggle-_-count').remove();},delay);
        }''',{'delay':milliseconds,'missingCount':missing_count})

    async def test_delayed_header_and_list_produce_all_14_comments(self):
        await self.delay_region(2500)
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',
                                        self.cfg|{'comment_selector':'','comment_wait_seconds':8})
        self.assertEqual(len(post['slots']),15)
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],14)

    async def test_partial_list_waits_for_the_remaining_comments(self):
        await self.fixture()
        await self.page.evaluate('''()=>{
            const list=document.querySelector('.comment-list-by-artists-_-comment_list');
            const missing=[...list.children].slice(7);missing.forEach(n=>n.remove());
            setTimeout(()=>missing.forEach(n=>list.append(n)),1200);
        }''')
        rows=await collect_artist_comments(self.page,self.cfg|{'comment_selector':'','comment_wait_seconds':5})
        self.assertEqual(len(rows),14)

    async def test_unknown_count_times_out_and_preserves_successful_archive(self):
        old=fixtures.draft();saved=await asyncio.to_thread(render_body,old['id'],'正文\n+\n评论')
        await self.page.locator('.comment').evaluate_all('nodes=>nodes.forEach(n=>n.remove())')
        with self.assertRaisesRegex(ValueError,'加载等待超时.*未识别'):
            await self.browser.extract(self.page,old['url'],'123456',self.cfg|{'comment_selector':'','comment_wait_seconds':.6})
        self.assertEqual(store.latest_rendered(old['url'],'123456')['id'],old['id'])
        self.assertTrue((store.DATA/saved['output']).exists())
        self.assertEqual(len(store.posts()),1)

    async def test_unknown_counter_is_not_accepted_even_with_visible_list(self):
        await self.fixture()
        await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate('el=>el.remove()')
        with self.assertRaisesRegex(ValueError,'未识别艺人评论计数'):
            await collect_artist_comments(self.page,self.cfg|{'comment_selector':'','comment_wait_seconds':.6})

    async def test_explicit_zero_is_distinct_from_missing_and_transient_zero(self):
        await self.fixture()
        await self.page.evaluate('''()=>{
            const list=document.querySelector('.comment-list-by-artists-_-comment_list');
            const counter=document.querySelector('.base-comment-artist-count-and-toggle-_-count');
            list.remove();counter.textContent='0';
            setTimeout(()=>{counter.textContent='14';document.body.append(list);},400);
        }''')
        self.assertEqual(len(await collect_artist_comments(self.page,self.cfg|{'comment_selector':''})),14)
        await self.page.locator('.comment-list-by-artists-_-comment_list').evaluate('el=>el.remove()')
        await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate("el=>el.textContent='0'")
        self.assertEqual(await collect_artist_comments(self.page,self.cfg|{'comment_selector':''}),[])

    async def test_cookie_bar_clicks_continue_not_optional_and_disappears(self):
        await self.page.set_content(COOKIE)
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'),'reject')
        self.assertEqual(await self.page.locator('[class*="_w_bottom_fixed_"]').count(),0)

    async def test_approximate_zero_is_not_treated_as_no_comments(self):
        await self.fixture()
        await self.page.locator('.comment-list-by-artists-_-comment_list').evaluate('el=>el.remove()')
        await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate("el=>el.textContent='0K'")
        with self.assertRaisesRegex(ValueError,'近似值'):
            await collect_artist_comments(self.page,self.cfg|{'comment_selector':'','comment_wait_seconds':.6})

    async def test_cookie_and_cmp_are_both_rejected(self):
        await self.page.set_content(COOKIE+'''<div role="dialog" style="position:fixed;inset:0;z-index:40000;background:white">
        <p>Weverse asks for your consent</p><button onclick="window.cmp='reject';this.parentElement.remove()">Do not consent</button></div>''')
        await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('[window.choice,window.cmp]'),['reject','reject'])

    async def test_cmp_iframe_is_rejected_before_the_cookie_bar_it_covers(self):
        await self.page.set_content(COOKIE+'''<iframe style="position:fixed;inset:0;width:100%;height:100%;z-index:40000;background:white"></iframe>''')
        frame=await (await self.page.locator('iframe').element_handle()).content_frame()
        await frame.set_content('''<p>Weverse asks for your consent</p>
        <button onclick="parent.cmp='reject';frameElement.remove()">Do not consent</button>''')
        await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('[window.choice,window.cmp]'),['reject','reject'])

    async def test_cookie_without_reject_is_never_accepted_or_hidden(self):
        await self.page.set_content(COOKIE)
        await self.page.get_by_role('button',name='不同意并继续',exact=True).evaluate('el=>el.remove()')
        with self.assertRaisesRegex(ValueError,'未找到明确的拒绝按钮'):
            await reject_optional_consent(self.page)
        self.assertIsNone(await self.page.evaluate('window.choice'))
        self.assertTrue(await self.page.locator('[class*="_w_bottom_fixed_"]').is_visible())

    async def test_english_cookie_bar_and_iframe_use_the_same_policy_region(self):
        await self.page.set_content('<iframe></iframe>')
        frame=await (await self.page.locator('iframe').element_handle()).content_frame()
        await frame.set_content(COOKIE.replace('不同意并继续','Disagree and continue'))
        await reject_optional_consent(self.page)
        self.assertEqual(await frame.evaluate('window.choice'),'reject')

    async def test_delayed_cookie_is_rejected_during_comment_loading(self):
        await self.delay_region(1500)
        await self.page.evaluate('''html=>setTimeout(()=>{
            const holder=document.createElement('div');holder.innerHTML=html;document.body.append(holder.firstElementChild);
        },200)''',COOKIE)
        self.assertEqual(len(await collect_artist_comments(self.page,self.cfg|{'comment_selector':''})),14)
        self.assertEqual(await self.page.evaluate('window.choice'),'reject')

    async def test_english_comment_timestamps_keep_chronology(self):
        await self.fixture()
        await self.page.locator('.comment-item-header-_-time').evaluate_all("nodes=>nodes.forEach(n=>n.textContent=n.textContent.replace('09. 30.','Sep 30,'))")
        rows=await collect_artist_comments(self.page,self.cfg|{'comment_selector':''})
        self.assertEqual(rows[0]['published'],'Sep 30, 03:13')
        self.assertEqual(rows[-1]['comment_id'],'3-511578371')

    async def test_cookie_bar_does_not_cover_captured_media(self):
        from PIL import Image
        await self.page.set_content('''<style>body{margin:0}.post{width:620px;padding:16px;background:white;box-sizing:border-box}
        .media{height:1200px;background:rgb(160,20,80)}</style><article class="post">
        <span class="artist">YEJUN</span><p class="text">原文🙂</p><div class="media" role="img"></div></article>'''+COOKIE)
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',
                                       self.cfg|{'capture_artist_comments':False,'comment_selector':''})
        image=Image.open(store.DATA/post['original']).convert('RGB')
        self.assertEqual(await self.page.evaluate('window.choice'),'reject')
        self.assertGreater(image.height,1000)
        for y in range(200,image.height-30,100):
            self.assertEqual(image.getpixel((300,y)),(160,20,80))

    async def test_site_written_cookie_rejection_survives_browser_restart(self):
        html=COOKIE.replace("window.choice='reject';", "window.choice='reject';document.cookie='test_cookie_choice=denied; Path=/; Max-Age=86400';")
        html += "<script>if(document.cookie.includes('test_cookie_choice=denied'))document.querySelector('[class*=\"_w_bottom_fixed_\"]').remove();</script>"
        url='https://weverse.io/plave/artist/1234'
        async def serve(route):
            await route.fulfill(content_type='text/html; charset=utf-8',body=html)
        await self.page.route(url,serve);await self.page.goto(url)
        await reject_optional_consent(self.page)
        self.assertIn('test_cookie_choice=denied',await self.page.evaluate('document.cookie'))
        await self.browser.close()
        context=await self.browser.open();self.page=await context.new_page()
        await self.page.route(url,serve);await self.page.goto(url)
        self.assertIn('test_cookie_choice=denied',await self.page.evaluate('document.cookie'))
        self.assertEqual(await self.page.locator('[class*="_w_bottom_fixed_"]').count(),0)
