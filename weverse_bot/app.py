import asyncio
import hmac
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from . import store
from .capture import browser, weverse_url
from .render import read_image, render_post
from .qq import qq

SESSION = secrets.token_urlsafe(32)
CSRF = secrets.token_urlsafe(32)
from .locks import workflow_lock as operation_lock
from .render import compose
from .translations import render_body, latest_context
from .commands import COMMANDS, help_text
monitor_state = {'state': '未启用', 'last_run': None, 'last_error': ''}
monitor_wakeup = asyncio.Event()


def serialize(post):
    post = dict(post)
    post['original_url'] = '/media/' + post['original']
    post['output_url'] = '/media/' + post['output'] if post['output'] else None
    post['comment_counts'] = post['slots'][0].get('comment_counts') if post['slots'] else None
    latest, saved, changed = latest_context(post)
    post['has_saved_version'] = bool(latest)
    post['saved_translations'] = saved
    post['changed_slots'] = changed
    for slot in post['slots']:
        slot['reusable'] = bool(slot.get('fingerprint') and store.memory(slot['fingerprint'], post['group_id']))
    return post


async def monitor_once():
    cfg = store.settings()
    monitor_state.update(state='正在检查', last_error='')
    try:
        urls = (await browser.discover())[:10]
        groups = [g for g in (cfg['groups'] or ['']) if g not in cfg['monitor_paused_groups']]
        if not groups:
            monitor_state.update(state='所有群自动记录已暂停', last_run=time.time(), last_error='')
            return
        for url in urls:
            previous = {group: store.latest_by_url(url, group) for group in groups}
            fresh = await browser.capture(url, groups[0])
            fingerprints = [s.get('fingerprint') for s in fresh['slots']]
            kept = False
            for index, group in enumerate(groups):
                old = previous[group]
                same = old and [s.get('fingerprint') for s in old['slots']] == fingerprints
                if index == 0:
                    if same:
                        with store.db() as con:
                            con.execute('DELETE FROM posts WHERE id=?', (fresh['id'],))
                    else:
                        kept = True
                elif not same:
                    store.add_post(fresh['url'], fresh['title'], group, fresh['original'],
                                   fresh['slots'], fresh['source'], fresh['note'])
                    kept = True
            if not kept:
                (store.DATA / fresh['original']).unlink(missing_ok=True)
        monitor_state.update(state='检查完成', last_run=time.time(), last_error='')
        store.event('动态检查完成；已记录本次列表可见帖子及匹配的艺人评论')
    except Exception as exc:
        monitor_state.update(state='检查失败', last_run=time.time(),
                             last_error=str(exc) if isinstance(exc, ValueError) else '网络或选择器异常，请检查登录与页面校准')
        store.event('动态检查失败，请查看 WebUI 状态', 'warning')
        raise


async def monitor_loop():
    while True:
        cfg = store.settings()
        if cfg['monitor_enabled']:
            try:
                async with operation_lock:
                    await monitor_once()
            except Exception:
                pass
        else:
            monitor_state['state'] = '未启用'
        try:
            await asyncio.wait_for(monitor_wakeup.wait(), timeout=cfg['poll_seconds'])
            monitor_wakeup.clear()
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app):
    monitor_task = asyncio.create_task(monitor_loop())
    store.event('WebUI 已启动（仅本机访问）')
    try:
        yield
    finally:
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
        await qq.stop()
        await browser.close()


app = FastAPI(title='PLAVE 翻译工作台', lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])
app.mount('/static', StaticFiles(directory=store.ROOT / 'static'), name='static')


