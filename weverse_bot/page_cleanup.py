"""Prepare public Weverse cards for screenshots without site chrome covering them."""
import re
import time
from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeout


REJECT_NAMES = re.compile(
    r'^(?:Do not consent|Reject all|Deny all|不同意|全部拒绝|拒绝全部|'
    r'Nicht zustimmen|Alle ablehnen)\s*$', re.I,
)
COOKIE_REJECT_NAMES = re.compile(
    r'^(?:不同意并继续|Disagree and continue|Continue without agreeing|'
    r'Continue without consent|Ablehnen und fortfahren|拒否して続行|동의하지 않고 계속)\s*$', re.I,
)
COOKIE_LAYER = '[class*="_w_bottom_fixed_"]'
FC_LAYER = '.fc-consent-root .fc-dialog-overlay, .fc-consent-root .fc-dialog[role="dialog"]'
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


async def _frame_visible(frame):
    # A CMP often hides its iframe instead of detaching it. Its inner nodes can
    # still report visible, so inspect the embedding frames as well.
    while frame.parent_frame:
        if frame.is_detached():
            return False
        try:
            host = await frame.frame_element()
            try:
                if not await host.is_visible():
                    return False
            finally:
                await host.dispose()
        except BrowserError:
            if frame.is_detached():
                return False
            raise
        frame = frame.parent_frame
    return not frame.is_detached()


async def _consent_visible(page):
    for frame in page.frames:
        if not await _frame_visible(frame):
            continue
        layers = frame.locator(FC_LAYER)
        for index in range(min(await layers.count(), 8)):
            if await layers.nth(index).is_visible():
                return True
        cookie_layers = frame.locator(COOKIE_LAYER).filter(
            has=frame.locator('a[href*="/policies/cookie"]'))
        for index in range(min(await cookie_layers.count(), 5)):
            if await cookie_layers.nth(index).is_visible():
                return True
        notices = frame.get_by_text(CONSENT_TITLE)
        for index in range(min(await notices.count(), 5)):
            if await notices.nth(index).is_visible():
                return True
    return False


async def _wait_rejection_closed(page, frame, button, layer, deadline):
    while time.monotonic() < deadline:
        if not await _frame_visible(frame):
            return True
        try:
            layer_visible = await layer.is_visible() if layer else False
            if layer and await layer.evaluate("el => el.classList.contains('fc-consent-root')"):
                layer_visible = await layer.evaluate('''root => [...root.querySelectorAll('.fc-dialog-overlay,.fc-dialog[role="dialog"]')].some(el=>{
                    if(!el.getClientRects().length)return false;
                    for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||s.opacity==='0')return false;}
                    return true;
                })''')
            if not await button.is_visible() and not layer_visible:
                return True
        except BrowserError:
            if not await _frame_visible(frame):
                return True
            # Navigation after rejection invalidates handles. Re-scan the new
            # document rather than treating a detached execution context as consent.
            if not await _consent_visible(page):
                return True
        await page.wait_for_timeout(150)
    return False


async def reject_optional_consent(page, wait_ms=0):
    """Reject prompts, temporarily widening a clipped consent viewport if needed.

    Restore the exact capture viewport even when rejection fails.
    """
    viewport = page.viewport_size
    try:
        return await _reject_prompts(page, wait_ms, viewport)
    finally:
        if viewport and not page.is_closed() and page.viewport_size != viewport:
            await page.set_viewport_size(viewport)


