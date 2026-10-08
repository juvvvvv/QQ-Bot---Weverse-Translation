"""Capture the artist-only list, keeping comment identity separate from order."""
import json
import math
import time
from datetime import datetime, timezone

LIST = '.comment-list-by-artists-_-comment_list'
CARD = '.comment-item-_-container'
TEXT = '.comment-item-content-_-comment .line-clamp-node-view-_-container'
AUTHOR = '.comment-item-header-profile-name-_-name'
TIME = '.comment-item-header-_-time'
BADGE = 'svg g[id="24/em/ic_officialbadge_special_medium"]'
TOGGLE = '.base-comment-artist-count-and-toggle-_-toggle_button'


def machine_time(value):
    """Read an explicit machine date; localized display text is never a date key."""
    if value is None or value == '':
        return None
    try:
        result = float(value)
        if result > 100_000_000_000:
            result /= 1000  # Unix milliseconds.
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return result.replace(tzinfo=result.tzinfo or timezone.utc).timestamp()
        except ValueError:
            return None


def order_comments(records):
    ids = {r['comment_id']: r for r in records}
    # Only use a supplied, unambiguous parent ID. Names and numeric ID order
    # cannot establish a thread relationship.
    children, roots = {}, []
    for record in records:
        parent = record.get('parent_id')
        seen = {record['comment_id']}
        current = parent
        while current in ids and current not in seen:
            seen.add(current)
            current = ids[current].get('parent_id')
        if current in seen:
            parent = None
            record['parent_id'] = None
        if parent in ids:
            children.setdefault(parent, []).append(record)
        else:
            roots.append(record)
    # The supplied Weverse list is newest first, including same-minute replies.
    # Use one consistent ordering for the whole snapshot. Never mix parsed dates
    # with unrecognized localized strings, or infer chronology from comment IDs.
    timestamps = {r['comment_id']: machine_time(r.get('timestamp')) for r in records}
    if records and all(value is not None for value in timestamps.values()):
        key = lambda r: (timestamps[r['comment_id']], -r['source_index'])
    else:
        key = lambda r: -r['source_index']
    result = []
    def visit(record, depth):
        record['depth'] = max(depth, 1 if record['is_reply'] else 0)
        record['grouping_known'] = not record['is_reply'] or record.get('parent_id') in ids
        result.append(record)
        for child in sorted(children.get(record['comment_id'], []), key=key):
            visit(child, depth + 1)
    for record in sorted(roots, key=key):
        visit(record, 0)
    return result


