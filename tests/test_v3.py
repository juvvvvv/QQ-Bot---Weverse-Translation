import asyncio
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from PIL import Image
from fastapi.testclient import TestClient
import test_workflow as workflow
from weverse_bot import store
from weverse_bot.artist_comments import order_comments
from weverse_bot.translations import split_body, expand_emoji, prepare, render_body, latest_context
from weverse_bot.app import app
from weverse_bot.commands import command_name


def draft(group='123456', extra=False):
    post=workflow.create_post(group)
    slots=post['slots']
    slots[0].update(author='YEJUN',emojis=['☺️','💙'],comment_id='')
    slots[1].update(author='HAMIN',emojis=['🙂'],comment_id='r1')
    if extra:
        slots.insert(1,{'key':'1','label':'新评论','y':85,'text':'新增原文','author':'EUNHO','comment_id':'r2','emojis':['👨‍👩‍👧‍👦']})
        slots[2]['key']='2'
    with store.db() as c:
        c.execute('UPDATE posts SET slots=? WHERE id=?',(json.dumps(slots,ensure_ascii=False),post['id']))
    return store.get_post(post['id'])


class TranslationTests(unittest.TestCase):
    def setUp(self): workflow.reset();store.save_watermark('',{'enabled':False})

    def test_plus_is_a_line_delimiter_and_append_is_explicit(self):
        self.assertEqual(split_body('正文 C++\n\n第二段\n+\n评论'),(False,['正文 C++\n\n第二段','评论']))
        self.assertEqual(split_body('+\n评论'),(True,['评论']))
        for text in ('','+','正文\n+','正文\n+\n+\n评论'):
            with self.assertRaises(ValueError):split_body(text)
        self.assertEqual(command_name('烤制|https://weverse.io/plave/artist/1234|'),'烤制')
        self.assertIsNone(command_name('今天烤制|链接|'))

    def test_emoji_references_are_adjacent_local_and_preserve_urls(self):
        slot={'key':'0','emojis':['👨‍👩‍👧‍👦','🇨🇳','👍🏽']}
        self.assertEqual(expand_emoji('/e/e 译文 /e',slot),'👨‍👩‍👧‍👦🇨🇳 译文 👍🏽')
        self.assertEqual(expand_emoji('/e https://example.org/e?q=/e',slot),'👨‍👩‍👧‍👦 https://example.org/e?q=/e')
        self.assertEqual(expand_emoji('/email',slot),'/email')
        self.assertEqual(expand_emoji('无引用',slot),'无引用')
        with self.assertRaises(ValueError):expand_emoji('/e/e/e/e',slot)

    def test_full_append_reorder_typo_correction_only_latest_survives(self):
        first=draft();v1=render_body(first['id'],'正文/e/e\n+\n评论/e')
        path1=store.DATA/v1['output']
        self.assertEqual(v1['translations'],{'0':'正文☺️💙','1':'评论🙂'})
        fresh=draft(extra=True)
        latest,saved,changed=latest_context(fresh)
        self.assertEqual(saved,{'0':'正文☺️💙','2':'评论🙂'})
        v2=render_body(fresh['id'],'+\n新评论/e')
        self.assertEqual(v2['translations'],{'0':'正文☺️💙','2':'评论🙂','1':'新评论👨‍👩‍👧‍👦'})
        self.assertFalse(path1.exists())
        self.assertEqual(len(store.posts(group='123456')),1)
        path2=store.DATA/v2['output']
        v3=render_body(v2['id'],'正文订正\n+\n新增评论订正\n+\n原评论订正')
        self.assertEqual(store.latest_rendered(v3['url'],'123456')['translations']['0'],'正文订正')
        self.assertFalse(path2.exists())
        self.assertTrue((store.DATA/v3['output']).exists())

    def test_bad_count_and_render_failure_preserve_last_success(self):
        old=draft();saved=render_body(old['id'],'正文\n+\n评论')
        new=draft(extra=True)
        for body in ('只有一段','+\n多余\n+\n另一段'):
            with self.assertRaises(ValueError):render_body(new['id'],body)
        with patch('weverse_bot.render.compose',side_effect=ValueError('模拟排版失败')):
            with self.assertRaises(ValueError):render_body(new['id'],'+\n新评论')
        self.assertEqual(store.latest_rendered(old['url'],'123456')['id'],old['id'])
        self.assertTrue((store.DATA/saved['output']).exists())

    def test_group_isolation_and_edited_original_require_full_bake(self):
        first=draft();render_body(first['id'],'正文\n+\n评论')
        with self.assertRaisesRegex(ValueError,'没有这个链接'):prepare(draft('234567',True),'+\n新评论')
        new=draft(extra=True);new['slots'][0]['text']='被编辑的原文'
        with self.assertRaisesRegex(ValueError,'发生变化'):prepare(new,'+\n新评论')

    def test_known_threads_group_and_unknown_replies_are_not_guessed(self):
        records=[{'comment_id':'c','parent_id':'a','is_reply':True,'published':'09. 30. 03:30','source_index':0},
                 {'comment_id':'b','is_reply':False,'published':'09. 30. 03:20','source_index':1},
                 {'comment_id':'a','is_reply':False,'published':'09. 30. 03:10','source_index':2}]
        self.assertEqual([r['comment_id'] for r in order_comments(records)],['a','c','b'])
        unknown=[dict(records[0],parent_id=None),records[1],records[2]]
        ordered=order_comments(unknown)
        self.assertEqual([r['comment_id'] for r in ordered],['a','b','c'])
        self.assertFalse(ordered[-1]['grouping_known']);self.assertEqual(ordered[-1]['depth'],1)

    def test_same_minute_uses_reverse_source_order_not_numeric_ids(self):
        rows=[{'comment_id':'1-2','is_reply':True,'published':'09. 30. 03:49','source_index':0},
              {'comment_id':'4-999','is_reply':True,'published':'09. 30. 03:49','source_index':1}]
        self.assertEqual([r['comment_id'] for r in order_comments(rows)],['4-999','1-2'])

    def test_api_unified_input_and_total_count_removed(self):
        post=draft()
        with TestClient(app) as client:
            client.get('/');headers={'x-wv-csrf':client.get('/api/session').json()['csrf']}
            self.assertNotIn('comment_count_selector',client.get('/api/settings').json())
            result=client.post('/api/posts/'+post['id']+'/render',headers=headers,json={'body':'正文/e\n+\n评论/e'})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['translations']['1'],'评论🙂')
            self.assertTrue(result.json()['has_saved_version'])


class NativeCommentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):await workflow.BrowserTests.asyncSetUp(self)
    async def asyncTearDown(self):await workflow.BrowserTests.asyncTearDown(self)

    async def fixture(self, collapsed=False):
        html=Path(__file__).with_name('fixtures').joinpath('weverse-artist-comments.html').read_text()
        style='''<style>body{margin:0;font-family:Arial}.blind{display:none}.post{width:420px;padding:16px;background:white;box-sizing:border-box}
        .text{font-size:14px;line-height:1.6;white-space:pre-wrap}.comment-item-_-container{display:flex;margin:16px}
        .comment-item-header-_-container{display:flex;gap:8px}.comment-item-header-_-time{font-size:12px;color:#aaa}
        .line-clamp-node-view-_-container{font-size:14px;line-height:1.6}.comment-item-_-image_area{flex:none}
        .avatar-_-image{display:block;border-radius:50%}.toolbar-_-container{display:flex;margin-top:10px}.toolbar-_-left{display:flex;gap:12px}
        .toolbar-_-button_text{background:white;border:0}.comment-item-content-_-mention{font-weight:bold}</style>'''
        await self.page.set_content(style+'<article class="post"><div class="artist">YEJUN</div><p class="text">예쁜하루☺️</p></article>'+html)
        if collapsed:
            await self.page.locator('.comment-list-by-artists-_-comment_list').evaluate("el=>el.style.display='none'")
            await self.page.locator('.base-comment-artist-count-and-toggle-_-toggle_button').evaluate('''el=>{
                el.setAttribute('aria-expanded','false');el.onclick=()=>{window.clicks=(window.clicks||0)+1;el.setAttribute('aria-expanded','true');document.querySelector('.comment-list-by-artists-_-comment_list').style.display='block';};
            }''')

    async def test_provided_14_comment_dom_captures_avatars_order_styles_and_emojis(self):
        await self.fixture(collapsed=True)
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',self.cfg|{'comment_selector':''})
        self.assertEqual(len(post['slots']),15)
        self.assertEqual(await self.page.evaluate('window.clicks'),1)
        self.assertEqual(post['slots'][1]['published'],'09. 30. 03:13')
        self.assertEqual(post['slots'][2]['published'],'09. 30. 03:14')
        self.assertEqual(post['slots'][-1]['emojis'],['😊','🌱'])
        self.assertEqual(post['slots'][1]['author'],'HAMIN')
        self.assertTrue(post['slots'][1]['frame'])
        self.assertTrue(post['slots'][3]['is_reply'])
        self.assertGreater(post['slots'][3]['x'],post['slots'][1]['x'])
        self.assertIn('未提供父评论编号',post['note'])
        original=Image.open(store.DATA/post['original'])
        self.assertEqual(original.width,420)
        self.assertIn((255,165,0),set(original.convert('RGB').getdata()))
        body='\n+\n'.join(f'第{i+1}段中文'+('/e'*len(s.get('emojis',[]))) for i,s in enumerate(post['slots']))
        result=await asyncio.to_thread(render_body,post['id'],body)
        image=Image.open(store.DATA/result['output'])
        self.assertEqual(image.width,420);self.assertGreater(image.height,original.height)
        image.save('/tmp/plave-v3-native-comments.png')

    async def test_artist_counter_mismatch_stops_incomplete_export(self):
        await self.fixture()
        await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate("el=>el.textContent='15'")
        with self.assertRaisesRegex(ValueError,'仅读取 14'):
            await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',self.cfg|{'comment_selector':''})
        self.assertEqual(store.posts(),[])

    async def test_native_comments_disabled_and_zero_are_distinct(self):
        await self.fixture()
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',self.cfg|{'capture_artist_comments':False,'comment_selector':''})
        self.assertEqual(len(post['slots']),1)
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],14)

    async def test_hidden_artist_counter_is_read_without_treating_it_as_zero(self):
        await self.fixture()
        await self.page.locator('.base-comment-artist-count-and-toggle-_-count').evaluate("el=>el.style.display='none'")
        post=await self.browser.extract(self.page,'https://weverse.io/plave/artist/3-241901329','123456',self.cfg|{'capture_artist_comments':False,'comment_selector':''})
        self.assertEqual(post['slots'][0]['comment_counts']['artist']['value'],14)


