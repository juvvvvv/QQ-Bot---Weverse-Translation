"""Regression cases for real-browser rejection failures and stale UI sessions."""
import unittest
from fastapi.testclient import TestClient
import test_workflow as workflow
from weverse_bot.app import app
from weverse_bot.page_cleanup import reject_optional_consent
from test_comment_loading import COOKIE


class HomeSessionTests(unittest.TestCase):
    def test_home_always_refreshes_the_local_session_even_with_old_validators(self):
        workflow.reset()
        with TestClient(app) as client:
            first = client.get('/')
            self.assertEqual(first.headers['cache-control'], 'no-store')
            client.cookies.set('wv_session', 'previous-process-session', domain='testserver.local', path='/')
            self.assertEqual(client.get('/api/session').status_code, 401)
            home = client.get('/', headers={'If-None-Match': first.headers['etag'],
                                           'If-Modified-Since': first.headers['last-modified']})
            self.assertEqual(home.status_code, 200)
            self.assertEqual(home.headers['cache-control'], 'no-store')
            self.assertIn('wv_session=', home.headers['set-cookie'])
            session = client.get('/api/session')
            self.assertEqual(session.status_code, 200)
            saved = client.put('/api/settings', headers={'x-wv-csrf': session.json()['csrf']},
                               json={'changes': {'comment_wait_seconds': 60}})
            self.assertEqual(saved.status_code, 200)


class RejectionRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.BrowserTests.asyncSetUp(self)
    async def asyncTearDown(self):
        await workflow.BrowserTests.asyncTearDown(self)

    async def test_persistent_fixed_app_shell_is_not_mistaken_for_the_dialog(self):
        await self.page.set_content('''<div id="app" style="position:fixed;inset:0;background:white"><main>
        <article>Original artist content</article>
        <div role="dialog"><p>Weverse asks for your consent</p>
        <button onclick="window.choice='reject';this.parentElement.remove()">Do not consent</button>
        </div></main></div>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')
        self.assertTrue(await self.page.locator('#app').is_visible())

    async def test_plain_prompt_inside_persistent_fixed_app_shell(self):
        await self.page.set_content('''<div id="app" style="position:fixed;inset:0">
        <article>Original artist content</article>
        <section><p>Weverse asks for your consent</p>
        <button onclick="window.choice='reject';this.parentElement.remove()">Do not consent</button>
        </section></div>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertTrue(await self.page.locator('#app').is_visible())

    async def test_delayed_close_longer_than_old_five_second_limit(self):
        await self.page.set_content('''<div role="dialog"><p>Weverse asks for your consent</p>
        <button onclick="window.choice='reject';this.disabled=true;setTimeout(()=>this.parentElement.remove(),5400)">Do not consent</button>
        <button onclick="window.choice='accept'">Consent</button></div>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')
        self.assertEqual(await self.page.locator('[role=dialog]').count(), 0)

    async def test_temporary_cover_retries_normal_click_without_forcing(self):
        await self.page.set_content('''<div role="dialog"><p>Weverse asks for your consent</p>
        <button onclick="window.choice='reject';this.parentElement.remove()">Do not consent</button></div>
        <div id="animation" style="position:fixed;inset:0;z-index:50000;background:white"></div>
        <script>setTimeout(()=>document.querySelector('#animation').remove(),2200)</script>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')

    async def test_disabled_button_becomes_ready_after_old_click_limit(self):
        await self.page.set_content('''<div role="dialog"><p>Weverse asks for your consent</p>
        <button disabled onclick="window.choice='reject';this.parentElement.remove()">Do not consent</button></div>
        <script>setTimeout(()=>document.querySelector('button').disabled=false,3400)</script>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')

    async def test_hidden_iframe_is_closed_even_though_its_dom_remains(self):
        await self.page.set_content('<iframe style="position:fixed;inset:0;background:white"></iframe>')
        frame = await (await self.page.locator('iframe').element_handle()).content_frame()
        await frame.set_content('''<p>Weverse asks for your consent</p>
        <button onclick="parent.choice='reject';frameElement.style.display='none'">Do not consent</button>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertFalse(await self.page.locator('iframe').is_visible())
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')
        self.assertFalse(await reject_optional_consent(self.page))

    async def test_hidden_cmp_iframe_does_not_block_rejecting_bottom_cookie(self):
        await self.page.set_content(COOKIE + '<iframe style="position:fixed;inset:0;width:100%;height:100%;z-index:40000;background:white"></iframe>')
        frame = await (await self.page.locator('iframe').element_handle()).content_frame()
        await frame.set_content('''<p>Weverse asks for your consent</p>
        <button onclick="parent.cmp='reject';frameElement.style.display='none'">Do not consent</button>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('[window.cmp,window.choice]'), ['reject', 'reject'])

    async def test_identical_buttons_are_checked_by_element_not_mutating_nth(self):
        await self.page.set_content('''<div role="dialog"><p>Weverse asks for your consent</p>
        <button onclick="window.first='reject';this.parentElement.remove()">Do not consent</button></div>
        <div role="dialog"><p>Weverse asks for your consent</p>
        <button onclick="window.second='reject';this.parentElement.remove()">Do not consent</button></div>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('[window.first,window.second]'), ['reject', 'reject'])

    async def test_bottom_cookie_disabled_reject_still_never_selects_optional(self):
        await self.page.set_content(COOKIE)
        await self.page.get_by_role('button', name='不同意并继续', exact=True).evaluate('el=>{el.disabled=true;setTimeout(()=>el.disabled=false,2100)}')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')

    async def test_clipped_desktop_cmp_is_rejected_and_capture_width_restored(self):
        await self.page.set_viewport_size({'width': 480, 'height': 600})
        await self.page.set_content('''<style>html,body{overflow:hidden}
        [role=dialog]{position:fixed;left:20px;top:20px;width:1000px;background:white}
        button{position:absolute;left:750px;top:100px}</style>
        <div role="dialog"><p>Weverse asks for your consent</p>
        <button onclick="window.choice='reject';this.parentElement.remove()">Do not consent</button></div>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')
        self.assertEqual(self.page.viewport_size, {'width': 480, 'height': 600})

    async def test_remaining_dedicated_backdrop_stops_capture(self):
        await self.page.set_content('''<div id="backdrop" style="position:fixed;inset:0;background:rgba(0,0,0,.7)">
        <div role="dialog"><p>Weverse asks for your consent</p>
        <button onclick="window.choice='reject';this.parentElement.remove()">Do not consent</button></div></div>''')
        with self.assertRaisesRegex(ValueError, '关闭弹窗失败'):
            await reject_optional_consent(self.page)
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')
        self.assertTrue(await self.page.locator('#backdrop').is_visible())

    async def test_first_click_before_site_handler_is_ready_is_retried(self):
        await self.page.set_content('''<div role="dialog"><p>Weverse asks for your consent</p>
        <button>Do not consent</button></div>
        <script>setTimeout(()=>document.querySelector('button').onclick=function(){
            window.choice='reject';this.parentElement.remove();
        },1200)</script>''')
        self.assertTrue(await reject_optional_consent(self.page))
        self.assertEqual(await self.page.evaluate('window.choice'), 'reject')
