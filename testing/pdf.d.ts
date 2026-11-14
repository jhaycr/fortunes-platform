/** A page: (font size, text) lines top to bottom; a size of 0 is a blank line. */
export type Page = [number, string][];
export declare const PAGE_WIDTH = 432;
export declare const PAGE_HEIGHT = 648;
/** A PDF whose text layer is `pages`, set in Helvetica, bold from 13 pt up. */
export declare function textPdf(pages: Page[]): Uint8Array;
