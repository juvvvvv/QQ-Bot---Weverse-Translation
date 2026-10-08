"""Measure visible content, not a site's minimum-height spacer or loaded item count."""
import math
import re
import time

def parse_count(text):
    display = text.strip()
    match = re.fullmatch(r'([0-9]+(?:[.,][0-9]+)*)\s*([kKmM万亿]?)', display)
    if not match:
        return None
    number, unit = match.groups()
    number = number.replace(',', '')
    if number.count('.') > 1:
        return None
    factor = {'k': 1000, 'm': 1_000_000, '万': 10_000, '亿': 100_000_000}.get(unit.lower(), 1)
    value = float(number) * factor
    if not math.isfinite(value) or value < 0 or (not unit and not value.is_integer()):
        return None
    return {'display': display, 'value': round(value), 'approximate': bool(unit)}


async def read_comment_counts(page, cfg):
    result = {'artist': None, 'captured_at': time.time(), 'warnings': []}
    for key, field in (('artist', 'artist_comment_count_selector'),):
        selector = cfg.get(field) or '.base-comment-artist-count-and-toggle-_-count'
        try:
            nodes = page.locator(selector)
            all_nodes = [nodes.nth(i) for i in range(min(await nodes.count(), 20))]
            visible = [node for node in all_nodes if await node.is_visible()]
            values = [parse_count(await node.text_content() or '') for node in (visible or all_nodes)]
            unique = {v['display']: v for v in values if v is not None}
            if len(unique) == 1:
                result[key] = next(iter(unique.values()))
            if len(unique) > 1 or (all_nodes and result[key] is None):
                result['warnings'].append(f'{field} 无法唯一读取计数，请校准计数元素。')
        except Exception:
            result['warnings'].append(f'{field} 无法读取，请检查选择器。')
    return result


async def add_count_row(card, counts):
    parts = []
    for key, label in (('artist', '艺人评论'),):
        if counts[key] is not None:
            parts.append(f"{label} {counts[key]['display']}")
    if parts:
        await card.evaluate('''(el, text) => {
            let row = el.querySelector('[data-wvbot-counts]');
            if (!row) { row = document.createElement('div'); row.dataset.wvbotCounts=''; el.append(row); }
            row.style.cssText='font-size:12px;line-height:1.6;color:#667085;margin-top:12px;clear:both';
            row.textContent=text;
        }''', ' · '.join(parts))


async def measure_card(card, text_selector):
    return await card.evaluate(r'''(el, selector) => {
        const a=el.getBoundingClientRect(), text=el.querySelector(selector), b=text.getBoundingClientRect();
        const visible = n => {
            if (n.closest('script,style,noscript,.blind,[aria-hidden="true"]')) return false;
            if (!n.getClientRects().length) return false;
            for (let p=n; p; p=p.parentElement) {
                const s=getComputedStyle(p);
                if (s.display==='none' || s.visibility==='hidden' || s.opacity==='0') return false;
                if (p===el) break;
            }
            return true;
        };
        const rects=[], originalRects=[];
        const add = r => { if(r.width && r.height && r.right>a.left && r.left<a.right && r.bottom>a.top) rects.push(r); };
        const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
        while(walker.nextNode()) {
            const node=walker.currentNode;
            if(!node.data.trim() || !visible(node.parentElement)) continue;
            const range=document.createRange(); range.selectNodeContents(node);
            for(const r of range.getClientRects()) add(r);
        }
        const originalWalker=document.createTreeWalker(text,NodeFilter.SHOW_TEXT);
        while(originalWalker.nextNode()) {
            const range=document.createRange();range.selectNodeContents(originalWalker.currentNode);
            originalRects.push(...range.getClientRects());
        }
        const originalBottom=Math.max(b.bottom,...originalRects.map(r=>r.bottom));
        for(const n of el.querySelectorAll('img,video,canvas,svg,[role="img"],.WidgetMedia,[data-wvbot-counts],[data-wvbot-frame],.toolbar-_-container,.comment-item-_-interaction')) {
            if(visible(n)) add(n.getBoundingClientRect());
        }
        const first=rects.length ? Math.min(...rects.map(r=>r.top)) : b.top;
        const bottom=Math.max(originalBottom,...rects.map(r=>r.bottom));
        const padding=Math.max(0, Math.ceil(first-a.top));
        return {w:a.width, h:a.height, left:a.left+scrollX, top:a.top+scrollY,
                y:originalBottom-a.top, x:b.left-a.left, text_width:b.width,
                font_size:parseFloat(getComputedStyle(text).fontSize),
                content_bottom:bottom-a.top, padding, target_height:bottom-a.top+padding,
                trailing_text:Math.abs(bottom-originalBottom)<1, scale:devicePixelRatio};
    }''', text_selector)


async def screenshot_card(page, bounds):
    height = min(bounds['h'], bounds['target_height'])
    if height <= 0 or bounds['content_bottom'] > bounds['h'] + 3:
        raise ValueError('动态内容超出了所选卡片范围，请重新校准卡片选择器。')
    if bounds['target_height'] * bounds['w'] * bounds['scale'] ** 2 > 24_000_000 or bounds['target_height'] * bounds['scale'] > 30000:
        raise ValueError('高清截图过大，请降低截图像素倍率或分批读取评论。')
    raw = await page.screenshot(
        full_page=True,
        type='png', clip={'x': bounds['left'], 'y': bounds['top'], 'width': bounds['w'], 'height': height},
        scale='device', animations='disabled', timeout=20000,
    )
    return raw
