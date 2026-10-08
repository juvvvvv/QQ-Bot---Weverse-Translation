"""Text watermarks only; apply once, after the final post/comment composition."""
from . import store


def text_job(width, cfg, body_size, scale):
    cfg = store.validate_watermark(store.WATERMARK_DEFAULTS | cfg)
    if not cfg['enabled'] or not cfg['text'].strip() or cfg['transparency'] == 100:
        return None
    margin = max(2, round(16 * scale))
    return {'text': cfg['text'], 'size': cfg['font_size'] * scale if cfg['font_size'] else body_size,
            'x': margin, 'text_width': width - margin * 2, 'centered': True,
            'color': cfg['color'], 'outline_color': cfg['outline_color'],
            'font_family': 'Arial,"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif',
            'outline_width': cfg['outline_width'] * scale if cfg['outline'] else 0}


def apply_text(image, mark, cfg, scale=1, bottom_padding=None):
    if mark is None:
        return image
    bbox = mark.getchannel('A').getbbox()
    if bbox is None:
        return image
    mark = mark.crop(bbox)
    margin = max(2, round(16 * scale))
    if mark.width > image.width - 2 * margin or mark.height > image.height - 2 * margin:
        raise ValueError('水印超出了成图空间，请减小字号、描边粗细或缩短文字。')
    opacity = 1 - cfg['transparency'] / 100
    mark.putalpha(mark.getchannel('A').point(lambda alpha: round(alpha * opacity)))
    column = (cfg['position'] - 1) % 3
    row = (cfg['position'] - 1) // 3
    x = [margin, (image.width - mark.width) // 2, image.width - margin - mark.width][column]
    y = [margin, (image.height - mark.height) // 2, image.height - margin - mark.height][row]
    if row == 2 and bottom_padding is not None:
        edge = max(2, round(4 * scale))
        footer = max(int(bottom_padding), mark.height + edge * 2)
        if footer > bottom_padding:
            from PIL import Image
            extended = Image.new('RGB', (image.width, image.height + footer - int(bottom_padding)), 'white')
            extended.paste(image)
            image = extended
        x = [edge, (image.width-mark.width)//2, image.width-edge-mark.width][column]
        y = image.height-footer+(footer-mark.height)//2
    result = image.convert('RGBA')
    result.alpha_composite(mark, (x, y))
    return result.convert('RGB')