async def _reject_prompts(page, wait_ms, viewport):
    """Reject each known consent prompt using the site's actual buttons.

    Keep the site's written preference in its persistent profile, and reject
    again whenever the site renews a prompt. Never force a covered click, accept
    optional data collection, or hide a remaining consent backdrop.
    """
    arrival_deadline = time.monotonic() + wait_ms / 1000
    action_deadline = None
    rejected = False
    dismissals = 0
    attempted = False
    widened = False
    while True:
        candidates, cookie_candidates = [], []
        central_visible = False
        for frame in page.frames:
            if not await _frame_visible(frame):
                continue
            central = frame.locator(FC_LAYER)
            central_visible = central_visible or any([await central.nth(i).is_visible() for i in range(min(await central.count(), 8))])
            if await frame.get_by_text(CONSENT_TITLE).count():
                central_visible = central_visible or any([await frame.get_by_text(CONSENT_TITLE).nth(i).is_visible() for i in range(min(await frame.get_by_text(CONSENT_TITLE).count(), 5))])
            # The supplied fc class identifies the refusal independently of label language.
            buttons = frame.locator('.fc-consent-root .fc-cta-do-not-consent').or_(frame.get_by_role('button', name=REJECT_NAMES))
            cookie_layers = frame.locator(COOKIE_LAYER).filter(
                has=frame.locator('a[href*="/policies/cookie"]'))
            cookie_buttons = cookie_layers.get_by_role('button', name=COOKIE_REJECT_NAMES)
            candidates.extend((frame, buttons.nth(i)) for i in range(min(await buttons.count(), 5)))
            cookie_candidates.extend((frame, cookie_buttons.nth(i)) for i in range(min(await cookie_buttons.count(), 5)))
        # The central CMP (including one in an iframe) can cover the Cookie bar.
        if not central_visible:
            candidates.extend(cookie_candidates)
        clicked = False
        for frame, locator in candidates:
            if not await _frame_visible(frame) or not await locator.is_visible():
                continue
            if action_deadline is None:
                action_deadline = time.monotonic() + 15
            if time.monotonic() >= action_deadline:
                break
            attempted = True
            # Bind to an element, not an nth locator: removing one prompt must
            # not make the close check silently target the next prompt's button.
            button = await locator.element_handle()
            if button is None:
                continue
            layer_handle = None
            try:
                layer_handle = await button.evaluate_handle(r'''el => {
                    const fc=el.closest('.fc-consent-root');
                    if(fc)return fc; // includes the sibling overlay from the supplied DOM
                    const known = el.closest('[role="dialog"], dialog, [aria-modal="true"], [class*="_w_bottom_fixed_"]');
                    if (known) {
                        // Include a dedicated fixed backdrop, but not an app
                        // shell that also contains the actual artist content.
                        for(let n=known.parentElement;n && n!==document.body;n=n.parentElement) {
                            const style=getComputedStyle(n);
                            if (style.position !== 'fixed') continue;
                            const walker=document.createTreeWalker(n, NodeFilter.SHOW_TEXT);
                            let otherText=false;
                            while(walker.nextNode()) {
                                if (!known.contains(walker.currentNode) && walker.currentNode.data.trim()) {
                                    otherText=true;
                                    break;
                                }
                            }
                            if (!otherText && style.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
                                style.backgroundColor !== 'transparent') return n;
                            break;
                        }
                        return known;
                    }
                    // Keep the closest prompt, never the outermost fixed app shell.
                    for(let n=el.parentElement;n && n!==document.body;n=n.parentElement) {
                        if (/Weverse\s+asks for your consent|Weverse\s+bittet um Ihre Zustimmung|Weverse.{0,20}请求您同意|Weverse.{0,20}征求您的同意/i.test(n.innerText || '') ||
                            getComputedStyle(n).position === 'fixed') return n;
                    }
                    return null;
                }''')
                layer = layer_handle.as_element()
                await button.click(timeout=max(1, min(1500, (action_deadline-time.monotonic())*1000)))
                closed = await _wait_rejection_closed(page, frame, button, layer,
                                                      min(action_deadline, time.monotonic() + 5))
                if not closed:
                    # Some prompts render before their site click handler is
                    # attached. Retry the identical explicit rejection once,
                    # unless the site marks that button as busy/disabled.
                    if await _frame_visible(frame) and await button.is_visible() and await button.is_enabled():
                        busy = await button.evaluate("el => el.getAttribute('aria-busy') === 'true' || el.getAttribute('aria-disabled') === 'true'")
                        if not busy:
                            await button.click(timeout=max(1, min(1500, (action_deadline-time.monotonic())*1000)))
                    closed = await _wait_rejection_closed(page, frame, button, layer, action_deadline)
                if not closed:
                    raise ValueError('自动拒绝已点击，但关闭弹窗失败（隐私弹窗或遮罩仍可见），已停止截图。请提供工作台浏览器中的弹窗截图。')
            except BrowserTimeout:
                # A temporary overlay or animation may block the click. Try the
                # other explicit rejection buttons, then re-scan until the bound.
                # Desktop CMP dialogs can exceed the narrow screenshot viewport.
                # Widen only when the button is outside it; never force the click.
                try:
                    bounds = await button.bounding_box()
                except BrowserError:
                    bounds = None
                if not widened and viewport and bounds and (
                    bounds['x'] < 0 or bounds['y'] < 0 or
                    bounds['x'] + bounds['width'] > viewport['width'] or
                    bounds['y'] + bounds['height'] > viewport['height']
                ):
                    await page.set_viewport_size({'width': max(1280, viewport['width']),
                                                  'height': max(1000, viewport['height'])})
                    widened = True
                continue
            except BrowserError:
                # A site navigation or prompt replacement may destroy a handle.
                # The next scan verifies the current visible page before capture.
                continue
            finally:
                if layer_handle:
                    await layer_handle.dispose()
                await button.dispose()
            rejected = clicked = True
            dismissals += 1
            if dismissals >= 10:
                raise ValueError('隐私提示反复出现，已停止截图，请检查工作台浏览器。')
            break
        if clicked:
            action_deadline = None
            attempted = False
            await page.wait_for_timeout(150)
            continue
        visible = await _consent_visible(page)
        now = time.monotonic()
        if visible and action_deadline is None:
            action_deadline = now + 15
        if action_deadline is not None:
            if not visible and not attempted:
                return rejected
            if now >= action_deadline:
                if attempted:
                    raise ValueError('自动点击拒绝按钮失败，已停止截图。请提供工作台浏览器中的弹窗截图。')
                raise ValueError('检测到隐私同意弹窗，但未找到明确的拒绝按钮，已停止截图。请提供该弹窗拒绝按钮的 outerHTML。')
        elif now >= arrival_deadline:
            return rejected
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
    header = card.locator('.community-artist-postId-_-header, .community-fanpost-postId-_-header')
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
