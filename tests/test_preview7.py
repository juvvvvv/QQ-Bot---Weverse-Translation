"""Absent artist regions and the supplied external original-post toolbar."""
import asyncio
import io
import unittest
from pathlib import Path
from PIL import Image
import test_workflow as workflow
import test_v3 as native
import test_preview6 as fan_samples
from weverse_bot import store
from weverse_bot.artist_comments import collect_artist_comments
from weverse_bot.translations import render_body

FIXTURES=Path(__file__).with_name('fixtures')
TOOLBAR=(FIXTURES/'weverse-artist-action-bar.html').read_text()
STYLE='''<style>body{margin:0;font-family:Arial}.blind{display:none}.post{width:420px;padding:16px;box-sizing:border-box;background:white}
.text{font-size:14px;line-height:1.6}.media{height:700px;background:rgb(180,20,80)}
.community-artist-postId-_-action_bar_inner{position:fixed;bottom:0;left:0;width:420px;background:#fff}
.toolbar-_-container,.toolbar-_-left,.toolbar-_-right{display:flex}.toolbar-_-container{gap:20px}.toolbar-_-left{gap:16px}
.toolbar-_-button_text{color:rgb(25,66,222);font-size:14px}.icon-_-icon{display:inline-flex}.icon-_-icon svg{width:100%;height:100%}
</style>'''


class OriginalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):await workflow.BrowserTests.asyncSetUp(self);store.save_watermark('',{'enabled':False})
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def original(self):
        await self.page.set_content(STYLE+'<article class="post"><div class="artist">YEJUN</div><div class="community-artist-postId-_-translate"><button>查看翻译</button></div><p class="text">예쁜하루☺️</p><div class="media" role="img"></div></article>'+TOOLBAR)

    async def extract(self,**extra):
        return await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'comment_selector':'','comment_wait_seconds':6,'max_scrolls':1}|extra)

    async def test_absent_artist_and_normal_regions_allow_original_skip_and_translation(self):
        await self.original()
        post=await self.extract(comment_wait_seconds=5)
        self.assertEqual(len(post['slots']),1)
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],0)
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['source'],'absent-artist-region')
        self.assertFalse(await self.page.locator('.community-artist-postId-_-translate').is_visible())
        toolbar=self.page.locator('.post [data-wvbot-owned-toolbar]')
        self.assertEqual(await toolbar.locator('button').all_inner_texts(),['10K+','2.1K',''])
        media=await self.page.locator('.media').bounding_box();bar=await toolbar.bounding_box();root=await self.page.locator('.post').bounding_box()
        self.assertGreaterEqual(bar['y'],media['y']+media['height'])
        original=Image.open(store.DATA/post['original']).convert('RGB')
        self.assertEqual(original.width,420)
        self.assertGreaterEqual(original.height,bar['y']+bar['height']-root['y']+15)
        skip=await asyncio.to_thread(render_body,post['id'],'/k')
        skipped=Image.open(store.DATA/skip['output']).convert('RGB')
        self.assertEqual(original.size,skipped.size);self.assertEqual(original.tobytes(),skipped.tobytes())
        baked=await asyncio.to_thread(render_body,post['id'],'漂亮的一天/e')
        after=Image.open(store.DATA/baked['output']).convert('RGB')
        self.assertEqual(baked['translations'],{'0':'漂亮的一天☺️'})
        delta=after.height-original.height;self.assertGreater(delta,0)
        top=round(bar['y']-root['y']);bottom=round(bar['y']+bar['height']-root['y'])
        self.assertEqual(original.crop((0,top,420,bottom)).tobytes(),after.crop((0,top+delta,420,bottom+delta)).tobytes())
        original.save('/tmp/plave-preview7-original-only.png');after.save('/tmp/plave-preview7-original-translated.png')

    async def test_main_toolbar_precedes_artist_section_and_ui_labels_disappear(self):
        await native.NativeCommentTests.fixture(self)
        await self.page.locator('.comment-list-by-artists-_-comment_list').evaluate('el=>[...el.children].slice(2).forEach(n=>n.remove())')
        await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate("el=>el.textContent='2'")
        await self.page.add_style_tag(content=STYLE.removeprefix('<style>').removesuffix('</style>'))
        await self.page.locator('.post').evaluate("el=>el.insertAdjacentHTML('afterbegin','<div class=\"community-artist-postId-_-translate\"><button>See translation</button></div>')")
        await self.page.evaluate('(html)=>document.body.insertAdjacentHTML("beforeend",html)',TOOLBAR)
        post=await self.extract()
        self.assertEqual(len(post['slots']),3)
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],2)
        self.assertFalse(await self.page.locator('.community-artist-postId-_-translate').is_visible())
        root=await self.page.locator('.post').bounding_box();bar=await self.page.locator('.post [data-wvbot-owned-toolbar]').bounding_box()
        self.assertGreaterEqual(post['slots'][1]['fragment_top'],bar['y']+bar['height']-root['y'])
        before=Image.open(store.DATA/post['original']).convert('RGB')
        baked=await asyncio.to_thread(render_body,post['id'],'正文中文/e\n+\n第一条评论\n+\n第二条评论')
        after=Image.open(store.DATA/baked['output']).convert('RGB')
        main_added=after.height-before.height
        self.assertEqual(after.width,before.width)
        self.assertGreater(main_added,0)
        before.save('/tmp/plave-preview7-with-comments.png');after.save('/tmp/plave-preview7-with-comments-translated.png')

    async def test_fan_original_without_any_comment_region_is_one_segment(self):
        await fan_samples.PostTests.fan(self)
        await self.page.locator('.community-fanpost-postId-_-aside').evaluate('el=>el.remove()')
        post=await self.extract()
        self.assertEqual(len(post['slots']),1);self.assertEqual(post['slots'][0]['post_kind'],'fan')
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],0)
        self.assertFalse(await self.page.locator('.community-fanpost-postId-_-translate').is_visible())
        self.assertEqual(await self.page.locator('[data-wvbot-owned-toolbar]').count(),1)
        result=await asyncio.to_thread(render_body,post['id'],'粉丝正文中文')
        self.assertEqual(result['status'],'translated')

    async def test_late_artist_region_after_four_seconds_is_not_omitted(self):
        await native.NativeCommentTests.fixture(self)
        await self.page.evaluate('''()=>{
            const list=document.querySelector('.comment-list-by-artists-_-comment_list');
            [...list.children].slice(2).forEach(n=>n.remove());
            const header=document.querySelector('.base-comment-artist-count-and-toggle-_-container');
            header.querySelector('.base-comment-artist-count-and-toggle-_-count').textContent='2';
            list.remove();header.remove();setTimeout(()=>document.body.append(header,list),4200);
        }''')
        rows=await collect_artist_comments(self.page,self.cfg|{'comment_selector':'','comment_wait_seconds':7})
        self.assertEqual(len(rows),2)

    async def test_busy_panel_and_broken_artist_counter_still_stop(self):
        await self.original()
        await self.page.evaluate("()=>document.body.insertAdjacentHTML('beforeend','<aside class=\"community-artist-postId-_-aside\" aria-busy=\"true\">Loading</aside>')")
        with self.assertRaisesRegex(ValueError,'加载等待超时'):await self.extract(comment_wait_seconds=.6)
        await self.original()
        await self.page.evaluate("()=>document.body.insertAdjacentHTML('beforeend','<div class=\"base-comment-artist-count-and-toggle-_-container\">Artist comments</div>')")
        with self.assertRaisesRegex(ValueError,'未识别艺人评论计数'):await self.extract(comment_wait_seconds=.6)
        self.assertEqual(store.posts(),[])

    async def test_inside_action_bar_is_copied_once_without_deleting_original_text(self):
        await self.original()
        await self.page.locator('.text').evaluate("el=>el.textContent='原文里写着查看翻译🙂'")
        await self.page.evaluate("()=>document.querySelector('.post').append(document.querySelector('.community-artist-postId-_-action_bar_inner'))")
        await self.page.locator('.community-artist-postId-_-action_bar_inner').evaluate("el=>el.style.cssText='height:90px;background:rgb(255,0,204)'")
        post=await self.extract(capture_artist_comments=False)
        self.assertEqual(post['slots'][0]['text'],'原文里写着查看翻译🙂')
        self.assertEqual(await self.page.locator('.post .toolbar-_-container:visible').count(),1)
        self.assertEqual(await self.page.locator('[data-wvbot-owned-toolbar]').count(),1)
        self.assertFalse(await self.page.locator('.community-artist-postId-_-action_bar_inner').is_visible())
        image=Image.open(store.DATA/post['original']).convert('RGB')
        self.assertNotIn((255,0,204),set(image.getdata()))

    async def test_empty_artist_title_and_list_without_counter_do_not_block_original(self):
        await self.original()
        await self.page.evaluate("()=>document.body.insertAdjacentHTML('beforeend','<div class=\"base-comment-artist-count-and-toggle-_-container\">Artist comments</div><div class=\"comment-list-by-artists-_-comment_list\"></div>')")
        post=await self.extract()
        self.assertEqual(len(post['slots']),1)
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],0)
