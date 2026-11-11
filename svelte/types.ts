// The wire types every Little Fortunes app shares: who is signed in, the
// people on the instance, settings, spending limits, voices and usage. An
// app's own types (its readings, its subject) live in the app, which
// re-exports these beside them so its pages import one module.
//
// `PlatformApi` is the part of an app's backend handle that the shared
// components call. The app's own `api` (HTTP for a server, browser-local for
// the public demo) has these methods and more; it is handed in once with
// `configure` (see app.ts).

export interface Persona {
	slug: string;
	name: string;
	description: string;
}

export type Visibility = 'private' | 'specific' | 'everyone';

export interface Person {
	username: string;
	display_name: string;
}

/** What the sharing control needs of a reading: any app's saved reading. */
export interface Shareable {
	id: number;
	owner: string;
	visibility: Visibility;
	shared_with: string[];
}

/** Fields listed in `managed` come from the deployment's config file and
 * cannot be written from the UI. */
export interface LlmSettings {
	base_url: string;
	model: string;
	api_key_set: boolean;
	from_env: boolean;
	managed: string[];
	config_file: string | null;
	config_error: string | null;
}

export interface AdminUser {
	username: string;
	display_name: string;
	kind: string; // person | system
	active: boolean;
	is_admin: boolean;
	first_seen: number;
	last_seen: number;
}

/** What the reassignment tool did: counts per surface, plus any files an app
 * keeps per person (drafts moved, library entries re-attributed). */
export interface ReassignReport {
	from: string;
	to: string;
	readings: number;
	settings: number;
	usage_rows: number;
	deactivated: boolean;
	staging_moved?: string[];
	staging_collisions?: string[];
	library_restamped?: string[];
}

/** Settings that follow a person across devices. Each app adds its own. */
export interface UserSettingsBase {
	auto_read_audio: boolean;
	/** The reader last chosen (null = never set server-side). */
	persona: string | null;
	/** False until any migrated pref has been written: drives the one-time
	 * first-login import from localStorage. */
	has_profile: boolean;
}

export interface LimitGauge {
	used: number;
	limit: number | null;
}

/** Daily spend caps. `{enabled: false}` alone when no cap is configured. */
export interface LimitsStatus {
	enabled: boolean;
	exempt?: boolean;
	readings?: LimitGauge;
	tokens?: LimitGauge;
	minutes?: LimitGauge;
}

export interface LimitsSettings {
	readings_per_day: number | null;
	llm_tokens_per_day: number | null;
	tts_minutes_per_day: number | null;
	managed: string[];
	config_file: string | null;
	config_error: string | null;
}

/** Everything an app needs to know about the signed-in person and what this
 * instance has switched on. `interpretation` / `tts` say whether the OPTIONAL
 * AI features are configured at all. */
export interface Me<S extends UserSettingsBase = UserSettingsBase> {
	user: string;
	display_name: string;
	interpretation: boolean;
	tts: boolean;
	settings: S;
	limits: LimitsStatus;
	is_admin: boolean;
	authenticated: boolean;
	logout_url: string | null;
	version: string;
}

export interface UsageRow {
	component: 'llm' | 'tts';
	model: string;
	calls: number;
	prompt_tokens: number;
	completion_tokens: number;
	characters: number;
	audio_bytes: number;
}

export interface UsageSummary {
	days: number;
	by_model: UsageRow[];
	daily: {
		day: string;
		calls: number;
		prompt_tokens: number;
		completion_tokens: number;
		tts_characters: number;
		audio_bytes: number;
	}[];
	by_user: {
		owner: string;
		component: 'llm' | 'tts';
		calls: number;
		prompt_tokens: number;
		completion_tokens: number;
		audio_bytes: number;
	}[];
}

/** One tunable in a provider's voice block. The server declares these so the
 * settings form is generated: adding a provider needs no web-page change. */
export interface VoiceFieldSpec {
	key: string;
	label: string;
	kind: 'text' | 'longtext' | 'number' | 'slider' | 'bool';
	default: string | number | boolean | null;
	required: boolean;
	min: number | null;
	max: number | null;
	step: number | null;
	help: string | null;
}

export interface VoiceProvider {
	name: string;
	label: string;
	default_base_url: string;
	default_model: string;
	supports_listing: boolean;
	/** Can build a voice from a written description (ElevenLabs) */
	supports_design: boolean;
	fields: VoiceFieldSpec[];
}

