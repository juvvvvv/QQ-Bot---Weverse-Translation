"""Capture the artist-only list, keeping comment identity separate from order."""
import re

LIST = '.comment-list-by-artists-_-comment_list'
CARD = '.comment-item-_-container'
TEXT = '.comment-item-content-_-comment .line-clamp-node-view-_-container'
AUTHOR = '.comment-item-header-profile-name-_-name'
TIME = '.comment-item-header-_-time'
BADGE = 'svg g[id="24/em/ic_officialbadge_special_medium"]'
TOGGLE = '.base-comment-artist-count-and-toggle-_-toggle_button'


def time_key(value, index):
    match = re.search(r'(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2}):(\d{2})', value)
    if match:
        return (*map(int, match.groups()), -index)
    return (99, 99, 99, 99, -index)


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
    key = lambda r: time_key(r.get('published', ''), r['source_index'])
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
    from .capture_layout import read_comment_counts
    count = (await read_comment_counts(page, cfg))['artist']
    if count and count['value'] == 0:
        return []
    containers = page.locator(LIST)
    buttons = page.locator(TOGGLE)
    for i in range(min(await buttons.count(), 5)):
        button = buttons.nth(i)
        if await button.is_visible() and await button.get_attribute('aria-expanded') == 'false':
            await button.click(timeout=5000)
            try:
                await containers.first.wait_for(state='visible', timeout=10000)
            except Exception as exc:
                raise ValueError('艺人评论已点击展开，但列表未加载，请检查网络后重试。') from exc
    if not await containers.count():
        return []
    visible = [containers.nth(i) for i in range(await containers.count()) if await containers.nth(i).is_visible()]
    if len(visible) != 1:
        raise ValueError('艺人评论列表未展开，或匹配到多个列表，请检查页面。')
    listing = visible[0]
    # Scroll the actual list/scrolling ancestor, not just the document.
    # The final artist count check rejects incomplete virtual/lazy lists.
    for _ in range(cfg['max_scrolls']):
        cards = listing.locator(':scope > ' + CARD)
        before = await cards.count()
        if before:
            await cards.last.scroll_into_view_if_needed()
        await listing.evaluate('''el => {
            for(let n=el;n;n=n.parentElement) {
                if(n.scrollHeight>n.clientHeight+4 && /auto|scroll/.test(getComputedStyle(n).overflowY)) {
                    n.scrollTop=n.scrollHeight; return;
                }
            }
        }''')
        await page.wait_for_timeout(250)
        if await cards.count() == before:
            break
    cards = listing.locator(':scope > ' + CARD)
    if await cards.count() > 200:
        raise ValueError('艺人评论超过单张图片的 200 条上限，请分批处理。')
    raw, seen = [], set()
    api_parents = api_parents or {}
    for index in range(await cards.count()):
        card = cards.nth(index)
        # Badge must belong to this comment's own header, never a nested reply.
        if not await card.locator('.comment-item-header-_-container ' + BADGE).count():
            continue
        comment_id = await card.get_attribute('data-wev-comment-id')
        if not comment_id:
            raise ValueError('艺人评论缺少唯一编号，无法安全绑定译文。')
        if comment_id in seen:
            continue
        seen.add(comment_id)
        parent = await card.get_attribute('data-parent-comment-id') or await card.get_attribute('data-root-comment-id')
        parent = parent or api_parents.get(comment_id)
        times = card.locator(TIME)
        if await times.count() != 1:
            raise ValueError('艺人评论缺少唯一发布时间，无法确定译文顺序。')
        raw.append({'card': card, 'comment_id': comment_id, 'parent_id': parent,
                    'is_reply': 'comment-item-_--instant-reply' in (await card.get_attribute('class') or '').split(),
                    'published': await times.inner_text(), 'source_index': index})
    # API IDs occasionally omit the namespace prefix; use only a unique match.
    for record in raw:
        if not record.get('parent_id'):
            short = record['comment_id'].split('-')[-1]
            candidates = [v for k, v in api_parents.items() if k == short]
            if len(candidates) == 1:
                record['parent_id'] = candidates[0]
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
