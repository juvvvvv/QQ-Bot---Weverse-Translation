import asyncio
import hashlib
import io
import re
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit
from PIL import Image
from playwright.async_api import async_playwright, Error as BrowserError, TimeoutError as BrowserTimeout
from . import store
from .page_cleanup import (reject_optional_consent, remove_site_chrome,
                           ensure_author_visible, prepare_emoji_text)
from .capture_layout import read_comment_counts, add_count_row, measure_card, screenshot_card
from .post_adapter import adapt_post
from .comment_image import comment_model, render_comment
from .artist_comments import (collect_artist_comments, stage_comment, decoration, TEXT, AUTHOR,
                              artist_avatar_sources, ensure_comment_avatar, avatar_source)


def weverse_url(raw, feed=False):
    value = urlsplit(raw.strip())
    if value.scheme != 'https' or value.hostname != 'weverse.io' or value.port not in (None, 443) or value.username or value.password:
        raise ValueError('只接受 https://weverse.io/plave/… 的链接。')
    if not re.match(r'^/plave(?:/|$)', value.path, re.I):
        raise ValueError('此机器人只处理 PLAVE 社区。')
    if not feed and not re.fullmatch(r'/plave/(?:artist|fanpost)/[0-9-]+/?', value.path, re.I):
        raise ValueError('请提供 PLAVE 帖子详情链接，例如 /plave/artist/数字编号。评论用帖子编号和评论序号处理。')
    # Share/query tokens must not be persisted in archives or logs.
    return urlunsplit(('https', 'weverse.io', value.path, '', ''))


