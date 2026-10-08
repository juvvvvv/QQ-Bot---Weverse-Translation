"""Actual anchor DOM, outside overlays, and native original/translation reflow."""
import asyncio
import io
import unittest
from PIL import Image
import test_workflow as workflow
import test_v3 as fixtures
from weverse_bot import store
from weverse_bot.comment_image import render_comment
from weverse_bot.translations import render_body, latest_context
from weverse_bot.render import compose


class NativeLayoutTests(unittest.IsolatedAsyncioTestCase):
    fixture=fixtures.NativeCommentTests.fixture
    async def asyncSetUp(self):await workflow.BrowserTests.asyncSetUp(self);store.save_watermark('',{'enabled':False})
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def actual_dom(self):
        await self.fixture()
        # React builds nested links via DOM, unlike parsing a saved HTML string.
        await self.page.locator('snapshot-anchor').evaluate_all('''nodes=>nodes.forEach(n=>{const a=document.createElement('a');for(const x of n.attributes)a.setAttribute(x.name,x.value);a.append(...n.childNodes);n.replaceWith(a)})''')
        await self.page.locator('.comment-item-_-container').evaluate_all('''cards=>cards.forEach(card=>{
            const extra=document.createElement('div');extra.className='extra-toolbar';
            extra.innerHTML='<svg width="24" height="8"><rect width="24" height="8" fill="#ff00cc"/></svg>';
            card.append(extra);
            card.querySelector('.line-clamp-node-view-_-container').style.color='#bc1428';
        })''')
        await self.page.add_style_tag(content='''a.comment-item-_-container .comment-item-_-header_wrap{transform:translateY(5px)}
            a.comment-item-_-container .comment-item-_-text_area{overflow:hidden;max-height:180px}
            a.comment-item-_-container .comment-item-_-interaction::after{content:"x";position:absolute;bottom:-5px;color:#ff00cc}
            .extra-toolbar{position:relative;bottom:0}
            .line-clamp-node-view-_-container{max-height:24px;overflow:visible}''')

    async def test_actual_anchors_remove_extra_icons_and_keep_single_original_before_translation(self):
        await self.actual_dom()
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',self.cfg|{'comment_selector':''})
        self.assertEqual(len(post['slots']),15)
        original=Image.open(store.DATA/post['original']).convert('RGB')
        self.assertNotIn((255,0,204),set(original.getdata()))
        body='\n+\n'.join('/k' if i==0 else '完整的中文译文，不能遮住韩语。\n第二行中文🙂' for i in range(15))
        result=await asyncio.to_thread(render_body,post['id'],body)
        translated=Image.open(store.DATA/result['output']).convert('RGB')
        self.assertEqual(original.width,translated.width);self.assertGreater(translated.height,original.height)
        self.assertNotIn((255,0,204),set(translated.getdata()))
        for slot in post['slots'][1:]:
            model=slot['native_card']
            raw=await render_comment(self.page,420,model,translation='中文\n'*6)
            self.assertEqual(await self.page.locator('#original').inner_text(),slot['text'])
            self.assertEqual(await self.page.locator('#translation').inner_text(),('中文\n'*6))
            original_box=await self.page.locator('#original').bounding_box();chinese_box=await self.page.locator('#translation').bounding_box()
            self.assertGreaterEqual(chinese_box['y'],original_box['y']+original_box['height']+5)
            self.assertEqual(await self.page.get_by_text(slot['text'],exact=True).count(),1)
            self.assertEqual(await self.page.locator('#toolbar svg').count(),len([t for t in model['tools'] if t['icon']]))
            self.assertEqual(await self.page.locator('.extra-toolbar').count(),0)
        original.save('/tmp/plave-preview5-original.png');translated.save('/tmp/plave-preview5-translated.png')

    async def test_reply_connector_is_continuous_through_long_translation_at_both_scales(self):
        await self.actual_dom()
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',self.cfg|{'comment_selector':''})
        reply=next(s for s in post['slots'] if s.get('is_reply'))
        for scale in (1,2):
            raw=await render_comment(self.page,420*scale,reply['native_card'],scale,'扩展的中文译文\n'*12)
            line=await self.page.locator('#connector').bounding_box()
            image=Image.open(io.BytesIO(raw)).convert('RGB')
            x=round(line['x'])
            # Every scanline, including the new Chinese paragraph, has the
            # same connector pixel; testing presence alone misses a split line.
            for y in range(round(line['y']),int(line['y']+line['height'])):
                self.assertEqual(image.getpixel((x,y)),(229,233,242),(scale,x,y))
            image.save(f'/tmp/plave-preview5-reply-{scale}x.png')

    async def test_main_toolbar_preserved_without_unrelated_fixed_footer_and_styles_restored(self):
        await self.page.set_content('''<style>body{margin:0}.post{width:420px;padding:16px;background:white;box-sizing:border-box}.text{font-size:14px}
            .media{height:970px;background:#ddd}.toolbar-_-container{height:32px}
            #outside{position:fixed;bottom:0;left:0;width:420px;height:100px;background:#ff00cc;z-index:900000}</style>
            <article class="post"><div class="artist">YEJUN</div><p class="text">原文</p><div class="media" role="img"></div>
            <div class="toolbar-_-container"><svg width="24" height="24"><rect width="24" height="24" fill="#bc1428"/></svg></div></article>
            <div id="outside" style="--restore-marker: retained; outline-width: 0px;">unrelated toolbar</div>''')
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'capture_artist_comments':False,'comment_selector':''})
        image=Image.open(store.DATA/post['original']).convert('RGB')
        colors=set(image.getdata());self.assertNotIn((255,0,204),colors);self.assertIn((188,20,40),colors)
        rows=[y for y in range(image.height) if image.getpixel((20,y))==(188,20,40)]
        self.assertEqual(len(rows),24)
        self.assertEqual(await self.page.locator('#outside').get_attribute('style'),'--restore-marker: retained; outline-width: 0px;')
        self.assertTrue(await self.page.locator('#outside').is_visible())

    async def test_skips_reuse_source_pixels_and_old_native_snapshots_require_fresh_read(self):
        await self.actual_dom()
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',self.cfg|{'comment_selector':''})
        old=Image.open(store.DATA/post['original']).convert('RGB')
        result=await asyncio.to_thread(render_body,post['id'],'\n+\n'.join('/k' for s in post['slots']))
        new=Image.open(store.DATA/result['output']).convert('RGB')
        self.assertEqual(new.size,old.size);self.assertEqual(new.tobytes(),old.tobytes())
        fresh=await self.browser.extract(self.page,post['url'],'123456',self.cfg|{'comment_selector':''})
        self.assertEqual(len(latest_context(fresh)[1]),15)
        legacy=[dict(s) for s in fresh['slots']]
        for s in legacy:s.pop('native_card',None)
        with self.assertRaisesRegex(ValueError,'重新读取网址'):
            await asyncio.to_thread(compose,old,legacy,{'0':'译文'},{'enabled':False})
