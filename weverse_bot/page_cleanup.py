"""Prepare public Weverse cards for screenshots without site chrome covering them."""
import re
import time


REJECT_NAMES = re.compile(
    r'^(?:Do not consent|Reject all|Deny all|不同意|全部拒绝|拒绝全部|'
    r'Nicht zustimmen|Alle ablehnen)\s*$', re.I,
)
CONSENT_TITLE = re.compile(
    r'Weverse\s+asks for your consent|Weverse\s+bittet um Ihre Zustimmung|'
    r'Weverse.{0,20}请求您同意|Weverse.{0,20}征求您的同意', re.I,
)

# These selectors came from the user's loaded Weverse DOM, rather than a guess
# that every header/dialog belongs to site navigation. The artist header is separate.
SITE_CHROME = '''
.global-_-header,
.global-header-_-container,
.login-required-bottom-layer-_-login_required_wrap {
    display: none !important;
}
'''


async def _consent_visible(page):
    for frame in page.frames:
        if frame.is_detached():
            continue
        notices = frame.get_by_text(CONSENT_TITLE)
        for index in range(min(await notices.count(), 5)):
            if await notices.nth(index).is_visible():
                return True
    return False


async def reject_optional_consent(page, wait_ms=0):
    """Click an explicit rejection button, including inside a consent iframe.

    Consent itself is never hidden with CSS, and an acceptance button is never
    clicked. Chromium's persistent profile stores the site's resulting preference.
    """
    deadline = time.monotonic() + wait_ms / 1000
    while True:
        for frame in page.frames:
            if frame.is_detached():
                continue
            buttons = frame.get_by_role('button', name=REJECT_NAMES)
            for index in range(min(await buttons.count(), 5)):
                button = buttons.nth(index)
                if not await button.is_visible():
                    continue
                layer_handle = await button.evaluate_handle('''el => {
                    let layer=el.closest('[role="dialog"], dialog, [aria-modal="true"]');
                    for(let n=el.parentElement;n && n!==document.body;n=n.parentElement) {
                        if(getComputedStyle(n).position==='fixed') layer=n;
                    }
                    return layer;
                }''')
                layer = layer_handle.as_element()
                try:
                    await button.click(timeout=3000)
                    if not frame.is_detached():
                        await button.wait_for(state='hidden', timeout=5000)
                        if layer:
                            await layer.wait_for_element_state('hidden', timeout=5000)
                except Exception as exc:
                    if not frame.is_detached():
                        raise ValueError(
                            '自动选择 Do not consent 或关闭弹窗失败，已停止截图。'
                            '请在工作台打开的浏览器中手动拒绝同意后重试。'
                        ) from exc
                finally:
                    await layer_handle.dispose()
                # Wait for the backdrop/fade-out after the rejection button closes.
                for _ in range(20):
                    if not await _consent_visible(page):
                        await page.wait_for_timeout(150)
                        return
                    await page.wait_for_timeout(100)
                raise ValueError('拒绝同意后弹窗仍未关闭，已停止截图，请检查工作台浏览器。')
        if time.monotonic() >= deadline:
            if await _consent_visible(page):
                raise ValueError(
                    '检测到隐私同意弹窗，但未找到明确的拒绝按钮，已停止截图。'
                    '请提供该弹窗拒绝按钮的 outerHTML，或先在工作台浏览器中手动选择。'
                )
            return
        await page.wait_for_timeout(150)


async def remove_site_chrome(page):
    """Hide identified site navigation and the floating login invitation only."""
    await page.add_style_tag(content=SITE_CHROME)


async def prepare_emoji_text(text_node):
    """Give complete emoji graphemes a color font and room inside the line box.

    Do not change the original code points or unlock genuinely truncated text.
    Only the selected original text block is modified, never the media/header.
    """
    return await text_node.evaluate(r'''el => {
        const segmenter = new Intl.Segmenter(undefined, {granularity: 'grapheme'});
        const emoji = /[\p{Extended_Pictographic}\p{Regional_Indicator}\u20e3]/u;
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        const found = [];
        const containers = new Set([el]);
        for (const node of nodes) {
            if (node.parentElement.closest('script, style, svg')) continue;
            const existing = node.parentElement.closest('[data-wvbot-emoji]');
            if (existing) {
                found.push(node.data);
                continue;
            }
            const parts = [...segmenter.segment(node.data)];
            const isEmoji = value => emoji.test(value) && !value.includes('\ufe0e');
            if (!parts.some(part => isEmoji(part.segment))) continue;
            const fragment = document.createDocumentFragment();
            containers.add(node.parentElement);
            for (const {segment} of parts) {
                if (!isEmoji(segment)) {
                    fragment.append(document.createTextNode(segment));
                    continue;
                }
                found.push(segment);
                const span = document.createElement('span');
                span.dataset.wvbotEmoji = '';
                span.textContent = segment;
                span.style.cssText = 'font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif!important;'
                    + 'font-size:inherit!important;display:inline-block!important;'
                    + 'line-height:1.3!important;padding:.08em .04em!important;'
                    + 'vertical-align:baseline!important;white-space:nowrap!important;'
                    + 'overflow:visible!important;';
                fragment.append(span);
            }
            node.replaceWith(fragment);
        }
        if (found.length) {
            for (const container of containers) {
                const style = getComputedStyle(container);
                const minimum = parseFloat(style.fontSize) * 1.6;
                const current = parseFloat(style.lineHeight);
                if (!Number.isFinite(current) || current < minimum) {
                    container.style.setProperty('line-height', minimum + 'px', 'important');
                }
            }
        }
        return found;
    }''')


async def ensure_author_visible(card, author):
    """Keep the real author header/avatar, including the responsive layout."""
    header = card.locator('.community-artist-postId-_-header')
    if await header.count() == 1 and not await header.is_visible():
        await header.evaluate('''el => {
            el.style.setProperty('display', 'block', 'important');
            el.style.setProperty('visibility', 'visible', 'important');
            el.style.setProperty('opacity', '1', 'important');
        }''')
    if not await author.is_visible():
        raise ValueError('作者名称在当前截图区域不可见，已停止截图，请重新校准动态卡片范围。')
    if await header.count() == 1:
        avatar = header.locator('.avatar-decorator-_-image img')
        if await avatar.count() != 1 or not await avatar.is_visible():
            raise ValueError('发帖者头像在当前截图区域不可见，已停止截图，请检查页面布局。')