class Browser:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.pw = self.context = None
        self.last_error = ''
        self.login_state = '尚未验证'
        self.mode = None
        self.phase = '尚未读取'

    async def reset_connection(self):
        context,pw=self.context,self.pw
        self.context=self.pw=None
        self.mode=None
        if context:
            try:await context.close()
            except BrowserError:pass
        if pw:
            try:await pw.stop()
            except BrowserError:pass

    async def open(self, headed=False):
        mode = ('headed' if headed else 'headless', store.settings()['capture_scale'])
        if self.context and self.mode == mode:
            try:
                # A retained Python handle can outlive a browser closed by the
                # user. Probe the connection, without logging any Cookie data.
                await self.context.cookies()
                return self.context
            except BrowserError:
                await self.reset_connection()
                store.event('浏览器连接已失效，正在自动恢复；登录资料保留。')
        if self.context:
            await self.context.close()
            self.context = None
        if not self.pw:
            self.pw = await async_playwright().start()
        self.context = await self.pw.chromium.launch_persistent_context(
            str(store.DATA / 'browser'), headless=not headed,
            executable_path=__import__('os').environ.get('WEVERSE_BROWSER_EXECUTABLE') or None,
            viewport={'width': store.settings()['capture_width'], 'height': 1000},
            device_scale_factor=mode[1], locale='zh-CN',
        )
        self.mode = mode
        return self.context

    async def close(self):
        async with self.lock:
            await self.reset_connection()

    @staticmethod
    async def close_page(page):
        if not page:return
        tasks=list(getattr(page,'_wv_response_tasks',set()))
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        try:await page.close()
        except BrowserError:pass

    @staticmethod
    def recoverable(exc):
        return isinstance(exc,BrowserTimeout) or (isinstance(exc,BrowserError) and any(
            text in str(exc) for text in ('has been closed','Connection closed','Browser closed',
                                         'net::ERR_CONNECTION_RESET','net::ERR_ABORTED','net::ERR_TIMED_OUT')))

    async def login(self):
        if sys.platform not in ('darwin', 'win32') and not __import__('os').environ.get('DISPLAY'):
            raise ValueError('当前机器没有桌面。请在你的 Windows / Mac 上点击登录，在弹出的浏览器中手动登录。')
        async with self.lock:
            context = await self.open(headed=True)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto('https://weverse.io/plave/artist', wait_until='domcontentloaded', timeout=60000)
            self.login_state = '登录窗口已打开；请手动登录，再抓取一条帖子验证'
            return self.login_state

    async def ready_page(self, url):
        ctx = await self.open(headed=not store.settings()['headless'])
        page = await ctx.new_page()
        page._wv_parents = {}
        page._wv_response_tasks = set()
        async def read_response(response):
            try:
                parsed = urlsplit(response.url)
                if 'comment' not in parsed.path.lower() or not (
                    parsed.hostname == 'global.apis.naver.com' or
                    parsed.hostname == 'weverse.io' or (parsed.hostname or '').endswith('.weverse.io')):
                    return
                if int(response.headers.get('content-length', '0')) > 2_000_000:
                    return
                value = await response.json()
                def visit(node):
                    if isinstance(node, list):
                        for item in node: visit(item)
                    elif isinstance(node, dict):
                        cid = node.get('commentId') or node.get('comment_id') or node.get('id')
                        parent = node.get('parentCommentId') or node.get('parent_comment_id') or node.get('rootCommentId')
                        if cid and parent:
                            page._wv_parents[str(cid)] = str(parent)
                        for item in node.values():
                            if isinstance(item, (dict, list)): visit(item)
                visit(value)
            except Exception:
                pass  # DOM fallback never invents a parent when data is absent.
        def response_received(response):
            task = asyncio.create_task(read_response(response))
            page._wv_response_tasks.add(task)
            task.add_done_callback(page._wv_response_tasks.discard)
        page.on('response', response_received)
        await page.set_viewport_size({'width': store.settings()['capture_width'], 'height': 1000})
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(2000)
            await reject_optional_consent(page, wait_ms=2000)
            return page
        except Exception:
            await self.close_page(page)
            raise

    async def capture(self, url, group=''):
        url = weverse_url(url)
        cfg = store.settings()
        if not all(cfg[k] for k in ('post_selector','text_selector','artist_selector','author_selector')):
            raise ValueError('网页抓取尚未校准。请在设置中填写帖子、原文、艺人标识和作者 CSS 选择器；也可以先上传截图。')
        async with self.lock:
            for attempt in range(2):
                page=None
                self.phase='网页打开'
                try:
                    page=await self.ready_page(url)
                    post=await self.extract(page,url,group,cfg)
                    self.last_error=''
                    return post
                except ValueError as exc:
                    self.last_error=str(exc)
                    raise
                except Exception as exc:
                    if attempt==0 and self.recoverable(exc):
                        await self.close_page(page);page=None
                        store.event(f'{self.phase}时连接中断或超时，自动恢复浏览器并重试一次。','warning')
                        await self.reset_connection()
                        continue
                    hint='网页或图片加载超时，请检查网络后重试。' if isinstance(exc,BrowserTimeout) else '网页读取失败，请查看运行日志中的阶段和错误类型。'
                    self.last_error=f'{self.phase}：{hint}'
                    store.event(f'网页读取失败 · 阶段：{self.phase} · 类型：{type(exc).__name__}；设置与成功译文已保留。','error')
                    raise ValueError(self.last_error) from exc
                finally:
                    await self.close_page(page)

    async def extract(self, page, url, group, cfg):
        """Capture a recognized original post and verified artist comments."""
        await reject_optional_consent(page)
        self.phase='原帖识别'
        cfg=await adapt_post(page,cfg)
        root = page.locator(cfg['post_selector'])
        try:
            await root.first.wait_for(state='visible', timeout=15000)
        except Exception as exc:
            if self.recoverable(exc) and not isinstance(exc,BrowserTimeout):raise
            if await page.locator('input[type=password]').count() or 'account.weverse.io' in page.url:
                self.login_state = '登录失效，请重新登录'
                raise ValueError(self.login_state) from exc
            raise ValueError('未找到帖子。可能需要登录，或 CSS 选择器已变化，请重新校准。') from exc
        if await root.count() != 1:
            raise ValueError('帖子选择器必须只匹配一个帖子卡片。请缩小选择器范围。')
        if cfg.get('post_kind')!='fan' and not await root.locator(cfg['artist_selector']).count():
            raise ValueError('未找到艺人标识，停止抓取。请确认这是 From PLAVE 艺人帖子，并校准标识选择器。')
        await remove_site_chrome(page)
        if cfg['expand_selector']:
            # Only explicit expand controls inside the post/comment cards, no sitewide buttons.
            scopes = [root]
            if cfg['comment_selector']:
                scopes.append(page.locator(cfg['comment_selector']))
            for scope in scopes:
                buttons = scope.locator(cfg['expand_selector'])
                for i in range(min(await buttons.count(), 30)):
                    button = buttons.nth(i)
                    if await button.is_visible():
                        await button.click(timeout=3000)
                        await page.wait_for_timeout(200)
        reached_end = False
        for _ in range(cfg['max_scrolls']):
            old = await page.evaluate('document.documentElement.scrollHeight')
            await page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
            await page.wait_for_timeout(600)
            new = await page.evaluate('document.documentElement.scrollHeight')
            if new == old:
                reached_end = True
                break
        items = [(root, cfg['text_selector'], '正文', cfg['author_selector'], {})]
        native = []
        if cfg.get('capture_artist_comments', True):
            self.phase='艺人评论读取'
            native = await collect_artist_comments(page, cfg, getattr(page, '_wv_parents', {}))
            tasks = list(getattr(page, '_wv_response_tasks', set()))
            if tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True),timeout=5)
                except asyncio.TimeoutError:pass
                native = await collect_artist_comments(page, cfg, getattr(page, '_wv_parents', {}))
        if native:
            for record in native:
                items.append((record['card'], TEXT, f"艺人评论 {len(items)}", AUTHOR, record))
        elif cfg['comment_selector'] and cfg.get('capture_artist_comments', True) and not getattr(page,'_wv_native_ready',False):
            if await root.locator(cfg['comment_selector']).count():
                raise ValueError('帖子卡片包含评论列表，会造成重复截图；请重新校准帖子卡片范围。')
            if not cfg['comment_text_selector']:
                raise ValueError('设置了评论卡片选择器后，还需设置评论原文选择器。')
            candidates = page.locator(cfg['comment_selector'])
            if await candidates.count() > 200:
                raise ValueError('评论选择器匹配过多，请缩小为单条评论卡片。')
            for i in range(await candidates.count()):
                card = candidates.nth(i)
                if await card.is_visible() and await card.locator(cfg['artist_selector']).count():
                    items.append((card, cfg['comment_text_selector'], f'艺人评论 {len(items)}', cfg['author_selector'], {}))
            if len(items) > 201:
                raise ValueError('艺人评论超过 200 条，请分批处理。')
        counts = await read_comment_counts(page, cfg)
        if cfg.get('capture_artist_comments', True) and counts['artist'] and not counts['artist']['approximate']:
            expected = counts['artist']['value']
            if expected != len(items) - 1:
                raise ValueError(f'网页显示 {expected} 条艺人评论，本次仅读取 {len(items)-1} 条。已停止，避免译文错配；请检查展开状态或评论列表加载。')
        avatar_sources = await artist_avatar_sources(root, cfg['author_selector'], native, include_main=cfg.get('post_kind')!='fan') if native else {}
        fragments, slots, offset = [], [], 0
        gap = 0 if native else round(16 * await page.evaluate('devicePixelRatio'))
        css_width = await root.evaluate('el=>el.getBoundingClientRect().width')
        for index, (card, text_selector, label, author_selector, metadata) in enumerate(items):
            self.phase=f'{label}图片读取'
            if metadata:
                await card.scroll_into_view_if_needed()
                # Let the source viewer replace a lazy placeholder after scrolling.
                await page.wait_for_timeout(100)
                fresh_avatar = await avatar_source(card, AUTHOR, '.comment-item-_-image_area img')
                if fresh_avatar['src'] and fresh_avatar['author']:
                    avatar_sources[fresh_avatar['author']] = fresh_avatar['src']
                card = await stage_comment(page, metadata, css_width,
                                           f'艺人评论 · {len(native)}' if index == 1 else '', avatar_sources)
            text_node = card.locator(text_selector)
            if await text_node.count() != 1:
                raise ValueError(f'{label}原文选择器必须匹配且仅匹配一个完整文本块。')
            await card.scroll_into_view_if_needed()
            if cfg['expand_selector']:
                buttons = card.locator(cfg['expand_selector'])
                for j in range(min(await buttons.count(), 10)):
                    if await buttons.nth(j).is_visible():
                        await buttons.nth(j).click(timeout=3000)
                        await page.wait_for_timeout(200)
            if await text_node.evaluate('(el) => el.scrollHeight > el.clientHeight + 3'):
                raise ValueError(f'{label}原文仍被折叠或截断，请展开全文或手动截图。')
            emojis = await prepare_emoji_text(text_node)
            # Load images inside the selected card before taking screenshot; failed loads are blockers.
            await card.evaluate('''async el => {
                for (const img of el.querySelectorAll('img')) {
                    img.loading = 'eager';
                    if (!img.complete) await Promise.race([
                        new Promise(r => { img.addEventListener('load',r,{once:true}); img.addEventListener('error',r,{once:true}); }),
                        new Promise(r => setTimeout(r,10000))
                    ]);
                    if (!img.complete || !img.naturalWidth) throw new Error('image incomplete');
                }
            }''')
            if metadata:
                await ensure_comment_avatar(card)
            elif native and cfg.get('post_kind')!='fan':
                main_avatar = await avatar_source(root, cfg['author_selector'], '.avatar-decorator-_-image img, .community-artist-postId-_-header img')
                if main_avatar['author'] and main_avatar['src']:
                    avatar_sources[main_avatar['author']] = main_avatar['src']
            await page.evaluate('document.fonts.ready')
            if await text_node.evaluate('(el) => el.scrollHeight > el.clientHeight + 3'):
                raise ValueError(f'{label}正文区域无法完整显示表情，请检查正文高度或手动截图。')
            text = (await text_node.inner_text()).strip()
            author_node = card.locator(author_selector)
            if await author_node.count() != 1:
                raise ValueError(f'{label}作者选择器必须匹配一个作者姓名或身份元素。')
            await ensure_author_visible(card, author_node)
            author = (await author_node.get_attribute('data-member-id') or
                      await author_node.get_attribute('data-author-id') or
                      await author_node.evaluate('el=>Array.from(el.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join("")') or
                      await author_node.inner_text()).strip()
            if not author:
                raise ValueError(f'{label}无法读取作者身份，停止历史匹配。请校准作者选择器。')
            if not text:
                raise ValueError(f'{label}没有可插入译文的正文，请手动截图指定位置。')
            # Consent can also appear late, after media/fonts finish loading.
            # Dismiss it before measuring the final screenshot/translation coordinates.
            self.phase=f'{label}截图'
            await reject_optional_consent(page)
            if index == 0 and not native:
                await add_count_row(card, counts)
            # Page screenshot clips start at the current viewport origin. Keep
            # that origin at document (0, 0) before measuring document coordinates,
            # including cards taller than the viewport and comments further down.
            await page.evaluate('window.scrollTo(0,0)')
            native_model = None
            if metadata:
                native_model = await comment_model(page, card, metadata,
                                                   f'艺人评论 · {len(native)}' if index == 1 else '')
                isolated = await page.context.new_page()
                try:
                    await isolated.set_viewport_size({'width': max(240, round(css_width)), 'height': 1000})
                    raw = await render_comment(isolated, css_width, native_model)
                    boxes = await measure_card(isolated.locator('#card'), '#original')
                    boxes['target_height'] = boxes['h']
                    boxes['padding'] = 16
                finally:
                    await isolated.close()
            else:
                boxes = await measure_card(card, text_selector)
                raw = await screenshot_card(page, boxes, card)
            # An asynchronously arriving CMP must be rejected before accepting
            # pixels; recapture after it closes instead of saving a covered image.
            if await reject_optional_consent(page) and not metadata:
                boxes = await measure_card(card, text_selector)
                raw = await screenshot_card(page, boxes, card)
            image = Image.open(io.BytesIO(raw)).convert('RGB')
            scale = image.width / boxes['w']
            wanted_height = round(boxes['target_height'] * scale)
            if wanted_height > image.height:
                padded = Image.new('RGB', (image.width, wanted_height), 'white')
                padded.paste(image)
                image = padded
            if boxes['y'] < 0 or boxes['y'] > boxes['h']:
                raise ValueError('原文位置不在卡片范围内，请重新校准。')
            y = offset + round(boxes['y'] * scale)
            comment_id = metadata.get('comment_id', '')
            fingerprint = hashlib.sha256(f'{url}\n{label == "正文"}\n{comment_id}\n{author}\n{text}'.encode()).hexdigest()
            decor = await decoration(card) if metadata else {}
            if decor.get('frame'):
                decor['frame'] = {**decor['frame'], 'left': round(decor['frame']['left'] * scale),
                                  'right': round(decor['frame']['right'] * scale)}
            if decor.get('connector_x') is not None:
                decor['connector_x'] = round(decor['connector_x'] * scale)
            slots.append({'key': str(index), 'label': label, 'y': y, 'text': text,
                          'emojis': emojis, 'comment_id': comment_id,
                          **{k: v for k, v in metadata.items() if k != 'card'}, **decor,
                          'x': round(boxes['x'] * scale), 'width': round(boxes['text_width'] * scale),
                          'font_size': boxes['font_size'] * scale,
                          'scale': scale, 'bottom_padding': round(boxes['padding'] * scale),
                          'trailing_text': boxes['trailing_text'],
                          **({'native_card': native_model, 'fragment_top': offset, 'fragment_height': image.height} if native_model else {}),
                          **({'comment_counts': counts, 'post_kind':cfg.get('post_kind','artist')} if index == 0 else {}),
                          'fingerprint': fingerprint, 'author': author,
                          'reusable': bool(store.memory(fingerprint, group))})
            fragments.append(image)
            offset += image.height + gap
        width = max(f.width for f in fragments)
        height = sum(f.height for f in fragments) + gap * (len(fragments) - 1)
        if width * height > 24_000_000 or height > 30000:
            raise ValueError('长图过大，请分批抓取。')
        combined = Image.new('RGB', (width, height), 'white')
        y = 0
        for im in fragments:
            combined.paste(im, (0, y))
            y += im.height + gap
        name = f'originals/{uuid.uuid4().hex}.png'
        combined.save(store.DATA / name)
        note = '仅记录本次页面中已加载、匹配艺人标识的卡片；不保证未加载或折叠的评论完整。'
        unknown = sum(not r.get('grouping_known', True) for r in native)
        if unknown:
            note += f' {unknown} 条楼中楼未提供父评论编号，已保留缩进和时间顺序，未猜测归属。'
        if not reached_end:
            note += '已达到滚动上限，请检查是否还有未加载内容。'
        self.login_state = '已成功读取当前艺人动态（不代表已登录或可读取需账号权限的内容）'
        self.last_error = ''
        return store.add_post(url, slots[0]['text'][:60],
                              group, name, slots, 'weverse', note)

    async def discover(self):
        cfg = store.settings()
        if not cfg['feed_link_selector'] or not cfg['artist_selector']:
            raise ValueError('自动记录需要先配置动态列表中的艺人帖子链接选择器和艺人标识。')
        async with self.lock:
            page = await self.ready_page(weverse_url(cfg['feed_url'], feed=True))
            try:
                links = await page.locator(cfg['feed_link_selector']).evaluate_all(
                    '(els) => els.slice(0,30).map(e=>e.href).filter(Boolean)')
                urls = []
                for link in links:
                    try:
                        url = weverse_url(link)
                        if url not in urls:
                            urls.append(url)
                    except ValueError:
                        continue
                if not urls:
                    raise ValueError('动态列表没有匹配到有效帖子链接；请检查登录和列表选择器。')
                return urls
            finally:
                await page.close()


browser = Browser()
