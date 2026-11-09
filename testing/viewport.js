import { expect } from '@playwright/test';
export function viewportOf(page) {
    const vp = page.viewportSize();
    if (!vp)
        throw new Error('these tests need a fixed viewport (set by the project device)');
    return vp;
}
function fmt(b) {
    return `[x ${Math.round(b.x)} y ${Math.round(b.y)} w ${Math.round(b.width)} h ${Math.round(b.height)}]`;
}
/** The element's WHOLE box lies inside the viewport (1px tolerance), and
 * nothing clips it there (an IntersectionObserver through every ancestor's
 * overflow — a box can be "inside" yet cut off by its own scroll row). With
 * `scroll` it is first scrolled into view: "reachable" rather than "visible
 * on open". Every offending edge is reported, with the numbers. */
export async function expectOnScreen(locator, what, opts = {}) {
    if (opts.scroll)
        await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    expect(box, `${what}: has a layout box`).not.toBeNull();
    const b = box;
    const vp = viewportOf(locator.page());
    const where = `${what} ${fmt(b)} in a ${vp.width}x${vp.height} viewport`;
    expect.soft(b.x, `${where}: left edge cut off`).toBeGreaterThanOrEqual(-1);
    expect.soft(b.y, `${where}: top edge cut off`).toBeGreaterThanOrEqual(-1);
    expect.soft(b.x + b.width, `${where}: right edge cut off`).toBeLessThanOrEqual(vp.width + 1);
    expect.soft(b.y + b.height, `${where}: bottom edge cut off`).toBeLessThanOrEqual(vp.height + 1);
    await expect.soft(locator, `${where}: clipped by an ancestor`).toBeInViewport({ ratio: 0.98 });
    return b;
}
/** One edge of a possibly screen-tall element can be brought on screen:
 * scroll it to that edge of the viewport, then that edge must be inside.
 * Guards against content that is cut off for good, however far you scroll. */
export async function expectEdgeReachable(locator, edge, what) {
    await locator.evaluate((el, e) => el.scrollIntoView({ block: e === 'top' ? 'start' : 'end' }), edge);
    const b = (await locator.boundingBox());
    const vp = viewportOf(locator.page());
    const y = edge === 'top' ? b.y : b.y + b.height;
    const where = `${what} ${fmt(b)} in a ${vp.width}x${vp.height} viewport: ${edge} edge unreachable`;
    expect.soft(y, where).toBeGreaterThanOrEqual(-1);
    expect.soft(y, where).toBeLessThanOrEqual(vp.height + 1);
    expect.soft(b.x, `${where} (left)`).toBeGreaterThanOrEqual(-1);
    expect.soft(b.x + b.width, `${where} (right)`).toBeLessThanOrEqual(vp.width + 1);
    return b;
}
/** Nothing inside `selector` is wider than it, so nothing scrolls sideways.
 * On a phone, sideways overflow is what makes the browser zoom the whole
 * layout out into a tiny, cut-off rendering. */
export async function expectNoSidewaysOverflow(page, what, selector = 'html') {
    const r = await page
        .locator(selector)
        .first()
        .evaluate((el) => ({ scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }));
    expect(r.scrollWidth, `${what}: ${selector} scrolls sideways (content ${r.scrollWidth}px wide in ${r.clientWidth}px)`).toBeLessThanOrEqual(r.clientWidth + 1);
}
/** No element under `selector` pokes out past the viewport's left or right
 * edge — catches overhangs that a scrollWidth check cannot see (fixed and
 * clipped boxes). Names the offenders. With `within`, the edges are that
 * element's instead: content spilling past the page column on a desktop,
 * where the viewport still has room to hide it. */
