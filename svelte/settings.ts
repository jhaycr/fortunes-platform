/** What a card tells the AI tab's header about itself, so the header can
 * summarise "what is switched on" without re-fetching every endpoint. */
export interface CardStatus {
	/** Configured and usable right now. */
	on: boolean;
	/** One-line detail: the model, the provider, "not configured". */
	detail: string;
	/** Set when something is wrong rather than merely off (a malformed
	 * config file, a persona with no voice) — shown in red, opens the card. */
	warn?: string;
	/** A broken /data/config.yaml, reported wherever it surfaces. */
	configError?: string | null;
}
