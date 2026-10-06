"""Per-group PNG logos and placement; incoming image URLs are downloaded by OneBot."""
import io
import uuid
from PIL import Image, ImageOps, UnidentifiedImageError
from . import store

POSITIONS = {'底部':'footer', '左上':'top-left', '右上':'top-right', '左下':'bottom-left', '右下':'bottom-right'}


def upload_logo(content, group=''):
    if len(content) > 4 * 1024 * 1024:
        raise ValueError('水印 PNG 不能超过 4 MB。')
    try:
        with Image.open(io.BytesIO(content)) as im:
            if im.format != 'PNG' or getattr(im, 'n_frames', 1) != 1:
                raise ValueError('请提供静态 PNG 水印图片，推荐透明背景。')
            if im.width * im.height > 4_000_000 or max(im.size) > 4096:
                raise ValueError('水印图片过大：最多 400 万像素，单边最多 4096 像素。')
            logo = ImageOps.exif_transpose(im).convert('RGBA')
            logo.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError('无法读取 PNG 水印。') from exc
    name = f'logos/{uuid.uuid4().hex}.png'
    logo.save(store.DATA / name)
    old = store.watermark(group)['logo']
    config = store.save_watermark(group, {'logo': name, 'enabled': True})
    if old and old != name:
        # Logos belong to exactly one group, so replacing one does not affect other groups.
        path = (store.DATA / old).resolve()
        if path.parent == (store.DATA / 'logos').resolve():
            path.unlink(missing_ok=True)
    store.event('本群 PNG 水印已更新')
    return config


def overlay_logo(image, cfg, footer_top):
    if not cfg.get('enabled', True) or not cfg.get('logo'):
        return image
    path = (store.DATA / cfg['logo']).resolve()
    if path.parent != (store.DATA / 'logos').resolve() or not path.is_file():
        raise ValueError('水印文件不存在，请重新上传 PNG。')
    logo = Image.open(path).convert('RGBA')
    margin = min(cfg['margin'], max(0, image.width // 4))
    available_h = (image.height - footer_top if cfg['position'] == 'footer' else image.height) - margin * 2
    target_w = max(1, min(image.width - 2 * margin, round(image.width * cfg['width_pct'] / 100)))
    max_h = max(1, available_h)
    ratio = min(target_w / logo.width, max_h / logo.height)
    logo = logo.resize((max(1, round(logo.width * ratio)), max(1, round(logo.height * ratio))), Image.Resampling.LANCZOS)
    alpha = logo.getchannel('A').point(lambda a: round(a * cfg['opacity'] / 100))
    logo.putalpha(alpha)
    position = cfg['position']
    x = margin if position.endswith('left') else image.width - margin - logo.width
    y = margin if position.startswith('top') else image.height - margin - logo.height
    if position == 'footer':
        x = (image.width - logo.width) // 2
        y = footer_top + max(0, (image.height - footer_top - logo.height) // 2)
    result = image.convert('RGBA')
    result.alpha_composite(logo, (x, y))
    return result.convert('RGB')