export async function expectNothingPokesOutSideways(page, what, selector, within) {
    const offenders = await page.locator(selector).first().evaluate((root, within) => {
        const box = within ? document.querySelector(within).getBoundingClientRect() : null;
        const [left, right] = box ? [box.left, box.right] : [0, window.innerWidth];
        const out = [];
        for (const el of [root, ...root.querySelectorAll('*')]) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height)
                continue;
            if (r.left < left - 1 || r.right > right + 1)
                out.push(`${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ')[0] : ''} [${Math.round(r.left)}..${Math.round(r.right)}]`);
        }
        return out;
    }, within);
    const edges = within ? `${within}` : `a ${viewportOf(page).width}px viewport`;
    expect(offenders, `${what}: elements poking out sideways of ${edges}`).toEqual([]);
}
export async function fingerSwipe(page, from, to, steps = 20) {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: from.x, y: from.y }] });
    for (let i = 1; i <= steps; i++) {
        await cdp.send('Input.dispatchTouchEvent', {
            type: 'touchMove',
            touchPoints: [{ x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps }]
        });
        await page.waitForTimeout(16);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await cdp.detach();
    await page.waitForTimeout(500); // momentum settles
}
/** Pinch-zoom about the centre of the screen; `scale` > 1 zooms in. */
export async function pinchZoom(page, scale) {
    const vp = viewportOf(page);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.synthesizePinchGesture', { x: vp.width / 2, y: vp.height / 2, scaleFactor: scale, relativeSpeed: 300 });
    await cdp.detach();
    await page.waitForTimeout(500);
}
/** The pinch-zoomed case a finger test cannot model: Android Chrome has twice
 * refused to pan the zoomed viewport at all (2026-10-10/11), leaving the
 * screen on the top 1/scale of the layout for good. The element's bottom must
 * then come onto that part of the screen by the page's own scrollers alone:
 * scroll every scrollable ancestor (the overlay, and the infobox where it is
 * its own scroller) to its end and require the edge above `layout height /
 * scale`, where a screen parked at the top of the layout ends. */
export async function expectReachableWithoutPan(page, locator, what) {
    await locator.evaluate((el) => {
        for (let n = el.parentElement; n; n = n.parentElement) {
            const o = getComputedStyle(n).overflowY;
            if (o === 'auto' || o === 'scroll')
                n.scrollTop = n.scrollHeight;
        }
    });
    await page.waitForTimeout(200);
    const w = await locator.evaluate((el) => {
        const vv = window.visualViewport;
        const layoutHeight = document.documentElement.clientHeight;
        return {
            bottom: Math.round(el.getBoundingClientRect().bottom),
            screenEnd: Math.round(layoutHeight / vv.scale),
            scale: +vv.scale.toFixed(2)
        };
    });
    expect(w.bottom, `${what}: with the overlay scrolled to its end the bottom edge sits at ${w.bottom}px, but a screen zoomed ${w.scale}x that never pans ends at ${w.screenEnd}px — only a viewport pan could reveal it`).toBeLessThanOrEqual(w.screenEnd + 1);
}
/** Swipe until the element's `edge` is inside the VISUAL viewport — the part
 * of the layout a (possibly pinch-zoomed) screen actually shows. `bottom`
 * swipes up, `right` swipes left. Fails naming where the edge was left. */
export async function expectReachableByFinger(page, locator, edge, what, maxSwipes = 8) {
    const vp = viewportOf(page);
    const where = () => locator.evaluate((el, e) => {
        const r = el.getBoundingClientRect();
        const vv = window.visualViewport;
        const edgePos = e === 'bottom' ? r.bottom : r.right;
        const limit = e === 'bottom' ? vv.offsetTop + vv.height : vv.offsetLeft + vv.width;
        return { edgePos: Math.round(edgePos), limit: Math.round(limit), scale: +vv.scale.toFixed(2) };
    }, edge);
    let w = await where();
    for (let i = 0; i < maxSwipes && w.edgePos > w.limit + 1; i++) {
        if (edge === 'bottom')
            await fingerSwipe(page, { x: vp.width / 2, y: vp.height * 0.8 }, { x: vp.width / 2, y: vp.height * 0.2 });
        else
            await fingerSwipe(page, { x: vp.width * 0.8, y: vp.height / 2 }, { x: vp.width * 0.2, y: vp.height / 2 });
        w = await where();
    }
    expect(w.edgePos, `${what}: ${edge} edge still at ${w.edgePos}px after ${maxSwipes} swipes, the screen ends at ${w.limit}px (zoom ${w.scale}x) — a finger cannot reach it`).toBeLessThanOrEqual(w.limit + 1);
}
/** How far (px) an inline control's centre sits from the visual centre of
 * the capitals in the heading it rides in: positive = too low. Measured on
 * the heading's first text node, its baseline and the font's cap height. */
export async function capCentreOffset(control) {
    return control.evaluate((btn) => {
        const h = btn.closest('h1,h2,h3,h4');
        const walker = document.createTreeWalker(h, NodeFilter.SHOW_TEXT);
        let t = walker.nextNode();
        while (t && !t.textContent?.trim())
            t = walker.nextNode();
        const probe = document.createElement('span');
        probe.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
        t.parentNode.insertBefore(probe, t.nextSibling);
        const base = probe.getBoundingClientRect().top;
        probe.remove();
        const cs = getComputedStyle(h);
        const ctx = document.createElement('canvas').getContext('2d');
        ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        const cap = ctx.measureText('H').actualBoundingBoxAscent;
        const r = btn.getBoundingClientRect();
        return r.top + r.height / 2 - (base - cap / 2);
    });
}
