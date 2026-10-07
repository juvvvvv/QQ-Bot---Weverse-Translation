import asyncio
import base64
import io
import json
import unittest
from PIL import Image, ImageChops, ImageOps
from fastapi.testclient import TestClient
import test_workflow as workflow
from weverse_bot import store
from weverse_bot.app import app
from weverse_bot.capture_layout import parse_count
from weverse_bot.render import compose, render_post
from weverse_bot.watermark import apply_text, text_job


def png_url(color, size):
    image = Image.new('RGB', size, color)
    data = io.BytesIO(); image.save(data, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()


def card_html(text='사랑하는 여러분\n예쁜하루☺️', min_height=600, photo=False, counters=''):
    media = f'<img class="photo" src="{png_url("white", (388, 200))}">' if photo else ''
    return f'''<style>body{{margin:0}}.post{{width:420px;box-sizing:border-box;padding:16px;background:white;min-height:{min_height}px}}
    .header{{height:40px}}.avatar{{width:32px;height:32px;vertical-align:top}}.artist{{display:inline-block}}
    .text{{font-size:14px;line-height:1.6;white-space:pre-wrap;margin:16px 0}}
    .photo{{display:block;width:388px;height:200px}}</style><article class="post">
    <div class="header"><img class="avatar" src="{png_url('orange',(32,32))}"><span class="artist">EUNHO</span></div>
    <p class="text">{text}</p>{media}</article>{counters}'''


class CaptureLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.BrowserTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await workflow.BrowserTests.asyncTearDown(self)

    async def capture_html(self, html):
        await self.page.set_content(html)
        return await self.browser.extract(self.page,'https://weverse.io/plave/artist/4-241905010','123456',self.cfg|{'comment_selector':''})

    async def test_bottom_spacing_adapts_to_text_length_without_fixed_height(self):
        store.save_watermark('', {'enabled':False})
        heights=[]
        for text in ('예쁜하루☺️', '원문\n두번째 줄\n세번째 줄\n마지막 줄🙂'):
            post=await self.capture_html(card_html(text, min_height=900))
            slot=post['slots'][0]
            self.assertTrue(slot['trailing_text'])
            self.assertEqual(slot['bottom_padding'],16)
            translated=await asyncio.to_thread(render_post,post['id'],{'0':'漂亮的一天🙂\n这是第二行'})
            with Image.open(store.DATA/translated['output']) as image:
                ink=ImageOps.invert(image.convert('RGB')).getbbox()
                self.assertEqual(ink[1],16)
                self.assertEqual(image.height-ink[3],16)
                self.assertLess(image.height,300)
                heights.append(image.height)
        self.assertGreater(heights[1],heights[0])

    async def test_all_white_photo_is_retained_by_geometry(self):
        post=await self.capture_html(card_html(photo=True))
        slot=post['slots'][0]
        self.assertFalse(slot['trailing_text'])
        photo=await self.page.locator('.photo').bounding_box()
        card=await self.page.locator('.post').bounding_box()
        with Image.open(store.DATA/post['original']) as image:
            self.assertAlmostEqual(image.height,photo['y']+photo['height']-card['y']+16,delta=1)
            self.assertEqual(image.width,420)

    async def test_hidpi_changes_pixels_not_css_layout_or_translation_position(self):
        store.save_watermark('',{'enabled':False})
        one=await self.capture_html(card_html())
        result1=await asyncio.to_thread(render_post,one['id'],{'0':'漂亮的一天🙂'})
        with Image.open(store.DATA/result1['output']) as image: size1=image.size
        store.save_settings({'capture_scale':2})
        context=await self.browser.open();self.page=await context.new_page()
        two=await self.capture_html(card_html())
        result2=await asyncio.to_thread(render_post,two['id'],{'0':'漂亮的一天🙂'})
        with Image.open(store.DATA/result2['output']) as image:
            self.assertEqual(image.width,size1[0]*2)
            self.assertAlmostEqual(image.height,size1[1]*2,delta=4)
        a,b=one['slots'][0],two['slots'][0]
        self.assertEqual(b['x'],a['x']*2)
        self.assertEqual(b['font_size'],a['font_size']*2)
        # Native emoji font metrics may differ by one CSS pixel across DPRs.
        self.assertAlmostEqual(b['y']/2,a['y'],delta=1)
        self.assertEqual(await self.page.locator('.post').evaluate('el=>el.getBoundingClientRect().width'),420)

    async def test_website_counts_are_not_loaded_comment_count(self):
        counters='<span class="comment-total-count-and-refresh-_-count">1.8K</span><span class="base-comment-artist-count-and-toggle-_-count">14</span>'
        post=await self.capture_html(card_html(counters=counters))
        counts=post['slots'][0]['comment_counts']
        self.assertEqual(len(post['slots']),1)
        self.assertEqual(counts['total'],{'display':'1.8K','value':1800,'approximate':True})
        self.assertEqual(counts['artist']['value'],14)
        self.assertIn('评论 1.8K',await self.page.locator('[data-wvbot-counts]').inner_text())
        newer=await self.capture_html(card_html(counters=counters.replace('1.8K','2,004')))
        self.assertEqual(newer['slots'][0]['comment_counts']['total']['value'],2004)

    async def test_missing_ambiguous_and_zero_counts_remain_distinct(self):
        unknown=await self.capture_html(card_html())
        self.assertIsNone(unknown['slots'][0]['comment_counts']['total'])
        zero=await self.capture_html(card_html(counters='<span class="comment-total-count-and-refresh-_-count">0</span>'))
        self.assertEqual(zero['slots'][0]['comment_counts']['total']['value'],0)
        ambiguous=await self.capture_html(card_html(counters='<span class="comment-total-count-and-refresh-_-count">1</span><span class="comment-total-count-and-refresh-_-count">2</span>'))
        counts=ambiguous['slots'][0]['comment_counts']
        self.assertIsNone(counts['total']);self.assertTrue(counts['warnings'])


class TextWatermarkTests(unittest.TestCase):
    def setUp(self): workflow.reset()

    def test_count_formats_and_invalid_values(self):
        self.assertEqual(parse_count('1.2万')['value'],12000)
        self.assertEqual(parse_count('1,234')['value'],1234)
        self.assertIsNone(parse_count('所有评论'))
        self.assertIsNone(parse_count('-1'))

    def test_nine_positions_use_requested_top_to_bottom_order(self):
        base=Image.new('RGB',(300,400),'white');mark=Image.new('RGBA',(20,10),'black')
        for position in range(1,10):
            cfg=store.WATERMARK_DEFAULTS|{'position':position,'transparency':0}
            result=apply_text(base,mark,cfg)
            box=ImageChops.difference(result,base).getbbox()
            x=[16,140,264][(position-1)%3];y=[16,195,374][(position-1)//3]
            self.assertEqual(box,(x,y,x+20,y+10))

    def test_font_and_transparency_follow_explicit_units(self):
        cfg=store.WATERMARK_DEFAULTS|{'font_size':0}
        self.assertEqual(text_job(840,cfg,28,2)['size'],28)
        self.assertEqual(text_job(840,cfg|{'font_size':20},28,2)['size'],40)
        base=Image.new('RGB',(300,400),'white');mark=Image.new('RGBA',(20,10),'black')
        faint=apply_text(base,mark,cfg|{'transparency':80})
        self.assertEqual(faint.getpixel((150,200)),(204,204,204))
        self.assertIsNone(text_job(300,cfg|{'transparency':100},24,1))

    def test_fill_color_and_outline_are_rendered(self):
        base=Image.new('RGB',(500,300),'white')
        cfg=store.WATERMARK_DEFAULTS|{'text':'ABC','font_size':32,'color':'#ff0000','outline':True,
                                    'outline_color':'#0000ff','outline_width':2,'transparency':0}
        result=compose(base,[],{},cfg)
        colors=set(result.getdata())
        self.assertTrue(any(r>180 and b<100 for r,g,b in colors))
        self.assertTrue(any(b>180 and r<100 for r,g,b in colors))

    def test_watermark_is_last_once_after_main_and_comment_translations(self):
        base=Image.new('RGB',(500,300),'white')
        slots=[{'key':'0','label':'正文','y':100},{'key':'1','label':'评论','y':240}]
        translations={'0':'正文译文\n第二行','1':'评论译文'}
        plain=compose(base,slots,translations,'')
        marked=compose(base,slots,translations,store.WATERMARK_DEFAULTS|{'text':'ABC','position':9,'transparency':0})
        box=ImageChops.difference(marked,plain).getbbox()
        self.assertEqual(marked.size,plain.size)
        self.assertEqual(box[2],plain.width-16)
        self.assertEqual(box[3],plain.height-16)
        self.assertGreater(box[1],300)

    def test_legacy_logo_is_not_rendered_or_deleted_and_new_defaults_are_used(self):
        path=store.DATA/'logos'/'legacy.png';path.write_bytes(b'old-logo')
        with store.db() as connection:
            connection.execute('INSERT INTO watermarks VALUES (?,?)',('123456',json.dumps({'logo':'logos/legacy.png','position':'bottom-right','enabled':True})))
        cfg=store.watermark('123456')
        self.assertEqual(cfg['text'],'@PLAVE_PixelDiary');self.assertEqual(cfg['position'],9)
        self.assertNotIn('logo',cfg);self.assertTrue(path.exists())
        for changes in ({'position':0},{'position':True},{'color':'red'},{'outline_width':9},{'font_size':2},{'logo':'x.png'}):
            with self.assertRaises(ValueError):store.save_watermark('',changes)

    def test_preview_does_not_save_settings_and_png_upload_is_removed(self):
        store.save_settings({'groups':['123456']})
        with TestClient(app) as client:
            client.get('/');headers={'x-wv-csrf':client.get('/api/session').json()['csrf']}
            before=store.watermark('123456')
            response=client.post('/api/watermark/preview?group_id=123456',headers=headers,
                                 json={'changes':{'text':'未保存','position':1,'transparency':0}})
            self.assertEqual(response.status_code,200,response.text[:200] if response.status_code!=200 else '')
            self.assertEqual(response.headers['content-type'],'image/png')
            self.assertEqual(store.watermark('123456'),before)
            response=client.put('/api/watermark?group_id=123456',headers=headers,json={'changes':{'text':'已保存','position':8}})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(store.watermark('123456')['position'],8)
            self.assertEqual(store.watermark()['text'],'@PLAVE_PixelDiary')
            self.assertEqual(client.post('/api/watermark/upload',headers=headers).status_code,404)
            self.assertEqual(client.put('/api/settings',headers=headers,json={'changes':{'capture_scale':4}}).status_code,400)