/** One candidate from a voice-design pass; `audio` is a playable data: URL. */
export interface DesignedVoice {
	generated_voice_id: string;
	audio: string;
	duration_secs: number | null;
}

/** A voice block for whichever provider is active: its shape varies by
 * provider, so it's keyed loosely and rendered from `fields`. */
export type VoiceValues = Record<string, string | number | boolean>;

export interface ProviderVoice {
	id: string;
	name: string;
	category: string | null;
	description: string | null;
	preview_url: string | null;
	labels: Record<string, string>;
	settings: VoiceValues;
}

export interface TtsSettings {
	provider: string;
	providers: VoiceProvider[];
	/** Each provider's own stored setup, so switching restores rather than
	 * carrying the previous provider's values across (or blanking the form
	 * over data that is actually still there). */
	connections: Record<
		string,
		{
			base_url: string;
			model: string;
			api_key_set: boolean;
			voices: Record<string, VoiceValues | null>;
			gaps: Record<string, string[]>;
			defaults: Record<string, VoiceValues>;
		}
	>;
	base_url: string;
	model: string;
	api_key_set: boolean;
	/** null when the persona has no voice on the active provider */
	voices: Record<string, VoiceValues | null>;
	/** required fields still missing, per persona */
	gaps: Record<string, string[]>;
	/** each persona's written character, used to seed voice design */
	descriptions: Record<string, string>;
	defaults: Record<string, VoiceValues>;
	managed: string[];
	config_file: string | null;
	config_error: string | null;
}

/** The server's human-readable `detail` when there is one ("Daily reading
 * limit reached, resets at midnight."), else a terse status fallback. */
export class ApiError extends Error {
	status: number;
	constructor(message: string, status: number) {
		super(message);
		this.status = status;
	}
}

/** What a backend can do, read by pages to mount or hide UI. Pages never ask
 * which BUILD they are in, only what the backend behind `api` offers. Each
 * app adds its own (a library, deck management). */
export interface CapabilitiesBase {
	/** Sharing readings with other people. */
	sharing: boolean;
	/** Spoken readings. */
	tts: boolean;
	/** The visitor's own LLM key, kept in the browser (the static demo). */
	localKey: boolean;
	/** The journal and settings live in this browser only: no account, no
	 * sync, nothing on a server. */
	localData: boolean;
}

/** The methods of an app's backend handle that the shared components call. */
export interface PlatformApi {
	capabilities: CapabilitiesBase;
	users(): Promise<Person[]>;
	setSharing(id: number, visibility: Visibility, grantees?: string[]): Promise<Shareable>;
	patchMe(p: { display_name?: string }): Promise<{ display_name: string }>;
	setupState(): Promise<{ required: boolean; user: string; is_admin: boolean }>;
	claimAdmin(token: string): Promise<{ user: string; is_admin: boolean }>;
	getLlmSettings(): Promise<LlmSettings>;
	setLlmSettings(s: { base_url?: string; model?: string; api_key?: string }): Promise<LlmSettings>;
	getLimitsSettings(): Promise<LimitsSettings>;
	setLimitsSettings(s: {
		readings_per_day?: number;
		llm_tokens_per_day?: number;
		tts_minutes_per_day?: number;
	}): Promise<LimitsSettings>;
	adminUsers(): Promise<AdminUser[]>;
	adminUserUpdate(
		u: string,
		p: { display_name?: string; active?: boolean; is_admin?: boolean }
	): Promise<AdminUser>;
	adminReassign(u: string, to: string): Promise<ReassignReport>;
	adminUserDelete(u: string): Promise<Record<string, unknown>>;
	adminUsage(days: number): Promise<UsageSummary>;
	getTtsSettings(): Promise<TtsSettings>;
	setTtsSettings(s: {
		provider?: string;
		base_url?: string;
		model?: string;
		api_key?: string;
		voices?: Record<string, VoiceValues>;
	}): Promise<TtsSettings>;
	listProviderVoices(): Promise<ProviderVoice[]>;
	designVoice(description: string, preview_text?: string): Promise<DesignedVoice[]>;
	keepDesignedVoice(v: {
		generated_voice_id: string;
		name: string;
		description: string;
		rejected?: string[];
	}): Promise<{ voice_id: string }>;
}
