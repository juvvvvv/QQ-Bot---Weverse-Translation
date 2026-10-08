"""Recognize the supplied fanpost structure independently of its URL route."""

FAN_MAIN = '.community-fanpost-postId-_-main'


async def adapt_post(page, cfg):
    roots=page.locator(FAN_MAIN)
    visible=[roots.nth(i) for i in range(await roots.count()) if await roots.nth(i).is_visible()]
    if not visible:return cfg
    if len(visible)!=1:raise ValueError('匹配到多个粉丝原帖，请检查页面。')
    root=visible[0]
    header=root.locator('.community-fanpost-postId-_-header')
    if await header.count()!=1 or await header.locator('.avatar-decorator-_-title').count()!=1:
        raise ValueError('粉丝原帖的头像昵称区域尚未完整加载，请重试。')
    if await header.locator('.avatar-decorator-_-subtitle').count()!=1:
        raise ValueError('粉丝原帖发布时间未完整加载，请重试。')
    viewers=root.locator('.community-fanpost-postId-_-wrap_weverse_viewer .WeverseViewer')
    if await viewers.count()!=1:raise ValueError('粉丝原帖正文区域未完整加载，请重试。')
    await viewers.evaluate('''viewer=>{
        if(viewer.querySelector('[data-wvbot-fan-text]'))return;
        const blocks=[...viewer.children].filter(n=>!n.matches('.blind,script,style'));
        const prefix=[];
        for(const block of blocks){if(block.matches('.WidgetMedia,figure,img,video') || block.querySelector('.WidgetMedia,img,video'))break;prefix.push(block);}
        // Keep media in its existing position. If text is interleaved with
        // media, use the whole viewer rather than reorder or drop any content.
        if(!prefix.length || blocks.slice(prefix.length).some(n=>n.textContent.trim())){viewer.dataset.wvbotFanText='';return;}
        if(prefix.length===1){prefix[0].dataset.wvbotFanText='';return;}
        const style=getComputedStyle(prefix[0]),wrap=document.createElement('div');wrap.dataset.wvbotFanText='';
        wrap.style.cssText=`font-size:${style.fontSize};font-family:${style.fontFamily};line-height:${style.lineHeight}`;
        viewer.insertBefore(wrap,prefix[0]);wrap.append(...prefix);
    }''')
    actions=page.locator('.community-fanpost-postId-_-action_bar .toolbar-_-container')
    if await actions.count()==1 and not await root.locator('[data-wvbot-owned-toolbar]').count():
        handle=await actions.element_handle()
        try:
            await root.evaluate('''(el,source)=>{
                const wrap=document.createElement('div');wrap.dataset.wvbotOwnedToolbar='';wrap.style.marginTop='16px';
                const toolbar=source.cloneNode(true);toolbar.style.cssText='position:static!important;display:flex!important;align-items:center!important;height:auto!important;width:auto!important;transform:none!important;';
                for(const child of toolbar.querySelectorAll('.toolbar-_-left,.toolbar-_-right'))child.style.cssText='position:static!important;display:flex!important;gap:16px!important;height:auto!important;transform:none!important;';
                const colors=[...source.querySelectorAll('button')].map(n=>getComputedStyle(n).color);
                [...toolbar.querySelectorAll('button')].forEach((n,i)=>{n.style.cssText=`position:static!important;display:inline-flex!important;align-items:center!important;gap:6px!important;color:${colors[i]}!important;background:transparent!important;border:0!important;margin:0!important;padding:0!important;`;});
                wrap.append(toolbar);el.append(wrap);
            }''',handle)
        finally:await handle.dispose()
    return cfg | {'post_selector':FAN_MAIN,'text_selector':'[data-wvbot-fan-text]',
                  'author_selector':'.community-fanpost-postId-_-header .avatar-decorator-_-title','post_kind':'fan'}
