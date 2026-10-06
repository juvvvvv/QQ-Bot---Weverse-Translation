"""Insert translation bands without resizing, covering or repainting source pixels."""
import io
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from . import store

MAX_PIXELS = 24_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def font(size, path=''):
    candidates = [path, os.environ.get('WEVERSE_FONT', ''),
                  '/System/Library/Fonts/PingFang.ttc',
                  '/System/Library/Fonts/STHeiti Light.ttc',
                  '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    raise ValueError('没有可用字体。请在设置里填写中文字体文件的完整路径。')


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


def wrap(text, draw, f, width):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for char in paragraph:
            if line and draw.textlength(line + char, font=f) > width:
                lines.append(line)
                line = char
            else:
                line += char
        lines.append(line)
    return lines


def band(width, label, text, size, font_path=''):
    pad = max(18, width // 32)
    body, heading = font(size, font_path), font(max(12, size - 6), font_path)
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    lines = wrap(text, probe, body, width - pad * 2 - 14)
    line_height = int(size * 1.65)
    height = pad * 2 + 26 + line_height * len(lines)
    out = Image.new('RGB', (width, height), '#f1f7f5')
    draw = ImageDraw.Draw(out)
    draw.rectangle((pad, pad, pad + 3, height - pad), fill='#297e69')
    draw.text((pad + 14, pad), label, font=heading, fill='#4c786d')
    for i, line in enumerate(lines):
        draw.text((pad + 14, pad + 28 + i * line_height), line, font=body, fill='#183c32')
    return out


def compose(original, slots, translations, watermark, size=24, font_path='', logo_config=None):
    source = Image.open(original).convert('RGB')
    if len(translations) > 30 or sum(len(t) for t in translations.values()) > 20000:
        raise ValueError('单张图片最多 30 段翻译，合计最多 20000 字。')
    if set(translations) - {str(s['key']) for s in slots}:
        raise ValueError('翻译位置不存在。')
    insertions = []
    for slot in slots:
        text = translations.get(str(slot['key']), '').strip()
        if text:
            y = int(slot['y'])
            if y < 0 or y > source.height:
                raise ValueError('翻译插入位置超出了截图高度。')
            insertions.append((y, band(source.width, f"中文翻译 · {slot['label']}", text, size, font_path)))
    insertions.sort(key=lambda x: x[0])
    footer = band(source.width, '翻译整理', watermark or 'PLAVE · 中文翻译', max(12, size - 6), font_path)
    logo_area = 0
    if logo_config and logo_config.get('enabled') and logo_config.get('logo') and logo_config['position'] == 'footer':
        logo = Image.open(store.DATA / logo_config['logo'])
        logo_area = min(1000, round(source.width * logo_config['width_pct'] / 100 * logo.height / logo.width)) + 2 * logo_config['margin']
    height = source.height + sum(b.height for _, b in insertions) + footer.height + logo_area
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
    result.paste(footer, (0, height - footer.height - logo_area))
    if logo_config:
        from .watermark import overlay_logo
        result = overlay_logo(result, logo_config, height - logo_area if logo_area else height - footer.height)
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