@app.middleware('http')
async def local_auth(request: Request, call_next):
    if request.url.path.startswith(('/api/', '/media/')):
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '只允许本机管理页面访问。'}, status_code=403)
        if not hmac.compare_digest(request.cookies.get('wv_session', ''), SESSION):
            return JSONResponse({'detail': '请先打开本机 WebUI 首页。'}, status_code=401)
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-wv-csrf', ''), CSRF):
            return JSONResponse({'detail': '页面已过期，请刷新后重试。'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.get('/')
async def home():
    response = FileResponse(store.ROOT / 'static/index.html')
    response.set_cookie('wv_session', SESSION, httponly=True, samesite='strict')
    return response


@app.get('/manual')
async def manual():
    return FileResponse(store.ROOT / 'docs/manual.html')


@app.get('/api/session')
async def session():
    return {'csrf': CSRF}


@app.get('/api/status')
async def status():
    entries = store.posts()
    counts = {s: sum(p['status'] == s for p in entries) for s in ('pending', 'partial', 'translated', 'ignored')}
    return {'counts': counts, 'count_note': '最近 200 条档案', 'qq': qq.status,
            'qq_enabled': qq.enabled, 'browser': browser.login_state, 'browser_error': browser.last_error,
            'monitor': monitor_state, 'members': len(store.members()),
            'web_capture_configured': all(store.settings()[k] for k in ('post_selector','text_selector','artist_selector','author_selector'))}


@app.get('/api/settings')
async def settings():
    cfg = store.settings()
    cfg.pop('watermark', None)
    cfg['ws_token_set'] = bool(cfg.pop('ws_token'))
    return cfg


class SettingsUpdate(BaseModel):
    changes: dict


@app.put('/api/settings')
async def update_settings(body: SettingsUpdate):
    value = body.changes
    if set(value) - set(store.DEFAULTS):
        raise ValueError('设置包含未知字段。')
    cfg = store.settings() | value
    for name in ('owner_qq', 'ws_url', 'ws_token', 'font_path', 'post_selector', 'text_selector',
                 'comment_selector', 'comment_text_selector', 'artist_selector', 'author_selector', 'expand_selector',
                 'artist_comment_count_selector', 'feed_url', 'feed_link_selector'):
        if not isinstance(cfg[name], str) or len(cfg[name]) > 2000:
            raise ValueError(f'{name} 字段格式不正确或过长。')
    if cfg['owner_qq'] and not re.fullmatch(r'[1-9][0-9]{4,19}', cfg['owner_qq']):
        raise ValueError('主人 QQ 号需为 5–20 位数字。')
    if not isinstance(cfg['groups'], list) or len(cfg['groups']) > 30 or any(not isinstance(g, str) or not re.fullmatch(r'[1-9][0-9]{4,19}', g) for g in cfg['groups']):
        raise ValueError('群号需为 5–20 位数字，最多启用 30 个群。')
    cfg['groups'] = list(dict.fromkeys(cfg['groups']))
    if not isinstance(cfg['monitor_paused_groups'], list) or any(not isinstance(g, str) or (g and not re.fullmatch(r'[1-9][0-9]{4,19}', g)) for g in cfg['monitor_paused_groups']):
        raise ValueError('暂停自动记录的群号格式不正确。')
    ws = urlsplit(cfg['ws_url'])
    if ws.scheme not in ('ws', 'wss') or not ws.hostname or ws.username or ws.password:
        raise ValueError('OneBot 地址需为 ws:// 或 wss://，令牌请填在独立令牌框。')
    if ws.scheme == 'ws' and ws.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('明文 WebSocket 只允许本机地址；远程 OneBot 请使用 wss://。')
    for key, low, high in [('font_size', 12, 48), ('capture_width', 480, 1600), ('poll_seconds', 60, 86400), ('max_scrolls', 1, 30), ('comment_wait_seconds', 5, 120)]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError(f'{key} 需要在 {low}–{high} 范围内。')
    if type(cfg['capture_scale']) is not int or cfg['capture_scale'] not in (1, 2, 3):
        raise ValueError('截图像素倍率支持 1、2、3。')
    if any(type(cfg[k]) is not bool for k in ('headless', 'monitor_enabled', 'capture_artist_comments')):
        raise ValueError('开关需为布尔值。')
    if cfg['monitor_enabled'] and not all(cfg[k] for k in ('post_selector', 'text_selector', 'artist_selector', 'author_selector', 'feed_link_selector')):
        raise ValueError('自动记录需要先完成帖子、原文、艺人标识、作者及列表链接选择器校准。')
    weverse_url(cfg['feed_url'], feed=True)
    connection_changed = any(k in value and value[k] != store.settings()[k] for k in ('ws_url', 'ws_token', 'owner_qq', 'groups'))
    if connection_changed and qq.enabled:
        await qq.stop()
    store.save_settings(cfg)
    monitor_wakeup.set()
    store.event('设置已保存' + ('；QQ 连接配置变更，请重新连接' if connection_changed else ''))
    return {'ok': True, 'reconnect': connection_changed}


@app.get('/api/posts')
async def posts(status: str = ''):
    if status and status not in ('pending', 'partial', 'translated', 'ignored'):
        raise ValueError('未知档案状态。')
    return [serialize(p) for p in store.posts(status)]


@app.get('/api/posts/{post_id}')
async def post(post_id: str):
    return serialize(store.get_post(post_id))


class CaptureRequest(BaseModel):
    url: str = Field(max_length=2000)
    group_id: str = ''


def check_group(group):
    if group and group not in store.settings()['groups']:
        raise ValueError('请先在设置中启用这个 QQ 群。')


@app.post('/api/capture')
async def capture(body: CaptureRequest):
    check_group(body.group_id)
    if operation_lock.locked():
        raise ValueError('已有网页任务正在执行，请稍后重试。')
    async with operation_lock:
        return serialize(await browser.capture(body.url, body.group_id))


@app.post('/api/upload')
async def upload(file: UploadFile = File(...), title: str = Form('手动截图'), group_id: str = Form(''),
                 slots: str = Form('[{"key":"0","label":"正文","y":0}]')):
    check_group(group_id)
    content = await file.read(12 * 1024 * 1024 + 1)
    if len(content) > 12 * 1024 * 1024:
        raise ValueError('文件不能超过 12 MB。')
    im = await asyncio.to_thread(read_image, content)
    try:
        parsed = __import__('json').loads(slots)
        if not isinstance(parsed, list) or not 1 <= len(parsed) <= 30:
            raise ValueError('需要 1–30 个翻译位置。')
        clean = []
        for index, s in enumerate(parsed):
            if not isinstance(s, dict) or type(s.get('y')) is not int or not 0 <= s['y'] <= im.height:
                raise ValueError('翻译位置需为原图中的整数像素坐标，不能超出图片高度。')
            label = s.get('label', '正文' if index == 0 else f'艺人评论 {index}')
            if not isinstance(label, str) or len(label) > 80:
                raise ValueError('位置名称不正确。')
            clean.append({'key': str(index), 'label': label, 'y': s['y'], 'text': '', 'fingerprint': None, 'reusable': False})
    except (TypeError, KeyError, __import__('json').JSONDecodeError) as exc:
        raise ValueError('翻译位置格式错误。') from exc
    name = f'originals/{uuid.uuid4().hex}.png'
    async with operation_lock:
        await asyncio.to_thread(im.save, store.DATA / name)
        saved = store.add_post('', title[:120], group_id, name, clean, 'upload',
                               '手动截图：请自行核对 PLAVE 艺人身份、正文和评论完整性。上传截图不自动复用历史译文。')
    store.event(f"上传截图：{saved['id']}")
    return serialize(saved)


class TranslationRequest(BaseModel):
    translations: dict[str, str] | None = None
    body: str | None = Field(default=None, max_length=50000)
    reuse: bool = False


@app.post('/api/posts/{post_id}/render')
async def render(post_id: str, body: TranslationRequest):
    async with operation_lock:
        if body.body is not None:
            return serialize(await asyncio.to_thread(render_body, post_id, body.body))
        if body.translations is None:
            raise ValueError('请填写完整译文。')
        return serialize(await asyncio.to_thread(render_post, post_id, body.translations, body.reuse))


@app.delete('/api/warehouse')
async def clear_warehouse(group_id: str = ''):
    check_group(group_id)
    async with operation_lock:
        return await asyncio.to_thread(store.clear_warehouse, group_id)


@app.get('/api/commands')
async def commands(level: int = 3):
    if level not in (1, 2, 3):
        raise ValueError('等级只支持 1、2、3。')
    return {'level': level, 'help': help_text(level),
            'commands': [{'name': name, 'level': minimum, 'example': example, 'description': description}
                         for name, (minimum, example, description) in COMMANDS.items() if level >= minimum]}


class WatermarkUpdate(BaseModel):
    changes: dict


@app.get('/api/watermark')
async def watermark(group_id: str = ''):
    check_group(group_id)
    return store.watermark(group_id)


@app.put('/api/watermark')
async def update_watermark(body: WatermarkUpdate, group_id: str = ''):
    check_group(group_id)
    async with operation_lock:
        return store.save_watermark(group_id, body.changes)


class WatermarkPreview(BaseModel):
    changes: dict
    post_id: str | None = None


@app.post('/api/watermark/preview')
async def preview_watermark(body: WatermarkPreview, group_id: str = ''):
    check_group(group_id)
    cfg = store.validate_watermark(store.watermark(group_id) | body.changes)
    settings = store.settings()
    def render_preview():
        import io
        from PIL import Image, ImageDraw
        if body.post_id:
            post = store.get_post(body.post_id)
            image = compose(store.DATA / post['original'], post['slots'], post['translations'], cfg,
                            settings['font_size'], settings['font_path'])
        else:
            base = Image.new('RGB', (640, 360), '#edf2f7')
            ImageDraw.Draw(base).rectangle((0, 0, 319, 359), fill='#2d3748')
            image = compose(base, [], {}, cfg, settings['font_size'], settings['font_path'])
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        return stream.getvalue()
    async with operation_lock:
        content = await asyncio.to_thread(render_preview)
    return Response(content, media_type='image/png', headers={'Cache-Control': 'no-store'})


@app.post('/api/posts/{post_id}/send')
async def send_post(post_id: str):
    saved = store.get_post(post_id)
    check_group(saved['group_id'])
    if not saved['group_id']:
        raise ValueError('此档案未关联 QQ 群。请先在档案详情中选择群。')
    if not saved['output']:
        raise ValueError('请先生成翻译图片。')
    await qq.send(saved['group_id'], f"PLAVE 翻译 · {post_id}", saved['output'])
    return {'ok': True}


class GroupRequest(BaseModel):
    group_id: str


@app.put('/api/posts/{post_id}/group')
async def assign_group(post_id: str, body: GroupRequest):
    check_group(body.group_id)
    store.get_post(post_id)
    with store.db() as c:
        c.execute('UPDATE posts SET group_id=?,updated=? WHERE id=?', (body.group_id, time.time(), post_id))
    return {'ok': True}


@app.get('/api/members')
async def members():
    return store.members()


class MemberRequest(BaseModel):
    group_id: str
    user_id: str
    enabled: bool = True
    level: int = Field(default=2, ge=1, le=3)


@app.post('/api/members')
async def member(body: MemberRequest):
    check_group(body.group_id)
    if not body.group_id or not re.fullmatch(r'[1-9][0-9]{4,19}', body.user_id):
        raise ValueError('请选择已启用的群，并填写正确的 QQ 号。')
    if body.enabled:
        store.set_level(body.group_id, body.user_id, body.level)
    else:
        store.revoke_level(body.group_id, body.user_id)
    return {'ok': True}


@app.post('/api/qq/{action}')
async def connect(action: str):
    if action == 'connect':
        cfg = store.settings()
        if not cfg['owner_qq'] or not cfg['groups']:
            raise ValueError('请先设置主人 QQ 号和至少一个启用群。')
        await qq.start()
    elif action == 'disconnect':
        await qq.stop()
    else:
        raise ValueError('未知连接操作。')
    return {'ok': True}


@app.post('/api/browser/login')
async def login():
    return {'message': await browser.login()}


@app.post('/api/browser/close')
async def close_browser():
    await browser.close()
    return {'ok': True}


@app.post('/api/monitor/check')
async def monitor_check():
    if operation_lock.locked():
        raise ValueError('已有任务正在执行，请稍后重试。')
    async with operation_lock:
        await monitor_once()
    return monitor_state


@app.get('/api/events')
async def events():
    with store.db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 80')]


@app.get('/media/{folder}/{filename}')
async def media(folder: str, filename: str):
    if folder not in ('originals', 'outputs') or not re.fullmatch(r'[a-f0-9-]+\.png', filename):
        raise HTTPException(404)
    path = store.DATA / folder / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='image/png')