class NewQQTests(unittest.IsolatedAsyncioTestCase):
    wait_for=workflow.QQTests.wait_for
    message=workflow.QQTests.message
    sent=workflow.QQTests.sent
    async def asyncSetUp(self):await workflow.QQTests.asyncSetUp(self)
    async def asyncTearDown(self):await workflow.QQTests.asyncTearDown(self)

    async def test_pipe_command_emoji_append_and_latest_only_over_onebot(self):
        calls=0
        async def capture(url,group):
            nonlocal calls
            calls+=1
            return draft(group,extra=calls>1)
        with patch('weverse_bot.qq.browser.capture',new=AsyncMock(side_effect=capture)):
            await self.message('烤制|https://weverse.io/plave/artist/1234|\n正文/e/e\n+\n评论/e',mid=101)
            await self.wait_for(lambda:len(self.sent())==2)
            old=store.latest_rendered('https://weverse.io/plave/artist/1234','123456')
            self.assertEqual(old['translations']['0'],'正文☺️💙')
            await self.message('烤制|https://weverse.io/plave/artist/1234|\n+\n新评论/e',mid=102)
            await self.wait_for(lambda:len(self.sent())==4)
            latest=store.latest_rendered(old['url'],'123456')
            self.assertEqual(latest['translations']['1'],'新评论👨‍👩‍👧‍👦')
            self.assertEqual(latest['translations']['2'],'评论🙂')
            self.assertEqual(len(store.posts(group='123456')),1)
            self.assertFalse((store.DATA/old['output']).exists())
