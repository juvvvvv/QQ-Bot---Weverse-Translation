"""Insert white translation rows; preserve source pixels before optional watermarks."""
import io
from PIL import Image, ImageOps, ImageDraw, UnidentifiedImageError
from . import store
from .text_image import render_text_images
from .watermark import text_job, apply_text

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


def compose(original, slots, translations, watermark, size=24, font_path=''):
    if isinstance(original, Image.Image):
        source = original.convert('RGB')
    else:
        with Image.open(original) as image:
            source = image.convert('RGB')
    if len(translations) > 201 or sum(len(t) for t in translations.values()) > 50000:
        raise ValueError('单张图片最多 201 段翻译，合计最多 50000 字。')
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
            pixel_scale = float(slot.get('scale', 1))
            text_size = max(12 * pixel_scale, min(48 * pixel_scale, float(slot.get('font_size', size))))
            positions.append(y)
            jobs.append({'text': text, 'size': text_size, 'x': x, 'text_width': text_width})
    scale = float(slots[0].get('scale', 1)) if slots else 1
    body_size = jobs[0]['size'] if jobs else (
        max(12 * scale, min(48 * scale, float(slots[0].get('font_size', size * scale))))
        if slots else size * scale)
    cfg = store.WATERMARK_DEFAULTS | (watermark if isinstance(watermark, dict) else {'text': watermark, 'position': 5})
    mark_job = text_job(source.width, cfg, body_size, scale)
    if mark_job:
        jobs.append(mark_job)
    images = render_text_images(source.width, jobs, font_path)
    mark = images.pop() if mark_job else None
    translated_slots = [s for s in slots if translations.get(str(s['key']), '').strip()]
    for index, slot in enumerate(translated_slots):
        if slot.get('trailing_text'):
            ink = ImageOps.invert(images[index]).getbbox()
            if ink:
                images[index] = images[index].crop((0, 0, images[index].width, ink[3]))
        # Extend frame sides and reply connectors through the inserted row.
        draw = ImageDraw.Draw(images[index])
        stroke = max(1, round(float(slot.get('scale', 1))))
        frame = slot.get('frame')
        if frame:
            for x in (frame['left'], frame['right']):
                draw.line((x, 0, x, images[index].height), fill=frame['color'], width=stroke)
        if slot.get('connector_x') is not None:
            x = slot['connector_x']
            draw.line((x, 0, x, images[index].height), fill='#e5e9f2', width=stroke)
    insertions = list(zip(positions, images))
    insertions.sort(key=lambda x: x[0])
    height = source.height + sum(b.height for _, b in insertions)
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
    result = apply_text(result, mark, cfg, scale,
                        bottom_padding=slots[-1].get('bottom_padding', 0) if slots else 0)
    if result.height > 40000 or result.width * result.height > 48_000_000:
        raise ValueError('包含水印的图片过大，请降低像素倍率。')
    return result


def _render_post(post_id, translations, reuse=False, merge=False, publish=False):
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
                     store.watermark(post['group_id']), cfg['font_size'], cfg['font_path'])
    # Unique output names avoid stale browser caches and preserve older revision files.
    import uuid
    name = f'outputs/{post_id}-{uuid.uuid4().hex[:8]}.png'
    result.save(store.DATA / name)
    keys = {str(s['key']) for s in post['slots']}
    complete = keys <= selected.keys()
    if publish:
        try:
            updated = store.publish_latest(post_id, selected, name, 'translated' if complete else 'partial')
        except Exception:
            (store.DATA / name).unlink(missing_ok=True)
            raise
    else:
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


def render_post(post_id, translations, reuse=False, merge=False, publish=False):
    with render_lock:
        return _render_post(post_id, translations, reuse, merge, publish)
