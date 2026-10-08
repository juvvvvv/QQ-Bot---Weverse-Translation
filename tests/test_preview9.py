"""An isolated footer survives clipping of ancestors, icons and count labels."""
import asyncio
import io
import unittest
from PIL import Image
import test_workflow as workflow
import test_preview7 as original_samples
import test_preview8 as photos
import test_v3 as comments
from weverse_bot import store
from weverse_bot.capture_layout import measure_card,screenshot_card
from weverse_bot.post_adapter import place_post_toolbar
from weverse_bot.post_toolbar import toolbar_model
from weverse_bot.translations import render_body


class IsolatedFooterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.BrowserTests.asyncSetUp(self)
        store.save_watermark('',{'enabled':False})
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def fixture(self,count=2,kind='ancestor'):
        await self.page.set_content(original_samples.STYLE+'''<style>
            .photos img{display:block;width:100%;height:auto;margin-bottom:8px}
            </style><article class="post"><div class="artist">EUNHO</div>
            <p class="text">같이 빠지갈래?🙂</p><div class="photos"></div></article>'''+original_samples.TOOLBAR)
        await self.page.locator('.photos').evaluate('(el,photos)=>{for(const src of photos){const img=document.createElement("img");img.src=src;el.append(img)}}',
            [photos.photo((180,20+i*20,80),530+i*40) for i in range(count)])
        await self.page.locator('.toolbar-_-container svg').evaluate_all('''nodes=>nodes.forEach(n=>{
            const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
            r.setAttribute('width','24');r.setAttribute('height','24');r.setAttribute('fill','#1942de');n.append(r);
        })''')
        await place_post_toolbar(self.page,self.page.locator('.post'))
        if kind=='ancestor':
            await self.page.locator('.post').evaluate('''el=>{
                const wrap=document.createElement('div'),root=el.getBoundingClientRect(),bar=el.querySelector('[data-wvbot-owned-toolbar]').getBoundingClientRect();
                wrap.style.cssText=`height:${bar.top-root.top+8}px;width:420px;overflow:hidden`;
                el.before(wrap);wrap.append(el);
            }''')
        elif kind=='root':
            await self.page.locator('.post').evaluate("el=>el.style.clipPath='inset(0 0 28px 0)'")
        elif kind=='toolbar':
            await self.page.locator('[data-wvbot-owned-toolbar]').evaluate("el=>el.style.clipPath='inset(0 0 12px 0)'")
        elif kind=='caption':
            await self.page.locator('[data-wvbot-owned-toolbar] button').evaluate_all('''buttons=>buttons.forEach(button=>{
                for(const node of [...button.childNodes])if(node.nodeType===3 && node.textContent.trim()){
                    const span=document.createElement('span');span.textContent=node.textContent;
                    span.style.clipPath='inset(0 0 50% 0)';node.replaceWith(span);
                }
            })''')

    async def extract(self):
        return await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'capture_artist_comments':False,'comment_selector':'','max_scrolls':1})

    def assert_complete(self,post):
        image=Image.open(store.DATA/post['original']).convert('RGB')
        layout=post['slots'][0]['toolbar_layout']
        self.assertEqual(layout['texts_display'],['10K+','2.1K',''])
        self.assertEqual(len(layout['icons']),3)
        for rect in layout['icons']:
            x=rect['x']+rect['width']//2
            rows=range(layout['top']+rect['y'],layout['top']+rect['y']+rect['height'])
            for y in rows:self.assertEqual(image.getpixel((x,y)),(25,66,222))
            self.assertGreaterEqual(image.height-(layout['top']+rect['y']+rect['height']),16)
        for rect in layout['texts']:
            self.assertGreaterEqual(image.height-(layout['top']+rect['y']+rect['height']),16)
        return image,layout

    async def test_ancestor_root_toolbar_and_caption_clipping_leave_all_footer_pixels(self):
        for count in (1,2,5):
            for kind in ('ancestor','root','toolbar','caption'):
                with self.subTest(photos=count,clipping=kind):
                    await self.fixture(count,kind)
                    post=await self.extract();image,layout=self.assert_complete(post)
                    media=await self.page.locator('.photos img').evaluate_all('ns=>ns.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})')
                    root=await self.page.locator('.post').bounding_box()
                    for i,rect in enumerate(media):
                        x=round(rect['x']-root['x']+rect['width']/2)
                        for offset in (1,rect['height']-1):
                            self.assertEqual(image.getpixel((x,round(rect['y']-root['y']+offset))),(180,20+i*20,80))
                    if count==2 and kind=='ancestor':image.save('/tmp/plave-preview9-clipped-ancestor-fixed.png')

    async def test_preview8_style_raw_capture_loses_icon_rows_in_clipped_ancestor(self):
        await self.fixture()
        root=self.page.locator('.post');bounds=await measure_card(root,'.text')
        before=Image.open(io.BytesIO(await screenshot_card(self.page,bounds,root))).convert('RGB')
        bar=await self.page.locator('[data-wvbot-owned-toolbar] svg').first.bounding_box()
        card=await root.bounding_box();x=round(bar['x']-card['x']+10);start=round(bar['y']-card['y'])
        self.assertLess(sum(before.getpixel((x,y))==(25,66,222) for y in range(start,min(start+20,before.height))),20)
        post=await self.extract();after,layout=self.assert_complete(post)
        before.save('/tmp/plave-preview9-clipped-ancestor-before.png')

    async def test_translate_skip_watermark_and_repeat_preserve_source_and_restore_styles(self):
        await self.fixture()
        stage=self.page.locator('[data-wvbot-owned-toolbar]');style=await stage.get_attribute('style')
        post=await self.extract();before,layout=self.assert_complete(post)
        self.assertEqual(await stage.get_attribute('style'),style)
        result=await asyncio.to_thread(render_body,post['id'],'要去冲浪吗？/e')
        after=Image.open(store.DATA/result['output']).convert('RGB');delta=after.height-before.height
        y=post['slots'][0]['y']
        self.assertEqual(before.crop((0,y,420,before.height)).tobytes(),after.crop((0,y+delta,420,after.height)).tobytes())
        result=await asyncio.to_thread(render_body,post['id'],'/k')
        skipped=Image.open(store.DATA/result['output']).convert('RGB');self.assertEqual(skipped.tobytes(),before.tobytes())
        repeated=await self.extract();self.assertEqual(await stage.count(),1)
        second,_=self.assert_complete(repeated);self.assertEqual(second.tobytes(),before.tobytes())
        store.save_watermark('',{'enabled':True,'position':8,'text':'@PLAVE_PixelDiary','font_size':20})
        result=await asyncio.to_thread(render_body,post['id'],'要去冲浪吗？/e')
        marked=Image.open(store.DATA/result['output']).convert('RGB')
        toolbar_end=layout['top']+max(r['y']+r['height'] for r in layout['icons']+layout['texts'])+delta
        self.assertEqual(after.crop((0,0,420,toolbar_end)).tobytes(),marked.crop((0,0,420,toolbar_end)).tobytes())
        marked.save('/tmp/plave-preview9-translated-watermark.png')

    async def test_footer_precedes_artist_comments_and_does_not_change_comment_models(self):
        await comments.NativeCommentTests.fixture(self)
        await self.page.locator('.comment-list-by-artists-_-comment_list').evaluate('el=>[...el.children].slice(2).forEach(n=>n.remove())')
        await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate("el=>el.textContent='2'")
        await self.page.add_style_tag(content=original_samples.STYLE.removeprefix('<style>').removesuffix('</style>'))
        await self.page.evaluate('(html)=>document.body.insertAdjacentHTML("beforeend",html)',original_samples.TOOLBAR)
        await place_post_toolbar(self.page,self.page.locator('.post'))
        await self.page.locator('[data-wvbot-owned-toolbar]').evaluate("el=>el.style.clipPath='inset(0 0 12px 0)'")
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'comment_selector':''})
        self.assertEqual(len(post['slots']),3)
        layout=post['slots'][0]['toolbar_layout']
        self.assertGreaterEqual(post['slots'][1]['fragment_top'],layout['top']+layout['height'])
        self.assertTrue(all(s['native_card'] for s in post['slots'][1:]))
        result=await asyncio.to_thread(render_body,post['id'],'正文\n+\n第一条评论\n+\n第二条评论')
        self.assertEqual(result['translations'],{'0':'正文','1':'第一条评论','2':'第二条评论'})

    async def test_toolbar_model_drops_handlers_scripts_and_resource_urls(self):
        await self.fixture(kind='toolbar')
        await self.page.locator('[data-wvbot-owned-toolbar] svg').first.evaluate('''el=>{
            el.setAttribute('onload','window.ran=true');
            el.insertAdjacentHTML('beforeend','<script>window.ran=true</script><image href="https://example.invalid/private.png"/>');
            el.querySelector('path').setAttribute('fill','url(https://example.invalid/paint.svg)');
        }''')
        model=await toolbar_model(self.page.locator('[data-wvbot-owned-toolbar]'))
        self.assertNotIn('onload',str(model));self.assertNotIn('window.ran',str(model));self.assertNotIn('example.invalid',str(model))
        self.assertEqual([tool['text'] for tool in model['tools']],['10K+','2.1K',''])
