"""Long multi-photo posts retain every toolbar pixel before/after translation."""
import asyncio
import base64
import io
import math
import unittest
from PIL import Image, ImageChops
import test_workflow as workflow
import test_preview7 as previous
from weverse_bot import store
from weverse_bot.capture_layout import measure_card, screenshot_card
from weverse_bot.translations import render_body


def photo(color, height):
    out=io.BytesIO();Image.new('RGB',(388,height),color).save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()


class MultiPhotoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.BrowserTests.asyncSetUp(self)
        store.save_watermark('',{'enabled':False})
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def capture(self, count, scale=1, suspended_frames=False):
        await self.page.set_content(previous.STYLE+'''<style>
          .post{padding-bottom:0;display:flex;flex-direction:column}
          .photos img{display:block;width:100%;height:auto;margin-bottom:9.25px}
          .icon-_-icon{display:inline-block}.icon-_-icon svg{width:22px;height:22px;display:block}
          .toolbar-_-container{max-height:12px;overflow:hidden}
        </style><article class="post"><div class="artist">EUNHO</div><p class="text">같이 빠지갈래?🙂</p>
        <div class="photos"></div></article>'''+previous.TOOLBAR)
        await self.page.locator('.photos').evaluate('(el,photos)=>{for(const src of photos){const img=document.createElement("img");img.loading="lazy";img.src=src;el.append(img)}}',
                [photo((180,20+i*20,80),551+i*43) for i in range(count)])
        # Paint the full icon viewBox; even a 1px cut now loses known pixels.
        await self.page.locator('.toolbar-_-container svg').evaluate_all('''nodes=>nodes.forEach(n=>{
            const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
            r.setAttribute('width','24');r.setAttribute('height','24');r.setAttribute('fill','#1942de');n.append(r);
        })''')
        context=await self.page.context.browser.new_context(viewport={'width':420,'height':1000},device_scale_factor=scale)
        try:
            page=await context.new_page()
            await page.set_content(await self.page.content())
            if suspended_frames:
                await page.evaluate('window.requestAnimationFrame=()=>0')
            post=await self.browser.extract(page,'https://weverse.io/plave/artist/1234','123456',self.cfg|{'comment_selector':'','capture_artist_comments':False,'max_scrolls':1})
            root=await page.locator('.post').bounding_box()
            bar=await page.locator('[data-wvbot-owned-toolbar] .toolbar-_-container').bounding_box()
            icons=await page.locator('[data-wvbot-owned-toolbar] svg').evaluate_all('ns=>ns.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})')
            media=await page.locator('.photos img').evaluate_all('ns=>ns.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})')
            self.assertEqual(await page.locator('[data-wvbot-owned-toolbar] button').all_inner_texts(),['10K+','2.1K',''])
            self.assertEqual(await page.locator('[data-wvbot-owned-toolbar]').count(),1)
        finally:await context.close()
        return post,root,bar,icons,media

    async def test_zero_one_two_and_five_photos_at_both_pixel_scales(self):
        for scale in (1,2):
            for count in (0,1,2,5):
                with self.subTest(scale=scale,photos=count):
                    post,root,bar,icons,media=await self.capture(count,scale)
                    image=Image.open(store.DATA/post['original']).convert('RGB')
                    self.assertEqual(image.width,420*scale)
                    self.assertGreaterEqual(image.height,math.floor((bar['y']+bar['height']-root['y']+15)*scale))
                    for icon in icons:
                        self.assertAlmostEqual(icon['height'],20)
                        x=round((icon['x']-root['x']+icon['width']/2)*scale)
                        start=math.ceil((icon['y']-root['y'])*scale)
                        end=math.floor((icon['y']+icon['height']-root['y'])*scale)
                        self.assertGreaterEqual(end-start,19*scale)
                        for y in range(start,end):self.assertEqual(image.getpixel((x,y)),(25,66,222))
                    for i,item in enumerate(media):
                        self.assertGreaterEqual(bar['y'],item['y']+item['height'])
                        x=round((item['x']-root['x']+item['width']/2)*scale)
                        for offset in (1,item['height']-1):
                            self.assertEqual(image.getpixel((x,round((item['y']-root['y']+offset)*scale))),(180,20+i*20,80))
                    if count==2 and scale==1:image.save('/tmp/plave-preview8-multi-original.png')

    async def test_translation_skip_and_bottom_watermark_preserve_photos_and_toolbar(self):
        post,root,bar,icons,media=await self.capture(2)
        before=Image.open(store.DATA/post['original']).convert('RGB')
        skip=await asyncio.to_thread(render_body,post['id'],'/k')
        skipped=Image.open(store.DATA/skip['output']).convert('RGB')
        self.assertEqual(before.tobytes(),skipped.tobytes())
        baked=await asyncio.to_thread(render_body,post['id'],'要去冲浪吗？/e\n第二行译文')
        after=Image.open(store.DATA/baked['output']).convert('RGB')
        delta=after.height-before.height;y=post['slots'][0]['y']
        self.assertGreater(delta,0)
        self.assertEqual(before.crop((0,y,420,before.height)).tobytes(),after.crop((0,y+delta,420,after.height)).tobytes())
        store.save_watermark('',{'enabled':True,'position':8,'text':'@PLAVE_PixelDiary','font_size':20,'outline':True,'outline_width':1})
        marked=await asyncio.to_thread(render_body,post['id'],'要去冲浪吗？/e\n第二行译文')
        watermark=Image.open(store.DATA/marked['output']).convert('RGB')
        toolbar_end=math.ceil(bar['y']+bar['height']-root['y']+delta)
        self.assertEqual(after.crop((0,0,420,toolbar_end)).tobytes(),watermark.crop((0,0,420,toolbar_end)).tobytes())
        difference=ImageChops.difference(after,watermark.crop((0,0,420,after.height))).getbbox()
        if difference:self.assertGreaterEqual(difference[1],toolbar_end)
        watermark.save('/tmp/plave-preview8-multi-translated-watermark.png')

    async def test_small_visible_svg_overflow_is_captured_instead_of_padded_over(self):
        await self.page.set_content('''<style>body{margin:0}.post{width:420px;background:white}.text{margin:0;height:20px}
            .toolbar-_-container{height:20px}svg{display:block}</style><article class="post">
            <p class="text">原文</p><div class="toolbar-_-container"><svg width="24" height="22"><rect width="24" height="22" fill="#bc1428"/></svg></div></article>''')
        root=self.page.locator('.post');bounds=await measure_card(root,'.text')
        self.assertEqual(bounds['content_bottom']-bounds['h'],2)
        image=Image.open(io.BytesIO(await screenshot_card(self.page,bounds,root))).convert('RGB')
        self.assertEqual(sum(image.getpixel((10,y))==(188,20,40) for y in range(image.height)),22)

    async def test_background_browser_with_suspended_frames_still_finishes_capture(self):
        post,root,bar,icons,media=await asyncio.wait_for(self.capture(2,suspended_frames=True),timeout=15)
        image=Image.open(store.DATA/post['original'])
        self.assertGreaterEqual(image.height,bar['y']+bar['height']-root['y']+15)
