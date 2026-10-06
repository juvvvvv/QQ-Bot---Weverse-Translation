import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from test_workflow import reset, create_post, source_image
from weverse_bot import store
from weverse_bot.render import render_post, compose
from weverse_bot.commands import command_name, translations_from_body, help_text
from weverse_bot.watermark import upload_logo


class V2Tests(unittest.TestCase):
    def setUp(self):
        reset()
        store.save_settings({'owner_qq':'111111','groups':['123456','234567']})

    def test_permission_inheritance_and_owner_protection(self):
        for level in (1,2,3):
            store.set_level('123456','222222',level)
            for minimum in (1,2,3):
                self.assertEqual(store.authorized('123456','222222',minimum),level>=minimum)
        with self.assertRaises(ValueError):store.set_level('123456','111111',1)
        with self.assertRaises(ValueError):store.revoke_level('123456','111111',3)
        with self.assertRaises(ValueError):store.revoke_level('123456','222222',1)
        store.revoke_level('123456','222222',3)
        self.assertEqual(store.permission_level('123456','222222'),0)
        self.assertEqual(store.permission_level('234567','222222'),0)

    def test_commands_match_only_complete_start_word(self):
        for text in ('日常聊天 截图','截图一下','帮我烤制','/截图 链接','#截图 链接','恢复 编号','引用 编号','发图 编号'):
            self.assertIsNone(command_name(text))
        self.assertEqual(command_name('烤制 https://weverse.io/plave/artist/123\n#PLAVE'),'烤制')
        self.assertEqual(translations_from_body('正文#标签\n截图 这行也是译文'),{'0':'正文#标签\n截图 这行也是译文'})
        self.assertEqual(translations_from_body('[正文]\n译文\n[评论1]\n评论#标签'),{'0':'译文','1':'评论#标签'})
        self.assertNotIn('设置权限 -l',help_text(2))
        self.assertIn('设置权限 -l',help_text(3))

    def test_logo_footer_preserves_source_and_corner_respects_transparency(self):
        source,filename=source_image()
        logo=Image.new('RGBA',(80,40),(255,0,0,128));buf=io.BytesIO();logo.save(buf,format='PNG')
        upload_logo(buf.getvalue(),'123456')
        slots=[{'key':'0','label':'正文','y':150}]
        result=compose(store.DATA/filename,slots,{'0':'中文译文'},'水印',logo_config=store.watermark('123456'))
        self.assertEqual(result.crop((0,0,320,150)).tobytes(),source.tobytes())
        store.save_watermark('123456',{'position':'top-right','opacity':0})
        transparent=compose(store.DATA/filename,slots,{'0':'中文译文'},'水印',logo_config=store.watermark('123456'))
        self.assertEqual(transparent.crop((0,0,320,150)).tobytes(),source.tobytes())
        store.save_watermark('123456',{'opacity':100})
        visible=compose(store.DATA/filename,slots,{'0':'中文译文'},'水印',logo_config=store.watermark('123456'))
        self.assertNotEqual(visible.crop((0,0,320,150)).tobytes(),source.tobytes())

    def test_png_validation_and_replacement(self):
        im=Image.new('RGBA',(32,16),(0,255,0,100));buf=io.BytesIO();im.save(buf,format='PNG')
        first=upload_logo(buf.getvalue(),'123456')
        second=upload_logo(buf.getvalue(),'123456')
        self.assertFalse((store.DATA/first['logo']).exists())
        self.assertTrue((store.DATA/second['logo']).exists())
        with self.assertRaises(ValueError):upload_logo(b'bad','123456')
        jpg=io.BytesIO();im.convert('RGB').save(jpg,format='JPEG')
        with self.assertRaises(ValueError):upload_logo(jpg.getvalue(),'123456')
        with self.assertRaises(ValueError):store.save_watermark('123456',{'opacity':101})
        with self.assertRaises(ValueError):store.save_watermark('123456',{'position':'invalid'})

    def test_permanent_clear_removes_all_revisions_and_memories_but_preserves_other_group(self):
        a=create_post(group='123456',fingerprints=True)
        b=create_post(group='234567',fingerprints=True)  # Shares the original file intentionally.
        first=render_post(a['id'],{'0':'第一次正文','1':'评论'})
        second=render_post(a['id'],{'0':'第二次正文','1':'评论'})
        other=render_post(b['id'],{'0':'另一个群','1':'另一个群评论'})
        for _ in range(205):create_post(group='123456')
        store.set_level('123456','222222',2)
        logo=Image.new('RGBA',(32,16),(0,0,255,200));buf=io.BytesIO();logo.save(buf,format='PNG')
        cfg=upload_logo(buf.getvalue(),'123456')
        result=store.clear_warehouse('123456')
        self.assertEqual(result['posts'],206)
        self.assertEqual(store.posts(group='123456'),[])
        self.assertFalse((store.DATA/first['output']).exists())
        self.assertFalse((store.DATA/second['output']).exists())
        self.assertTrue((store.DATA/other['output']).exists())
        self.assertTrue((store.DATA/b['original']).exists())
        self.assertIsNone(store.memory('post-hash','123456'))
        self.assertEqual(store.memory('post-hash','234567'),'另一个群')
        self.assertTrue((store.DATA/cfg['logo']).exists())
        self.assertEqual(store.permission_level('123456','222222'),2)
        store.clear_warehouse('234567')
        self.assertFalse((store.DATA/b['original']).exists())

    def test_file_cleanup_failure_is_retained_for_retry(self):
        saved=create_post()
        with patch('weverse_bot.store.cleanup_garbage',side_effect=PermissionError):
            with self.assertRaisesRegex(ValueError,'图片删除失败'):store.clear_warehouse('123456')
        self.assertEqual(store.posts(group='123456'),[])
        with store.db() as c:self.assertGreater(c.execute('SELECT count(*) FROM garbage').fetchone()[0],0)
        store.clear_warehouse('123456')
        self.assertFalse((store.DATA/saved['original']).exists())

    def test_old_boolean_permissions_migrate_without_losing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'archive.sqlite3'
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE members (group_id TEXT,user_id TEXT,PRIMARY KEY(group_id,user_id))')
                c.execute("INSERT INTO members VALUES ('123456','222222')")
            import os
            env=os.environ.copy();env['WEVERSE_DATA_DIR']=tmp
            code="from weverse_bot import store; assert store.members()[0]['level']==2; store.init(); assert len(store.members())==1"
            result=subprocess.run([sys.executable,'-c',code],cwd=store.ROOT,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