async def collect_artist_comments(page, cfg, api_parents=None):
    """Wait for the artist region, not a fixed sleep or one empty DOM lookup."""
    from .capture_layout import read_comment_counts
    from .page_cleanup import reject_optional_consent
    deadline = time.monotonic() + cfg.get('comment_wait_seconds', 30)
    listing = None
    raw = []
    expected = None
    clicked = False
    stable_key = None
    stable_since = 0
    last_scroll = 0
    scrolls = 0
    problem = ''
    while True:
        await reject_optional_consent(page)
        counts = await read_comment_counts(page, cfg)
        count = counts['artist']
        expected = count['value'] if count else None
        if expected is not None and expected > 200:
            raise ValueError('艺人评论超过单张图片的 200 条上限，请分批处理。')
        containers = page.locator(LIST)
        visible = [containers.nth(i) for i in range(await containers.count())
                   if await containers.nth(i).is_visible()]
        if len(visible) > 1:
            raise ValueError('匹配到多个可见艺人评论列表，请检查页面。')
        listing = visible[0] if visible else None
        # Preserve explicitly configured legacy adapters. The default native
        # path must never interpret a missing list/counter as zero comments.
        if not await containers.count() and count is None and cfg.get('comment_selector'):
            legacy = page.locator(cfg['comment_selector'])
            if await legacy.count():
                return []
        buttons = page.locator(TOGGLE)
        if not clicked and expected != 0:
            for i in range(min(await buttons.count(), 5)):
                button = buttons.nth(i)
                if await button.is_visible() and await button.get_attribute('aria-expanded') == 'false':
                    await button.click(timeout=3000)
                    clicked = True
                    break
        raw = []
        busy = False
        if listing is not None:
            raw, busy = await listing.evaluate(r'''el => {
                const cards=[...el.children].filter(n=>n.matches('.comment-item-_-container'));
                const result=[];
                for(let index=0;index<cards.length;index++) {
                    const c=cards[index];
                    if(!c.querySelector('.comment-item-header-_-container svg g[id="24/em/ic_officialbadge_special_medium"]'))continue;
                    const t=c.querySelectorAll('.comment-item-header-_-time');
                    const date=t[0];
                    const stamp=date?.getAttribute('datetime') || date?.getAttribute('data-timestamp')
                        || date?.getAttribute('data-created-at') || date?.querySelector('time[datetime]')?.getAttribute('datetime');
                    const author=c.querySelector('.comment-item-header-profile-name-_-name');
                    const text=c.querySelector('.comment-item-content-_-comment .line-clamp-node-view-_-container');
                    result.push({comment_id:c.getAttribute('data-wev-comment-id'),
                        parent_id:c.getAttribute('data-parent-comment-id') || c.getAttribute('data-root-comment-id'),
                        is_reply:c.classList.contains('comment-item-_--instant-reply'),
                        published:date?.innerText?.trim() || '',timestamp:stamp,source_index:index,
                        ready:!!(t.length===1 && date?.innerText?.trim() && author?.textContent?.trim() && text),
                        snapshot:(author?.textContent || '')+'\n'+(text?.textContent || '')});
                }
                const region=el.closest('.comment-shape-by-item-type-_-container') || el;
                const busy=region?.getAttribute('aria-busy')==='true' || !!region?.querySelector('[aria-busy="true"], [role="progressbar"]');
                return [result,busy];
            }''')
        ids = [r['comment_id'] for r in raw]
        valid = all(ids) and len(ids) == len(set(ids)) and all(r['ready'] for r in raw)
        ready = count is not None and not count['approximate'] and expected == len(raw) and valid and not busy
        # An explicit, stable zero is different from a missing counter.
        if count is not None and not count['approximate'] and expected == 0 and not raw and not busy:
            ready = True
        if ready:
            key = (expected, tuple((r['comment_id'], r['published'], r['snapshot']) for r in raw))
            if key != stable_key:
                stable_key, stable_since = key, time.monotonic()
            if time.monotonic() - stable_since >= (1.0 if expected == 0 else .4):
                break
        else:
            stable_key = None
        if count is None:
            problem = '未识别艺人评论计数'
        elif count['approximate']:
            problem = '艺人评论数为近似值，无法确认完整数量'
        elif not valid:
            problem = '评论编号、作者或发布时间尚未完整加载'
        else:
            problem = f'网页显示 {expected} 条艺人评论，本次仅读取 {len(raw)} 条'
        if time.monotonic() >= deadline:
            raise ValueError(f'艺人评论加载等待超时：{problem}。已停止截图，原存档保留；请检查评论区和网络后重试。')
        if listing is not None and not ready and scrolls < cfg['max_scrolls'] and time.monotonic() - last_scroll >= .8:
            cards = listing.locator(':scope > ' + CARD)
            if await cards.count():
                await cards.last.scroll_into_view_if_needed(timeout=2000)
            await listing.evaluate('''el => {
                for(let n=el;n;n=n.parentElement) {
                    if(n.scrollHeight>n.clientHeight+4 && /auto|scroll/.test(getComputedStyle(n).overflowY)) {
                        n.scrollTop=n.scrollHeight;return;
                    }
                }
            }''')
            scrolls += 1
            last_scroll = time.monotonic()
        await page.wait_for_timeout(200)
    api_parents = api_parents or {}
    seen = set(ids)
    for record in raw:
        record.pop('ready');record.pop('snapshot')
        record['timestamp'] = machine_time(record.get('timestamp'))
        record['card'] = listing.locator(CARD + '[data-wev-comment-id=' + json.dumps(record['comment_id']) + ']')
        record['parent_id'] = record['parent_id'] or api_parents.get(record['comment_id'])
        if not record.get('parent_id'):
            short = record['comment_id'].split('-')[-1]
            if short in api_parents and sum(cid.split('-')[-1] == short for cid in seen) == 1:
                record['parent_id'] = api_parents[short]
        parent = record.get('parent_id')
        if parent and parent not in seen:
            matches = [cid for cid in seen if cid.split('-')[-1] == str(parent)]
            if len(matches) == 1:
                record['parent_id'] = matches[0]
    return order_comments(raw)


