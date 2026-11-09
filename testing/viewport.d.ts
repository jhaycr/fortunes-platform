import { type Locator, type Page } from '@playwright/test';
export interface Box {
    x: number;
    y: number;
    width: number;
    height: number;
}
export declare function viewportOf(page: Page): {
    width: number;
    height: number;
};
/** The element's WHOLE box lies inside the viewport (1px tolerance), and
 * nothing clips it there (an IntersectionObserver through every ancestor's
 * overflow — a box can be "inside" yet cut off by its own scroll row). With
 * `scroll` it is first scrolled into view: "reachable" rather than "visible
 * on open". Every offending edge is reported, with the numbers. */
export declare function expectOnScreen(locator: Locator, what: string, opts?: {
    scroll?: boolean;
}): Promise<Box>;
/** One edge of a possibly screen-tall element can be brought on screen:
 * scroll it to that edge of the viewport, then that edge must be inside.
 * Guards against content that is cut off for good, however far you scroll. */
export declare function expectEdgeReachable(locator: Locator, edge: 'top' | 'bottom', what: string): Promise<Box>;
/** Nothing inside `selector` is wider than it, so nothing scrolls sideways.
 * On a phone, sideways overflow is what makes the browser zoom the whole
 * layout out into a tiny, cut-off rendering. */
export declare function expectNoSidewaysOverflow(page: Page, what: string, selector?: string): Promise<void>;
/** No element under `selector` pokes out past the viewport's left or right
 * edge — catches overhangs that a scrollWidth check cannot see (fixed and
 * clipped boxes). Names the offenders. With `within`, the edges are that
 * element's instead: content spilling past the page column on a desktop,
 * where the viewport still has room to hide it. */
export declare function expectNothingPokesOutSideways(page: Page, what: string, selector: string, within?: string): Promise<void>;
export declare function fingerSwipe(page: Page, from: {
    x: number;
    y: number;
}, to: {
    x: number;
    y: number;
}, steps?: number): Promise<void>;
/** Pinch-zoom about the centre of the screen; `scale` > 1 zooms in. */
export declare function pinchZoom(page: Page, scale: number): Promise<void>;
/** The pinch-zoomed case a finger test cannot model: Android Chrome has twice
 * refused to pan the zoomed viewport at all (2026-10-10/11), leaving the
 * screen on the top 1/scale of the layout for good. The element's bottom must
 * then come onto that part of the screen by the page's own scrollers alone:
 * scroll every scrollable ancestor (the overlay, and the infobox where it is
 * its own scroller) to its end and require the edge above `layout height /
 * scale`, where a screen parked at the top of the layout ends. */
export declare function expectReachableWithoutPan(page: Page, locator: Locator, what: string): Promise<void>;
/** Swipe until the element's `edge` is inside the VISUAL viewport — the part
 * of the layout a (possibly pinch-zoomed) screen actually shows. `bottom`
 * swipes up, `right` swipes left. Fails naming where the edge was left. */
export declare function expectReachableByFinger(page: Page, locator: Locator, edge: 'bottom' | 'right', what: string, maxSwipes?: number): Promise<void>;
/** How far (px) an inline control's centre sits from the visual centre of
 * the capitals in the heading it rides in: positive = too low. Measured on
 * the heading's first text node, its baseline and the font's cap height. */
export declare function capCentreOffset(control: Locator): Promise<number>;
