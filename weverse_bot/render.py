"""Insert white translation rows; preserve source pixels before optional watermarks."""
import io
from PIL import Image, ImageOps, UnidentifiedImageError
from . import store
from .text_image import render_text_images

MAX_PIXELS = 24_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def read_image(content):
    try:
        im = Image.open(io.BytesIO(content))
        if im.format not in ('PNG', 'JPEG', 'WEBP'):
            raise ValueError('请上传 PNG、JPEG 或 WebP 静态截图。')
        if im.width * im.height > MAX_PIXELS or im.width < 240 or im.width > 4000 or im.height > 30000:
            raise ValueError('截图尺寸需为宽 240–4000 像素，高不超过 30000，且不超过 2400 万像素。')
        im = ImageOps.exif_transpose(im)
        if im.mode == 'RGBA':
            bg = Image.new('RGB', im.size, 'white')
            bg.paste(im, mask=im.getchannel('A'))
            return bg
        return im.convert('RGB')
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError) as exc:
        raise ValueError('无法读取图片或图片过大。') from exc


def band(width, label, text, size, font_path=''):
    # Labels belong in the editor, rather than inside the translated image.
    pad = max(16, width // 32)
    return render_text_images(width, [{'text': text, 'size': size, 'x': pad,
                                      'text_width': width - pad * 2}], font_path)[0]


def compose(original, slots, translations, watermark, size=24, font_path='', logo_config=None):
    with Image.open(original) as image:
        source = image.convert('RGB')
    if len(translations) > 30 or sum(len(t) for t in translations.values()) > 20000:
        raise ValueError('单张图片最多 30 段翻译，合计最多 20000 字。')
    if set(translations) - {str(s['key']) for s in slots}:
        raise ValueError('翻译位置不存在。')
    positions, jobs = [], []
    for slot in slots:
        text = translations.get(str(slot['key']), '').strip()
        if text:
            y = int(slot['y'])
            if y < 0 or y > source.height:
                raise ValueError('翻译插入位置超出了截图高度。')
            x = int(slot.get('x', max(16, source.width // 32)))
            text_width = int(slot.get('width', source.width - x * 2))
            if x < 0 or text_width < 24 or x + text_width > source.width:
                raise ValueError('正文边距超出了截图宽度，请重新读取网页或重新选择插入位置。')
            text_size = max(12, min(48, float(slot.get('font_size', size))))
            positions.append(y)
            jobs.append({'text': text, 'size': text_size, 'x': x, 'text_width': text_width})
    if watermark:
        jobs.append({'text': watermark, 'size': max(12, size - 6), 'x': 16,
                     'text_width': source.width - 32, 'centered': True})
    images = render_text_images(source.width, jobs, font_path)
    mark = images.pop() if watermark else None
    insertions = list(zip(positions, images))
    insertions.sort(key=lambda x: x[0])
    logo_area = 0
    if logo_config and logo_config.get('enabled') and logo_config.get('logo') and logo_config['position'] == 'footer':
        logo = Image.open(store.DATA / logo_config['logo'])
        logo_area = min(1000, round(source.width * logo_config['width_pct'] / 100 * logo.height / logo.width)) + 2 * logo_config['margin']
    height = source.height + sum(b.height for _, b in insertions) + logo_area
    if height > 40000 or source.width * height > 48_000_000:
        raise ValueError('合成图片过长，请减少译文或分开处理。')
    result = Image.new('RGB', (source.width, height), 'white')
    cursor = target = 0
    for y, segment in insertions:
        crop = source.crop((0, cursor, source.width, y))
        result.paste(crop, (0, target))
        target += y - cursor
        result.paste(segment, (0, target))
        target += segment.height
        cursor = y
    result.paste(source.crop((0, cursor, source.width, source.height)), (0, target))
    if mark is not None:
        mark.putalpha(mark.getchannel('A').point(lambda alpha: round(alpha * .2)))
        layer = result.convert('RGBA')
        layer.alpha_composite(mark, (0, (height - mark.height) // 2))
        result = layer.convert('RGB')
    if logo_config:
        from .watermark import overlay_logo
        result = overlay_logo(result, logo_config, height - logo_area if logo_area else height)
    return result


def _render_post(post_id, translations, reuse=False, merge=False):
    post = store.get_post(post_id)
    if post['status'] == 'ignored':
        raise ValueError('此档案已忽略，请先恢复待翻译状态。')
    if any(not isinstance(v, str) or len(v) > 10000 for v in translations.values()):
        raise ValueError('每段翻译需为文字，最多 10000 字。')
    selected = (post['translations'] | translations) if merge else dict(translations)
    if reuse:
        for s in post['slots']:
            if not selected.get(str(s['key'])) and s.get('fingerprint'):
                previous = store.memory(s['fingerprint'], post['group_id'])
                if previous:
                    selected[str(s['key'])] = previous
    selected = {k: v.strip() for k, v in selected.items() if v.strip()}
    if not selected:
        raise ValueError('请填写至少一段翻译，或先保存可以复用的历史译文。')
    cfg = store.settings()
    result = compose(store.DATA / post['original'], post['slots'], selected,
                     cfg['watermark'], cfg['font_size'], cfg['font_path'], store.watermark(post['group_id']))
    # Unique output names avoid stale browser caches and preserve older revision files.
    import uuid
    name = f'outputs/{post_id}-{uuid.uuid4().hex[:8]}.png'
    result.save(store.DATA / name)
    keys = {str(s['key']) for s in post['slots']}
    complete = keys <= selected.keys()
    updated = store.update_post(post_id, translations=selected, output=name,
                                status='translated' if complete else 'partial')
    for s in post['slots']:
        if s.get('fingerprint') and selected.get(str(s['key'])):
            store.remember(s['fingerprint'], selected[str(s['key'])], post['group_id'])
    store.event(f'生成翻译图：{post_id}（{len(selected)}/{len(keys)} 段）')
    return updated


# Serialize image revisions and memory writes, including simultaneous WebUI / QQ tasks.
import threading
render_lock = threading.RLock()


def render_post(post_id, translations, reuse=False, merge=False):
    with render_lock:
        return _render_post(post_id, translations, reuse, merge)
