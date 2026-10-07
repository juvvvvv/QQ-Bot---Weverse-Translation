"""Render literal translation text with Chromium's wrapping and color emoji."""
import asyncio
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright
from .page_cleanup import prepare_emoji_text


FONT_URL = 'https://qqbot-font.invalid/local-font'
HTML = '''<!doctype html><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'; font-src https://qqbot-font.invalid">
<style>
html,body{margin:0;padding:0;background:transparent}
#band{box-sizing:border-box;background:white}
#text{margin:0;color:#111;font-family:QQBotCustom,"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
 line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere}
</style><div id="band"><p id="text"></p></div>'''


async def _render(width, jobs, font_path):
    path = Path(font_path or os.environ.get('WEVERSE_FONT', ''))
    if font_path and not path.is_file():
        raise ValueError('自定义字体文件不存在，请检查字体路径或清空以使用系统字体。')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, executable_path=os.environ.get('WEVERSE_BROWSER_EXECUTABLE') or None,
        )
        try:
            page = await browser.new_page(
                viewport={'width': width, 'height': 1000}, device_scale_factor=1,
            )

            async def local_resources(route):
                if route.request.url == FONT_URL and path.is_file():
                    await route.fulfill(path=str(path), headers={'Access-Control-Allow-Origin': '*'})
                else:
                    await route.abort()

            # Rendering never visits Weverse, uses the login profile or fetches text URLs.
            await page.route('**/*', local_resources)
            await page.set_content(HTML)
            if path.is_file():
                loaded = await page.evaluate('''async url => {
                    try {
                        const font = await new FontFace('QQBotCustom', `url("${url}")`).load();
                        document.fonts.add(font);
                        return true;
                    } catch { return false; }
                }''', FONT_URL)
                if font_path and not loaded:
                    raise ValueError('浏览器无法加载自定义字体，请清空字体路径使用系统字体，或选择可用的 TTF/OTF 字体。')
            images = []
            for job in jobs:
                await page.evaluate('''({width, text, size, x, text_width, centered, color, outline_color, outline_width=0}) => {
                    const band = document.getElementById('band');
                    const p = document.getElementById('text');
                    band.style.width = width + 'px';
                    band.style.padding = Math.max(6, Math.round(size * .45), outline_width+4) + 'px 0';
                    band.style.background = centered ? 'transparent' : 'white';
                    p.removeAttribute('style');
                    p.style.fontSize = size + 'px';
                    p.style.lineHeight = Math.max(size*1.65, size+outline_width*2+4) + 'px';
                    p.style.color = color || '#111111';
                    p.style.webkitTextStroke = outline_width + 'px ' + (outline_color || '#ffffff');
                    p.style.paintOrder = 'stroke fill';
                    p.style.marginLeft = x + 'px';
                    p.style.width = text_width + 'px';
                    p.style.textAlign = centered ? 'center' : 'left';
                    // User translations are text, never HTML or executable markup.
                    p.textContent = text;
                }''', {'width': width, **job})
                await prepare_emoji_text(page.locator('#text'))
                await page.evaluate('document.fonts.ready')
                height = await page.locator('#band').evaluate('el=>el.getBoundingClientRect().height')
                if height > 40000 or width * height > 24_000_000:
                    raise ValueError('译文排版过长，请减少译文或分开处理。')
                raw = await page.locator('#band').screenshot(
                    type='png', omit_background=bool(job.get('centered')), animations='disabled',
                )
                with Image.open(io.BytesIO(raw)) as image:
                    images.append(image.convert('RGBA' if job.get('centered') else 'RGB'))
            return images
        finally:
            await browser.close()


def render_text_images(width, jobs, font_path=''):
    """Run in a separate thread, including callers already inside an async loop."""
    if not jobs:
        return []

    def run():
        # Windows subprocesses require a Proactor loop even when the web server
        # uses a Selector loop. Runner is available on every supported Python.
        factory = asyncio.ProactorEventLoop if sys.platform == 'win32' else None
        with asyncio.Runner(loop_factory=factory) as runner:
            return runner.run(_render(width, jobs, font_path))

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(run).result()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('译文排版失败，请确认首次安装已完成、Chromium 可以启动以及字体可用。') from exc