async def stage_comment(page, record, width, heading=''):
    """Keep original contents/assets/CSS, normalize layout before native capture."""
    handle = await record['card'].evaluate_handle('''(original,{width,depth,reply,heading}) => {
        document.querySelector('[data-wvbot-stage]')?.remove();
        const stage=document.createElement('div');stage.dataset.wvbotStage='';
        stage.style.cssText=`position:relative;box-sizing:border-box;width:${width}px;padding:16px;background:white;color:#111;`;
        const body=original.querySelector('.line-clamp-node-view-_-container');
        stage.style.fontFamily=getComputedStyle(body).fontFamily;
        stage.style.fontSize=getComputedStyle(body).fontSize;
        if(heading){const title=document.createElement('div');title.textContent=heading;
            title.style.cssText='font-size:14px;font-weight:600;margin-bottom:16px;line-height:1.6';stage.append(title);}
        const card=original.cloneNode(true);card.removeAttribute('href');
        const indent=Math.min(depth,4)*28;
        card.style.cssText=`position:relative!important;display:flex!important;align-items:flex-start!important;gap:12px!important;box-sizing:border-box!important;margin:0 0 0 ${indent}px!important;padding:0!important;width:calc(100% - ${indent}px)!important;height:auto!important;min-height:0!important;background:white!important;text-decoration:none!important;color:inherit!important;`;
        const avatar=card.querySelector('.comment-item-_-image_area');
        if(avatar){avatar.style.cssText=`position:static!important;flex:0 0 ${reply?24:32}px!important;width:${reply?24:32}px!important;height:auto!important;`;
            for(const n of avatar.querySelectorAll('span,img'))n.style.setProperty('position','static','important');}
        const area=card.querySelector('.comment-item-_-text_area');
        area.style.cssText=`box-sizing:border-box!important;position:relative!important;flex:1!important;min-width:0!important;margin:0!important;width:auto!important;height:auto!important;background:white!important;padding:${reply?'0':'14px 16px'}!important;border:${reply?'0':'1px solid #e5e9f2'}!important;border-radius:${reply?'0':'18px'}!important;`;
        if(!reply)area.dataset.wvbotFrame='';
        for(const n of card.querySelectorAll('.comment-item-_-more_wrap,.comment-item-_-translate'))n.style.setProperty('display','none','important');
        const text=card.querySelector('.line-clamp-node-view-_-container');
        text.style.setProperty('white-space','pre-wrap','important');
        text.style.setProperty('overflow-wrap','anywhere','important');
        for(const icon of card.querySelectorAll('.icon-_-icon')) {
            icon.style.setProperty('display','inline-flex','important');
            for(const svg of icon.querySelectorAll('svg'))svg.style.cssText='width:100%!important;height:100%!important;display:block!important;';
        }
        for(const blind of card.querySelectorAll('.blind'))blind.style.setProperty('display','none','important');
        const name=card.querySelector('.comment-item-header-profile-name-_-name');
        name.style.cssText='display:inline-flex!important;align-items:center!important;gap:4px!important;font-weight:600!important;text-decoration:none!important;color:inherit!important;';
        const header=card.querySelector('.comment-item-header-_-container');
        header.style.cssText='display:flex!important;align-items:center!important;flex-wrap:wrap!important;gap:8px!important;margin-bottom:4px!important;';
        const time=card.querySelector('.comment-item-header-_-time');
        time.style.cssText='font-size:12px!important;color:#9a9a9a!important;white-space:nowrap!important;';
        if(reply){const line=document.createElement('span');line.dataset.wvbotConnector='';line.setAttribute('role','img');
            line.style.cssText='position:absolute;left:11px;top:36px;bottom:0;border-left:1px solid #e5e9f2;width:1px;';card.append(line);}
        stage.append(card);document.body.append(stage);return stage;
    }''', {'width': width, 'depth': record['depth'], 'reply': record['is_reply'], 'heading': heading})
    await handle.dispose()
    await page.add_style_tag(content='[data-wvbot-stage] .comment-item-_-container::before,[data-wvbot-stage] .comment-item-_-container::after{display:none!important}')
    return page.locator('[data-wvbot-stage]')


async def decoration(card):
    return await card.evaluate('''el => {
        const a=el.getBoundingClientRect(),frame=el.querySelector('[data-wvbot-frame]'),line=el.querySelector('[data-wvbot-connector]');
        const r=frame?.getBoundingClientRect(), l=line?.getBoundingClientRect();
        return {frame:r?{left:r.left-a.left,right:r.right-a.left-1,color:'#e5e9f2'}:null,
                connector_x:l?l.left-a.left:null};
    }''')
