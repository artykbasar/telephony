import { getApps, initializeApp } from "firebase/app";
import { deleteToken, getMessaging, getToken, isSupported, onMessage as onFCMMessage } from "firebase/messaging";

const TELEPHONY_VOICE_STORAGE_KEY = "telephony-voice-outbound-number";
const TELEPHONY_MIC_STORAGE_KEY = "telephony-voice-microphone";
const TELEPHONY_SPEAKER_STORAGE_KEY = "telephony-voice-speaker";
const TELEPHONY_AUDIO_PROCESSING_KEY = "telephony-voice-audio-processing";
const TELEPHONY_VOICE_LAYOUT_KEY = "telephony-voice-layout-v1";
const TELEPHONY_TAB_STORAGE_KEY = "telephony-voice-tab-id-v1";
const TELEPHONY_SOFTPHONE_WIDTH = 280;
const TELEPHONY_SOFTPHONE_HEIGHT = 560;
const TELEPHONY_SOFTPHONE_COMPACT_HEIGHT = 448;
const TELEPHONY_SOFTPHONE_ACTION_ROW_HEIGHT = 76;
const TELEPHONY_SOFTPHONE_VIEWPORT_RATIO = 0.82;
const TELEPHONY_SOFTPHONE_MIN_SCALE = 0.62;
const TELEPHONY_WIDGET_VIEWPORT_MARGIN = 8;
const TELEPHONY_PANEL_ANCHOR_GAP = 24;
const TELEPHONY_PANEL_FLIP_HYSTERESIS = 48;
const TELEPHONY_TOGGLE_DOCK_GAP = 24;
const TELEPHONY_TOGGLE_DOCK_HYSTERESIS = 24;
const TELEPHONY_HISTORY_PREFETCH_PX = 240;
const TELEPHONY_CONTACTS_PREFETCH_PX = 240;
const TELEPHONY_PUSH_PROJECT_NAME = "telephony";
const TELEPHONY_PUSH_TOKEN_KEY = `firebase_token_${TELEPHONY_PUSH_PROJECT_NAME}`;
const TELEPHONY_PUSH_DEEP_LINK_KEY = "telephony_call";

function telephonyNotificationAssetUrl(value) {
	const asset = String(value || "").trim();
	if (!asset) return "";
	try {
		return new URL(asset, window.location.origin).href;
	} catch (_) {
		return "";
	}
}

const voiceStateLabels = {
	idle: __("Ready"),
	new: __("Starting"),
	dialing: __("Dialing"),
	ringing: __("Ringing"),
	connecting: __("Connecting"),
	connected: __("Connected"),
	held: __("Held"),
	resume_required: __("Resume on this browser"),
	other_device: __("Active in another browser"),
	disconnecting: __("Ending"),
	ended: __("Ended"),
	failed: __("Failed"),
};

class TelephonyVoiceSoftphone {
	constructor(access, numbers) {
		this.access = access;
		this.numbers = numbers || [];
		this.voiceWorker = null;
		this.voiceStarting = false;
		this.voiceReconnectTimer = null;
		this.controlReady = false;
		this.sessionId = null;
		this.tabId = this.getOrCreateTabId();
		this.handsetClaimPending = false;
		this.mediaResumeRequired = false;
		this.autoRestoreTimer = null;
		this.autoRestoredCallIds = new Set();
		this.currentCall = null;
		this.mediaCallId = null;
		this.mediaRunning = false;
		this.mediaPendingCallId = null;
		this.mediaSetupPromise = null;
		this.mediaAttachWaiter = null;
		this.mediaLifecycleGeneration = 0;
		this.mediaStats = null;
		this.serverMediaStatus = null;
		this.playbackStats = { underruns: 0, queuedMs: 0, outputSampleRate: 0 };
		this.mediaFlowEvents = [];
		this.mediaConnectedAt = new Map();
		this.mediaStartupTiming = null;
		this.lastMediaStartupTiming = null;
		this.registration = {};
		this.capabilities = {};
		this.transportRttMs = null;
		this.preflightChecks = [];
		this.muted = false;
		this.preferredMicId = localStorage.getItem(TELEPHONY_MIC_STORAGE_KEY) || "";
		this.preferredSpeakerId = localStorage.getItem(TELEPHONY_SPEAKER_STORAGE_KEY) || "";
		this.audioProcessing = localStorage.getItem(TELEPHONY_AUDIO_PROCESSING_KEY) !== "0";
		this.devices = { inputs: [], outputs: [] };
		this.preparedAudioContext = null;
		this.preparedLocalStream = null;
		this.preparedMicrophonePromise = null;
		this.preparedMicrophoneGeneration = 0;
		this.lastMicrophonePreparationMs = null;
		this.callSequences = new Map();
		this.waitingCall = null;
		this.parkedCall = null;
		this.transferPending = false;
		this.blindTransfer = null;
		this.attendedTransfer = null;
		this.transferredCallIds = new Set();
		this.pendingCallSwitch = null;
		this.ringtoneSource = null;
		this.ringtoneGain = null;
		this.ringtoneLoading = null;
		this.ringtoneGeneration = 0;
		this.ringbackTimer = null;
		this.ringbackNodes = new Set();
		this.keypadToneSource = null;
		this.keypadToneGain = null;
		this.uiAudioBuffers = new Map();
		this.connectedAt = null;
		this.durationTimer = null;
		this.visibleActionRows = 1;
		this.keypadManuallyShown = false;
		this.layoutPrefs = this.loadLayoutPrefs();
		this.dragging = false;
		this.dragMoved = false;
		this.dragStart = null;
		this.dragOffset = null;
		this.dragPointer = null;
		this.dragFrame = null;
		this.panelPlacement = { horizontal: null, vertical: null };
		this.panelBodyPosition = null;
		this.panelDockSide = null;
		this.panelDragState = null;
		this.dragSource = null;
		this.boundDragMove = (event) => this.onDragMove(event);
		this.boundDragEnd = () => this.endDrag();
		this.boundResizeHandler = () => this.handleViewportResize();
		this.view = "dialer";
		this.historyItems = [];
		this.historyCursor = null;
		this.historyHasMore = true;
		this.historyLoading = false;
		this.historyLoaded = false;
		this.historyDirty = true;
		this.historyError = null;
		this.historyRequestGeneration = 0;
		this.historyLastGroup = null;
		this.historyFilter = "all";
		this.historySelectedItem = null;
		this.historySelectedContact = null;
		this.historySelectedContactLoading = false;
		this.historySelectedContactRequest = 0;
		this.historyContactEditorMode = null;
		this.historyContactSearchResults = [];
		this.historyContactSearchText = "";
		this.historyContactSearchLoading = false;
		this.historyContactSearchRequest = 0;
		this.historyContactSearchTimer = null;
		this.historyContactSaving = false;
		this.contactsItems = [];
		this.contactsCursor = null;
		this.contactsHasMore = true;
		this.contactsLoading = false;
		this.contactsLoaded = false;
		this.contactsError = null;
		this.contactsRequestGeneration = 0;
		this.contactsSearchTimer = null;
		this.contactsSelectedItem = null;
		this.contactResolutionCache = new Map();
		this.contactResolutionPending = new Map();
		this.callNotificationStatus = null;
		this.callNotificationBusy = false;
		this.pushMessaging = null;
		this.pushRegistration = null;
		this.pushToken = localStorage.getItem(TELEPHONY_PUSH_TOKEN_KEY) || "";
		this.pushForegroundUnsubscribe = null;
		this.deepLinkedCallId = this.readDeepLinkedCallId();
		this.mount();
	}

	mount() {
		if (document.getElementById("telephony-sip-softphone")) return;
		const keypadLetters = { 2: "ABC", 3: "DEF", 4: "GHI", 5: "JKL", 6: "MNO", 7: "PQRS", 8: "TUV", 9: "WXYZ", 0: "+" };
		const keypadHtml = ["1","2","3","4","5","6","7","8","9","*","0","#"]
			.map((digit) => `<button type="button" data-digit="${digit}"><span class="tp-softphone-keypad-digit">${digit}</span><span class="tp-softphone-keypad-letters">${keypadLetters[digit] || ""}</span></button>`)
			.join("");
		this.$root = $(`<div id="telephony-sip-softphone" class="telephony-voice-root">
			<div class="tp-softphone-toggle">
				<button type="button" class="tp-softphone-toggle-main" aria-expanded="false" aria-label="${__("Desk softphone")}">
					<span class="tp-softphone-toggle-icon" aria-hidden="true">
						<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16">
							<path fill-rule="evenodd" d="M1.885.511a1.745 1.745 0 0 1 2.61.163L6.29 2.98c.329.423.445.974.315 1.494l-.547 2.19a.68.68 0 0 0 .178.643l2.457 2.457a.68.68 0 0 0 .644.178l2.189-.547a1.75 1.75 0 0 1 1.494.315l2.306 1.794c.829.645.905 1.87.163 2.611l-1.034 1.034c-.74.74-1.846 1.065-2.877.702a18.6 18.6 0 0 1-7.01-4.42 18.6 18.6 0 0 1-4.42-7.009c-.362-1.03-.037-2.137.703-2.877z"/>
						</svg>
					</span>
				</button>
				<span class="tp-softphone-toggle-timer" aria-hidden="true"></span>
				<div class="tp-softphone-toggle-inline-controls">
					<button type="button" class="tp-softphone-toggle-inline-btn tp-softphone-mute" aria-label="${__("Mute call")}">
						<span class="tp-softphone-inline-icon tp-softphone-inline-mic"></span>
					</button>
					<button type="button" class="tp-softphone-toggle-inline-btn tp-softphone-hangup" aria-label="${__("End call")}">
						<span class="tp-softphone-inline-icon tp-softphone-inline-hangup"></span>
					</button>
				</div>
			</div>
			<div class="tp-softphone-panel" aria-hidden="true">
				<div class="tp-softphone-content">
					<div class="tp-softphone-inner">
						<div class="tp-softphone-header">
							<div class="tp-softphone-profile">
								<div class="tp-softphone-profile-avatar" data-voice="profile-avatar" aria-hidden="true"></div>
								<div class="tp-softphone-profile-copy">
									<div class="tp-softphone-profile-name" data-voice="profile-name"></div>
									<div class="tp-softphone-status"><span class="tp-softphone-status-dot"></span><span data-voice="status">${__("Connecting")}…</span><span class="tp-softphone-profile-number" data-voice="profile-number"></span></div>
									<div class="tp-softphone-build" aria-hidden="true"></div>
								</div>
							</div>
							<div class="tp-softphone-header-actions">
								<button type="button" class="tp-softphone-close" aria-label="${__("Close panel")}">×</button>
							</div>
						</div>
						<div class="tp-softphone-view-tabs" role="tablist" aria-label="${__("Softphone view")}">
							<button type="button" class="tp-softphone-view-tab active" data-softphone-view="dialer" role="tab" aria-selected="true" aria-label="${__("Dialer")}" title="${__("Dialer")}"><span class="tp-softphone-view-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="7" cy="6" r="1.5"/><circle cx="12" cy="6" r="1.5"/><circle cx="17" cy="6" r="1.5"/><circle cx="7" cy="11" r="1.5"/><circle cx="12" cy="11" r="1.5"/><circle cx="17" cy="11" r="1.5"/><circle cx="7" cy="16" r="1.5"/><circle cx="12" cy="16" r="1.5"/><circle cx="17" cy="16" r="1.5"/><circle cx="12" cy="21" r="1.5"/></svg></span></button>
							<button type="button" class="tp-softphone-view-tab" data-softphone-view="history" role="tab" aria-selected="false" aria-label="${__("History")}" title="${__("History")}"><span class="tp-softphone-view-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 7.5v5l3.3 2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span></button>
							<button type="button" class="tp-softphone-view-tab" data-softphone-view="contacts" role="tab" aria-selected="false" aria-label="${__("Contacts")}" title="${__("Contacts")}"><span class="tp-softphone-view-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M5.5 19c.7-3.4 3-5.2 6.5-5.2s5.8 1.8 6.5 5.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></span></button>
							<button type="button" class="tp-softphone-view-tab" data-softphone-view="settings" role="tab" aria-selected="false" aria-label="${__("Settings")}" title="${__("Settings")}"><span class="tp-softphone-view-icon" aria-hidden="true"><svg class="icon icon-sm tp-softphone-frappe-settings-icon" viewBox="0 0 24 24"><use href="#icon-settings"></use></svg></span></button>
						</div>
						<div class="tp-softphone-display tp-softphone-dialer-item">
							<div class="tp-softphone-call-state-row">
								<span class="tp-softphone-call-state-dot" aria-hidden="true"></span>
								<div class="tp-softphone-remote-label" data-voice="call-state-label">${__("Ready")}</div>
								<div class="tp-softphone-timer" data-voice="duration" aria-hidden="true"></div>
							</div>
							<div class="tp-softphone-attended-summary" data-voice="attended-summary" aria-hidden="true">
								<div class="tp-softphone-attended-banner">
									<span class="tp-softphone-attended-dot" aria-hidden="true"></span>
									<div class="tp-softphone-attended-banner-copy">
										<strong data-voice="attended-stage-title">${__("Attended Transfer")}</strong>
										<span data-voice="attended-stage-detail"></span>
									</div>
								</div>
								<div class="tp-softphone-attended-legs">
									<div class="tp-softphone-attended-leg tp-softphone-attended-original">
										<span class="tp-softphone-attended-leg-icon" aria-hidden="true">Ⅱ</span>
										<div class="tp-softphone-attended-leg-copy"><strong data-voice="attended-original-party"></strong><span><span>${__("Original")}</span> · <span data-voice="attended-original-state"></span> · <span data-voice="attended-original-time"></span></span></div>
									</div>
									<div class="tp-softphone-attended-leg tp-softphone-attended-consult">
										<span class="tp-softphone-attended-leg-icon" aria-hidden="true">☎</span>
										<div class="tp-softphone-attended-leg-copy"><strong data-voice="attended-consult-party"></strong><span><span>${__("Consult")}</span> · <span data-voice="attended-consult-state"></span> · <span data-voice="attended-consult-time"></span></span></div>
									</div>
								</div>
							</div>
							<div class="tp-softphone-party-card">
								<div class="tp-softphone-party-avatar" data-voice="party-avatar">T</div>
								<div class="tp-softphone-party-copy">
									<div class="tp-softphone-remote-value" data-voice="party">${__("Ready")}</div>
									<div class="tp-softphone-party-number" data-voice="party-number"></div>
								</div>
							</div>
							<div class="tp-softphone-input-wrap">
								<input type="text" class="tp-softphone-dial-input" data-voice="number" inputmode="tel" autocomplete="off" placeholder="${__("Enter number or name")}" />
								<button type="button" class="tp-softphone-input-clear" data-action="backspace" aria-label="${__("Backspace")}" title="${__("Backspace")}">⌫</button>
							</div>
						</div>
						<div class="tp-softphone-waiting tp-softphone-dialer-item" data-voice="waiting-call">
							<strong data-voice="waiting-party"></strong>
							<div class="tp-softphone-waiting-actions">
								<button type="button" class="tp-softphone-waiting-answer" data-action="answer-waiting">${__("Answer")}</button>
								<button type="button" class="tp-softphone-waiting-decline" data-action="decline-waiting">${__("Decline")}</button>
							</div>
						</div>
						<div class="tp-softphone-actions tp-softphone-dialer-item" data-voice="actions">
							<button type="button" class="tp-softphone-btn primary tp-softphone-icon-only" data-action="call" aria-label="${__("Call")}" title="${__("Call")}">
								<span class="tp-softphone-btn-icon tp-softphone-answer-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Call</span>
							</button>
							<button type="button" class="tp-softphone-btn success tp-softphone-icon-only" data-action="answer" aria-label="${__("Answer")}" title="${__("Answer")}">
								<span class="tp-softphone-btn-icon tp-softphone-answer-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Answer</span>
							</button>
							<button type="button" class="tp-softphone-btn success tp-softphone-icon-only" data-action="resume-handset" aria-label="${__("Resume call on this browser")}" title="${__("Resume call on this browser")}">
								<span class="tp-softphone-btn-icon tp-softphone-answer-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Resume</span>
							</button>
							<button type="button" class="tp-softphone-btn danger tp-softphone-icon-only" data-action="end" aria-label="${__("End call")}" title="${__("End call")}">
								<span class="tp-softphone-btn-icon tp-softphone-end-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">End</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="mute" aria-label="${__("Mute")}" title="${__("Mute")}">
								<span class="tp-softphone-btn-icon tp-softphone-mute-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Mute</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="hold" aria-label="${__("Hold")}" title="${__("Hold")}">
								<span class="tp-softphone-btn-icon tp-softphone-hold-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Hold</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="transfer" aria-label="${__("Transfer")}" title="${__("Transfer")}">
								<span class="tp-softphone-btn-icon tp-softphone-transfer-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Transfer</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="attended-transfer" aria-label="${__("Attended transfer")}" title="${__("Attended transfer")}">
								<span class="tp-softphone-btn-icon tp-softphone-attended-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Attended</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="swap" aria-label="${__("Swap calls")}" title="${__("Swap calls")}">
								<span class="tp-softphone-btn-icon tp-softphone-swap-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Swap</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="cancel-transfer" aria-label="${__("Cancel transfer")}" title="${__("Cancel transfer")}">
								<span class="tp-softphone-btn-icon tp-softphone-transfer-back-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">Back</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="keypad" aria-label="${__("Show keypad")}" title="${__("Show keypad")}">
								<span class="tp-softphone-inline-icon tp-softphone-dialpad-icon" aria-hidden="true"></span><span class="tp-softphone-btn-label">${__("Keypad")}</span>
							</button>
							<button type="button" class="tp-softphone-btn secondary tp-softphone-icon-only" data-action="transfer-contacts" aria-label="${__("Choose transfer contact")}" title="${__("Choose transfer contact")}">
								<span class="tp-softphone-transfer-contact-icon" aria-hidden="true">♙</span><span class="tp-softphone-btn-label">${__("Contacts")}</span>
							</button>
						</div>
						<div class="tp-softphone-keypad tp-softphone-dialer-item" data-voice="keypad">${keypadHtml}</div>
						<div class="tp-softphone-note tp-softphone-dialer-item" data-voice="note"></div>
						<div class="tp-softphone-history-view" data-voice="history-view" aria-hidden="true">
							<div class="tp-softphone-history-list-pane" data-voice="history-list-pane">
								<div class="tp-softphone-history-filters" role="group" aria-label="${__("Filter call history")}">
									<button type="button" class="tp-softphone-history-filter active" data-history-filter="all" aria-pressed="true">${__("All")}</button>
									<button type="button" class="tp-softphone-history-filter" data-history-filter="missed" aria-pressed="false">${__("Missed")}</button>
									<button type="button" class="tp-softphone-history-filter tp-softphone-history-filter-icon" data-history-filter="incoming" aria-pressed="false" aria-label="${__("Incoming")}" title="${__("Incoming")}"><svg class="icon icon-sm tp-softphone-history-filter-direction-icon" aria-hidden="true"><use href="#icon-arrow-down-left"></use></svg></button>
									<button type="button" class="tp-softphone-history-filter tp-softphone-history-filter-icon" data-history-filter="outgoing" aria-pressed="false" aria-label="${__("Outgoing")}" title="${__("Outgoing")}"><svg class="icon icon-sm tp-softphone-history-filter-direction-icon" aria-hidden="true"><use href="#icon-arrow-up-right"></use></svg></button>
								</div>
								<button type="button" class="tp-softphone-history-active" data-action="history-return-call">
									<span class="tp-softphone-history-live-dot" aria-hidden="true"></span>
									<span class="tp-softphone-history-active-party" data-voice="history-active-party"></span>
									<span class="tp-softphone-history-active-timer" data-voice="history-active-timer"></span>
								</button>
								<div class="tp-softphone-history-list" data-voice="history-list"></div>
								<div class="tp-softphone-history-state" data-voice="history-state"></div>
								<button type="button" class="tp-softphone-history-full" data-route="call-log">${__("View all history")} <span aria-hidden="true">›</span></button>
							</div>
							<div class="tp-softphone-history-detail" data-voice="history-detail" aria-hidden="true">
								<div class="tp-softphone-history-detail-scroll" data-voice="history-detail-content"></div>
							</div>
						</div>
						<div class="tp-softphone-contacts-view" data-voice="contacts-view" aria-hidden="true">
							<div class="tp-softphone-contacts-list-pane" data-voice="contacts-list-pane">
								<div class="tp-softphone-contact-search-wrap">
									<span class="tp-softphone-contact-search-icon" aria-hidden="true">⌕</span>
									<input type="search" class="tp-softphone-contact-search" data-voice="contact-search" autocomplete="off" placeholder="${__("Search name, company, email, phone…")}" />
								</div>
								<div class="tp-softphone-contacts-list" data-voice="contacts-list"></div>
								<div class="tp-softphone-contacts-state" data-voice="contacts-state"></div>
								<button type="button" class="tp-softphone-history-full" data-route="contacts">${__("View all contacts")} <span aria-hidden="true">›</span></button>
							</div>
							<div class="tp-softphone-contact-detail" data-voice="contact-detail" aria-hidden="true">
								<div class="tp-softphone-history-detail-scroll" data-voice="contact-detail-content"></div>
							</div>
						</div>
						<div class="tp-softphone-settings-view" data-voice="settings-view" aria-hidden="true">
							<div class="tp-softphone-settings-scroll">
								<section class="tp-softphone-settings-section tp-softphone-settings-audio">
									<div class="tp-softphone-settings-section-head"><div><strong>${__("Audio")}</strong><span>${__("Calling devices and processing")}</span></div></div>
									<div class="tp-softphone-setting-row telephony-voice-number-choice"><label>${__("Outbound number")}</label><select data-voice="from"></select></div>
									<div class="tp-softphone-setting-row"><label>${__("Microphone")}</label><select data-voice="microphone"></select></div>
									<div class="tp-softphone-setting-row"><label>${__("Speaker")}</label><select data-voice="speaker"></select></div>
									<label class="tp-softphone-setting-check"><input type="checkbox" data-voice="audio-processing"> <span>${__("Echo cancellation, noise suppression and automatic gain")}</span></label>
								</section>
								<section class="tp-softphone-settings-section tp-softphone-settings-notifications">
									<div class="tp-softphone-settings-section-head"><div><strong>${__("Call notifications")}</strong><span>${__("Push alerts for incoming Telephony calls")}</span></div></div>
									<div class="tp-softphone-call-notification-card">
										<div class="tp-softphone-call-notification-copy"><span class="tp-softphone-call-notification-dot" data-voice="call-notification-dot"></span><div><strong data-voice="call-notification-title">${__("Checking…")}</strong><span data-voice="call-notification-detail">${__("Checking this device and the Frappe push relay.")}</span></div></div>
										<button type="button" data-action="toggle-call-notifications" data-voice="call-notification-toggle">${__("Enable")}</button>
									</div>
								</section>
								<section class="tp-softphone-settings-section tp-softphone-settings-readiness">
									<div class="tp-softphone-settings-section-head">
										<div><strong>${__("Readiness")}</strong><span data-voice="readiness-summary">${__("Checking browser and SIP status…")}</span></div>
										<button type="button" class="tp-softphone-settings-refresh" data-action="run-preflight" aria-label="${__("Refresh readiness checks")}" title="${__("Refresh readiness checks")}">↻</button>
									</div>
									<div class="tp-softphone-readiness-primary" data-voice="readiness-primary"></div>
									<details class="tp-softphone-readiness-details">
										<summary>${__("Show all checks")}</summary>
										<div class="tp-softphone-readiness-list" data-voice="readiness-list"></div>
									</details>
								</section>
								<section class="tp-softphone-settings-section tp-softphone-settings-quick-links">
									<div class="tp-softphone-settings-section-head"><div><strong>${__("Quick links")}</strong><span>${__("Open Telephony records and tools")}</span></div></div>
									<div class="tp-softphone-settings-links">
										<button type="button" data-route="call-log">${__("Call history")}</button>
										
									</div>
								</section>
								<section class="tp-softphone-settings-section tp-softphone-settings-diagnostics">
									<div class="tp-softphone-settings-section-head">
										<div><strong>${__("Diagnostics")}</strong><span data-voice="diagnostics-glance">${__("Transport and media details")}</span></div>
										<span data-voice="quality" class="tp-softphone-quality-inline"></span>
									</div>
									<details class="tp-softphone-diagnostics-details">
										<summary>${__("Show diagnostics")}</summary>
										<div class="tp-softphone-diagnostics-summary" data-voice="diagnostics-summary"></div>
										<details class="tp-softphone-raw-diagnostics">
											<summary>${__("Raw diagnostics")}</summary>
											<pre class="tp-softphone-diagnostics-json" data-voice="diagnostics-json"></pre>
										</details>
									</details>
								</section>
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>`).appendTo(document.body);

		this.root = this.$root[0];
		this.$panel = this.$root.find(".tp-softphone-panel");
		this.panel = this.$panel[0];
		this.panelContent = this.$root.find(".tp-softphone-content")[0];
		this.$toggle = this.$root.find(".tp-softphone-toggle");
		this.button = this.$toggle[0];
		this.$toggleMain = this.$root.find(".tp-softphone-toggle-main");
		this.$toggleTimer = this.$root.find(".tp-softphone-toggle-timer");
		this.$status = this.$root.find('[data-voice="status"]');
		this.$profileAvatar = this.$root.find('[data-voice="profile-avatar"]');
		this.$profileName = this.$root.find('[data-voice="profile-name"]');
		this.$profileNumber = this.$root.find('[data-voice="profile-number"]');
		this.$party = this.$root.find('[data-voice="party"]');
		this.$partyAvatar = this.$root.find('[data-voice="party-avatar"]');
		this.$partyNumber = this.$root.find('[data-voice="party-number"]');
		this.$callStateLabel = this.$root.find('[data-voice="call-state-label"]');
		this.$duration = this.$root.find('[data-voice="duration"]');
		this.$attendedSummary = this.$root.find('[data-voice="attended-summary"]');
		this.$attendedStageTitle = this.$root.find('[data-voice="attended-stage-title"]');
		this.$attendedStageDetail = this.$root.find('[data-voice="attended-stage-detail"]');
		this.$attendedOriginalParty = this.$root.find('[data-voice="attended-original-party"]');
		this.$attendedOriginalState = this.$root.find('[data-voice="attended-original-state"]');
		this.$attendedOriginalTime = this.$root.find('[data-voice="attended-original-time"]');
		this.$attendedConsultParty = this.$root.find('[data-voice="attended-consult-party"]');
		this.$attendedConsultState = this.$root.find('[data-voice="attended-consult-state"]');
		this.$attendedConsultTime = this.$root.find('[data-voice="attended-consult-time"]');
		this.$number = this.$root.find('[data-voice="number"]');
		this.$from = this.$root.find('[data-voice="from"]');
		this.$note = this.$root.find('[data-voice="note"]');
		this.$keypad = this.$root.find('[data-voice="keypad"]');
		this.$actions = this.$root.find('[data-voice="actions"]');
		this.$quality = this.$root.find('[data-voice="quality"]');
		this.$settingsView = this.$root.find('[data-voice="settings-view"]');
		this.$callNotificationDot = this.$root.find('[data-voice="call-notification-dot"]');
		this.$callNotificationTitle = this.$root.find('[data-voice="call-notification-title"]');
		this.$callNotificationDetail = this.$root.find('[data-voice="call-notification-detail"]');
		this.$callNotificationToggle = this.$root.find('[data-voice="call-notification-toggle"]');
		this.$readinessSummary = this.$root.find('[data-voice="readiness-summary"]');
		this.$readinessPrimary = this.$root.find('[data-voice="readiness-primary"]');
		this.$readinessList = this.$root.find('[data-voice="readiness-list"]');
		this.$diagnosticsGlance = this.$root.find('[data-voice="diagnostics-glance"]');
		this.$diagnosticsSummary = this.$root.find('[data-voice="diagnostics-summary"]');
		this.$diagnosticsJson = this.$root.find('[data-voice="diagnostics-json"]');
		this.$microphone = this.$root.find('[data-voice="microphone"]');
		this.$speaker = this.$root.find('[data-voice="speaker"]');
		this.$audioProcessing = this.$root.find('[data-voice="audio-processing"]');
		this.$waitingCall = this.$root.find('[data-voice="waiting-call"]');
		this.$waitingParty = this.$root.find('[data-voice="waiting-party"]');
		this.$historyView = this.$root.find('[data-voice="history-view"]');
		this.$historyListPane = this.$root.find('[data-voice="history-list-pane"]');
		this.$historyList = this.$root.find('[data-voice="history-list"]');
		this.$historyState = this.$root.find('[data-voice="history-state"]');
		this.$historyDetail = this.$root.find('[data-voice="history-detail"]');
		this.$historyDetailContent = this.$root.find('[data-voice="history-detail-content"]');
		this.$historyActive = this.$root.find('[data-action="history-return-call"]');
		this.$historyActiveParty = this.$root.find('[data-voice="history-active-party"]');
		this.$historyActiveTimer = this.$root.find('[data-voice="history-active-timer"]');
		this.$contactsView = this.$root.find('[data-voice="contacts-view"]');
		this.$contactsListPane = this.$root.find('[data-voice="contacts-list-pane"]');
		this.$contactsList = this.$root.find('[data-voice="contacts-list"]');
		this.$contactsState = this.$root.find('[data-voice="contacts-state"]');
		this.$contactSearch = this.$root.find('[data-voice="contact-search"]');
		this.$contactDetail = this.$root.find('[data-voice="contact-detail"]');
		this.$contactDetailContent = this.$root.find('[data-voice="contact-detail-content"]');
		this.$tools = $();
		this.$devicesPanel = $();
		this.$diagnosticsGrid = $();
		this.$preflight = $();

		this.$toggleMain.on("click", () => {
			if (this.dragMoved) { this.dragMoved = false; return; }
			this.toggle();
		});
		this.$root.find(".tp-softphone-close").on("click", () => this.toggle(false));
		this.$root.find(".tp-softphone-toggle-inline-btn.tp-softphone-mute").on("click", (event) => { event.stopPropagation(); this.toggleMute(); });
		this.$root.find(".tp-softphone-toggle-inline-btn.tp-softphone-hangup").on("click", (event) => { event.stopPropagation(); this.hangup(); });
		this.$root.find('[data-action="call"]').on("click", () => void this.dial());
		this.$root.find('[data-action="answer"]').on("click", () => void this.answer());
		this.$root.find('[data-action="resume-handset"]').on("click", () => void this.resumeHandset());
		this.$root.find('[data-action="end"]').on("click", () => {
			if (this.currentCall?.direction === "incoming" && this.currentCall.state === "ringing") this.reject();
			else this.hangup();
		});
		this.$root.find('[data-action="mute"]').on("click", () => this.toggleMute());
		this.$root.find('[data-action="hold"]').on("click", () => {
			if (this.currentCall?.state === "held") void this.resumeCall();
			else this.hold();
		});
		this.$root.find('[data-action="transfer"]').on("click", () => void this.startBlindTransfer());
		this.$root.find('[data-action="attended-transfer"]').on("click", () => void this.attendedTransferAction());
		this.$root.find('[data-action="swap"]').on("click", () => void this.swapCalls());
		this.$root.find('[data-action="cancel-transfer"]').on("click", () => void this.cancelAnyTransfer());
		this.$root.find('[data-action="keypad"]').on("click", () => this.toggleKeypad());
		this.$root.find('[data-action="transfer-contacts"]').on("click", () => void this.setSoftphoneView("contacts"));
		this.$root.find('[data-action="answer-waiting"]').on("click", () => void this.answerWaiting());
		this.$root.find('[data-action="decline-waiting"]').on("click", () => this.declineWaiting());
		this.$root.find('[data-action="run-preflight"]').on("click", () => void this.runPreflight());
		this.$root.find('[data-action="toggle-call-notifications"]').on("click", () => void this.toggleCallNotifications());
		this.$root.find('[data-softphone-view]').on("click", (event) => {
			void this.setSoftphoneView(String($(event.currentTarget).data("softphone-view") || "dialer"));
		});
		this.$historyActive.on("click", () => void this.setSoftphoneView("dialer"));
		this.$historyList.on("scroll", () => this.maybeLoadMoreHistory());
		this.$contactsList.on("scroll", () => this.maybeLoadMoreContacts());
		this.$root.find("[data-history-filter]").on("click", (event) => {
			void this.setHistoryFilter(String($(event.currentTarget).data("history-filter") || "all"));
		});
		this.$historyList.on("click", "[data-history-number]", (event) => {
			event.stopPropagation();
			void this.callFromHistory(String($(event.currentTarget).data("history-number") || ""));
		});
		this.$historyList.on("click", "[data-history-id]", (event) => {
			if ($(event.target).closest("[data-history-number]").length) return;
			this.openHistoryDetail(String($(event.currentTarget).data("history-id") || ""));
		});
		this.$historyList.on("keydown", "[data-history-id]", (event) => {
			if (event.key !== "Enter" && event.key !== " ") return;
			if ($(event.target).closest("button").length) return;
			event.preventDefault();
			this.openHistoryDetail(String($(event.currentTarget).data("history-id") || ""));
		});
		this.$historyDetail.on("click", '[data-action="history-detail-back"]', () => this.closeHistoryDetail());
		this.$historyDetail.on("click", '[data-action="history-call-again"]', (event) => {
			event.stopPropagation();
			void this.callFromHistory(String($(event.currentTarget).data("history-number") || ""));
		});
		this.$historyDetail.on("click", '[data-action="history-open-record"]', () => {
			if (this.historySelectedItem?.name) frappe.set_route("Form", "TP Call Log", this.historySelectedItem.name);
		});
		this.$historyDetail.on("click", "[data-history-contact-number]", (event) => {
			event.stopPropagation();
			void this.callFromHistory(String($(event.currentTarget).data("history-contact-number") || ""));
		});
		this.$historyDetail.on("click", '[data-action="history-open-contact"]', () => {
			if (!this.historySelectedContact) return;
			this.contactsSelectedItem = this.historySelectedContact;
			this.$contactsView?.addClass?.("tp-softphone-contact-detail-open");
			this.$contactDetail?.attr?.("aria-hidden", "false");
			this.setSoftphoneView("contacts");
		});
		this.$historyDetail.on("click", '[data-action="history-create-contact"]', () => this.openHistoryContactEditor("create"));
		this.$historyDetail.on("click", '[data-action="history-add-existing-contact"]', () => this.openHistoryContactEditor("attach"));
		this.$historyDetail.on("click", '[data-action="history-contact-editor-cancel"]', () => this.closeHistoryContactEditor());
		this.$historyDetail.on("submit", "[data-history-contact-create-form]", (event) => {
			event.preventDefault();
			void this.saveHistoryContactFromForm($(event.currentTarget));
		});
		this.$historyDetail.on("input", "[data-history-contact-search]", (event) => {
			clearTimeout(this.historyContactSearchTimer);
			const query = String($(event.currentTarget).val() || "");
			this.historyContactSearchText = query;
			this.historyContactSearchTimer = setTimeout(() => void this.searchHistoryContacts(query), 180);
		});
		this.$historyDetail.on("click", "[data-history-contact-choice]", (event) => {
			void this.addHistoryNumberToContact(String($(event.currentTarget).data("history-contact-choice") || ""));
		});
		this.$contactsList.on("click", "[data-contact-number]", (event) => {
			event.stopPropagation();
			void this.callFromHistory(String($(event.currentTarget).data("contact-number") || ""));
		});
		this.$contactsList.on("click", "[data-contact-id]", (event) => {
			this.openContactDetail(String($(event.currentTarget).data("contact-id") || ""));
		});
		this.$contactDetail.on("click", '[data-action="contact-detail-back"]', () => this.closeContactDetail());
		this.$contactDetail.on("click", "[data-contact-detail-number]", (event) => {
			event.stopPropagation();
			void this.callFromHistory(String($(event.currentTarget).data("contact-detail-number") || ""));
		});
		this.$contactDetail.on("click", '[data-action="contact-open-record"]', () => {
			if (this.contactsSelectedItem?.name) frappe.set_route("Form", "Contact", this.contactsSelectedItem.name);
		});
		this.$contactSearch.on("input", () => {
			clearTimeout(this.contactsSearchTimer);
			this.contactsSearchTimer = setTimeout(() => void this.loadContacts({ reset: true }), 220);
		});
		this.$root.find("[data-digit]").on("click", (event) => this.pressDigit(String($(event.currentTarget).data("digit"))));
		this.$root.find('[data-action="backspace"]').on("click", () => this.backspaceNumber());
		this.$number.on("input", () => this.updateClearButtonVisibility());
		this.$number.on("keydown", (event) => { if (event.key === "Enter") void this.dial(); });
		this.$from.on("change", () => {
			localStorage.setItem(TELEPHONY_VOICE_STORAGE_KEY, this.$from.val() || "");
			this.renderHeaderNumber();
		});
		this.$microphone.on("change", () => void this.changeMicrophone(String(this.$microphone.val() || "")));
		this.$speaker.on("change", () => void this.changeSpeaker(String(this.$speaker.val() || "")));
		this.$audioProcessing.prop("checked", this.audioProcessing).on("change", () => void this.changeAudioProcessing(Boolean(this.$audioProcessing.prop("checked"))));
		this.$root.find('[data-route="call-log"]').on("click", () => frappe.set_route("List", "TP Call Log"));
		this.$root.find('[data-route="contacts"]').on("click", () => frappe.set_route("List", "Contact"));

		this.root?.classList.add("tp-softphone-keypad-visible");
		this.renderNumbers();
		this.renderUserIdentity();
		
		this.updateClearButtonVisibility();
		this.refresh();
		this.applyLayoutPrefs();
		this.initDrag();
		window.addEventListener("resize", this.boundResizeHandler);
		this.deviceChangeHandler = () => void this.refreshDevices();
		navigator.mediaDevices?.addEventListener?.("devicechange", this.deviceChangeHandler);
		this.diagnosticsTimer = setInterval(() => {
			this.renderQuality();
			if (this.view === "settings") this.renderDiagnostics();
		}, 500);
		void this.refreshDevices();
		void this.runPreflight();
		this.renderCallNotificationStatus();
		void this.refreshCallNotificationStatus({ initializeExisting: true });
		void this.startControl();
	}

	readDeepLinkedCallId() {
		try {
			return String(new URL(window.location.href).searchParams.get(TELEPHONY_PUSH_DEEP_LINK_KEY) || "").trim();
		} catch (_) {
			return "";
		}
	}

	clearDeepLinkedCallId() {
		this.deepLinkedCallId = "";
		try {
			const url = new URL(window.location.href);
			url.searchParams.delete(TELEPHONY_PUSH_DEEP_LINK_KEY);
			window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
		} catch (_) {}
	}

	maybeOpenDeepLinkedCall(call) {
		if (!this.deepLinkedCallId || !call?.call_id || String(call.call_id) !== this.deepLinkedCallId) return false;
		this.setSoftphoneView("dialer");
		this.toggle(true, { persist: true, focus: false });
		this.clearDeepLinkedCallId();
		return true;
	}

	callNotificationsEnabledOnDevice() {
		return Boolean(
			this.pushToken
			&& typeof Notification !== "undefined"
			&& Notification.permission === "granted"
		);
	}

	renderCallNotificationStatus() {
		if (!this.$callNotificationTitle?.length) return;
		const status = this.callNotificationStatus;
		const permission = typeof Notification === "undefined" ? "unsupported" : Notification.permission;
		let title = __("Checking…");
		let detail = __("Checking this device and the Frappe push relay.");
		let action = __("Enable");
		let tone = "neutral";
		let disabled = this.callNotificationBusy;
		if (this.callNotificationBusy) {
			title = __("Updating call notifications…");
			detail = __("Please wait while this device is updated.");
		} else if (status && (!status.supported || !status.relay_enabled || !status.relay_url_configured)) {
			title = __("Push relay is not ready");
			detail = __("A System Manager must enable and configure Frappe Push Notifications for this site.");
			action = __("Unavailable");
			tone = "warning";
			disabled = true;
		} else if (status && status.browser_supported === false) {
			title = __("Not supported on this device");
			detail = __("This browser cannot register for Frappe push notifications.");
			action = __("Unavailable");
			tone = "warning";
			disabled = true;
		} else if (permission === "denied") {
			title = __("Notifications are blocked");
			detail = __("Allow notifications for this site in your browser or device settings.");
			action = __("Blocked");
			tone = "warning";
			disabled = true;
		} else if (this.callNotificationsEnabledOnDevice()) {
			title = __("Enabled on this device");
			detail = __("Incoming Telephony calls can notify you when Desk is in the background.");
			action = __("Disable");
			tone = "enabled";
		} else if (status) {
			title = __("Not enabled on this device");
			detail = __("Enable push alerts for incoming Telephony calls on this browser or installed web app.");
		}
		this.$callNotificationTitle.text(title);
		this.$callNotificationDetail.text(detail);
		this.$callNotificationToggle.text(action).prop("disabled", disabled);
		this.$callNotificationDot.removeClass("is-enabled is-warning").addClass(tone === "enabled" ? "is-enabled" : tone === "warning" ? "is-warning" : "");
	}

	async refreshCallNotificationStatus({ initializeExisting = false } = {}) {
		try {
			const response = await frappe.call({ method: "telephony.push.get_call_notification_status" });
			const status = response.message || {};
			let browserSupported = false;
			if (status.supported && status.relay_enabled && status.relay_url_configured) {
				browserSupported = Boolean(
					"serviceWorker" in navigator
					&& typeof Notification !== "undefined"
					&& await isSupported()
				);
			}
			this.callNotificationStatus = { ...status, browser_supported: browserSupported };
			this.renderCallNotificationStatus();
			if (!initializeExisting || !this.pushToken) return;
			if (Notification.permission !== "granted" || !browserSupported) {
				if (Notification.permission === "denied") {
					try { await this.unregisterPushToken(this.pushToken); } catch (_) {}
					localStorage.removeItem(TELEPHONY_PUSH_TOKEN_KEY);
					this.pushToken = "";
					this.renderCallNotificationStatus();
				}
				return;
			}
			await this.initializeCallPush({ synchronizeToken: true });
		} catch (error) {
			this.callNotificationStatus = {
				supported: false, relay_enabled: false, relay_url_configured: false,
				browser_supported: false, error: error?.message || String(error),
			};
			this.renderCallNotificationStatus();
		}
	}

	async fetchCallPushBootstrap() {
		const response = await frappe.call({ method: "telephony.push.get_call_notification_bootstrap" });
		const bootstrap = response.message || {};
		if (!bootstrap?.config || !bootstrap?.vapid_public_key) {
			throw new Error(__("The Frappe push relay returned an incomplete configuration."));
		}
		return bootstrap;
	}

	async waitForPushWorker(registration) {
		if (registration.active) return registration;
		const worker = registration.installing || registration.waiting;
		if (!worker) return registration;
		await new Promise((resolve, reject) => {
			const timeout = setTimeout(() => reject(new Error(__("Timed out starting the Telephony notification worker."))), 8000);
			const done = () => {
				if (worker.state !== "activated") return;
				clearTimeout(timeout);
				worker.removeEventListener("statechange", done);
				resolve();
			};
			worker.addEventListener("statechange", done);
			done();
		});
		return registration;
	}

	async initializeCallPush({ synchronizeToken = false } = {}) {
		if (this.pushMessaging && this.pushRegistration) {
			if (synchronizeToken) await this.synchronizeCallPushToken();
			return;
		}
		const bootstrap = await this.fetchCallPushBootstrap();
		const appName = "telephony-push";
		const firebaseApp = getApps().find((app) => app.name === appName) || initializeApp(bootstrap.config, appName);
		this.pushMessaging = getMessaging(firebaseApp);
		const workerAsset = frappe.assets?.bundled_asset?.("telephony_push_worker.bundle.js") || "telephony_push_worker.bundle.js";
		const workerUrl = `${workerAsset}?config=${encodeURIComponent(JSON.stringify(bootstrap.config))}`;
		this.pushRegistration = await this.waitForPushWorker(await navigator.serviceWorker.register(workerUrl, { type: "classic" }));
		this.pushVapidKey = bootstrap.vapid_public_key;
		if (this.pushForegroundUnsubscribe) this.pushForegroundUnsubscribe();
		this.pushForegroundUnsubscribe = onFCMMessage(this.pushMessaging, (payload) => void this.handleForegroundCallPush(payload));
		if (synchronizeToken) await this.synchronizeCallPushToken();
	}

	async registerPushToken(token) {
		const params = new URLSearchParams({ fcm_token: token, project_name: TELEPHONY_PUSH_PROJECT_NAME });
		const response = await fetch(`/api/method/frappe.push_notification.subscribe?${params.toString()}`, {
			method: "GET", credentials: "same-origin", headers: { Accept: "application/json" },
		});
		if (!response.ok) throw new Error(__("Frappe could not register this device for call notifications."));
		return true;
	}

	async unregisterPushToken(token) {
		if (!token) return true;
		const params = new URLSearchParams({ fcm_token: token, project_name: TELEPHONY_PUSH_PROJECT_NAME });
		const response = await fetch(`/api/method/frappe.push_notification.unsubscribe?${params.toString()}`, {
			method: "GET", credentials: "same-origin", headers: { Accept: "application/json" },
		});
		if (!response.ok) throw new Error(__("Frappe could not unregister this device from call notifications."));
		return true;
	}

	async synchronizeCallPushToken() {
		if (!this.pushMessaging || !this.pushRegistration || !this.pushVapidKey || Notification.permission !== "granted") return "";
		const token = await getToken(this.pushMessaging, {
			vapidKey: this.pushVapidKey,
			serviceWorkerRegistration: this.pushRegistration,
		});
		if (!token) throw new Error(__("Firebase did not return a notification token for this device."));
		const previous = this.pushToken || localStorage.getItem(TELEPHONY_PUSH_TOKEN_KEY) || "";
		if (previous && previous !== token) {
			try { await this.unregisterPushToken(previous); } catch (_) {}
		}
		await this.registerPushToken(token);
		this.pushToken = token;
		localStorage.setItem(TELEPHONY_PUSH_TOKEN_KEY, token);
		this.renderCallNotificationStatus();
		return token;
	}

	async enableCallNotifications() {
		await this.refreshCallNotificationStatus();
		const status = this.callNotificationStatus || {};
		if (!status.supported || !status.relay_enabled || !status.relay_url_configured || !status.browser_supported) {
			throw new Error(__("Frappe Push Notifications are not available on this site or device."));
		}
		await this.initializeCallPush();
		const permission = await Notification.requestPermission();
		if (permission !== "granted") throw new Error(__("Notification permission was not granted."));
		await this.synchronizeCallPushToken();
	}

	async disableCallNotifications() {
		const token = this.pushToken || localStorage.getItem(TELEPHONY_PUSH_TOKEN_KEY) || "";
		if (token) await this.unregisterPushToken(token);
		if (this.pushMessaging) {
			try { await deleteToken(this.pushMessaging); } catch (_) {}
		}
		localStorage.removeItem(TELEPHONY_PUSH_TOKEN_KEY);
		this.pushToken = "";
		this.renderCallNotificationStatus();
	}

	async toggleCallNotifications() {
		if (this.callNotificationBusy) return;
		this.callNotificationBusy = true;
		this.renderCallNotificationStatus();
		try {
			if (this.callNotificationsEnabledOnDevice()) {
				await this.disableCallNotifications();
				frappe.show_alert({ message: __("Call notifications disabled on this device."), indicator: "green" });
			} else {
				await this.enableCallNotifications();
				frappe.show_alert({ message: __("Call notifications enabled on this device."), indicator: "green" });
			}
		} catch (error) {
			frappe.show_alert({ message: error?.message || __("Could not update call notifications."), indicator: "red" });
		} finally {
			this.callNotificationBusy = false;
			this.renderCallNotificationStatus();
		}
	}

	async handleForegroundCallPush(payload) {
		const data = payload?.data || {};
		if (data.type !== "telephony_incoming_call") return;
		if (this.currentCall?.call_id && String(this.currentCall.call_id) === String(data.call_id || "")) return;
		if (document.visibilityState === "visible" && document.hasFocus()) return;
		if (!this.pushRegistration || Notification.permission !== "granted") return;
		const options = {
			body: data.body || payload?.notification?.body || "",
			tag: data.call_id ? `telephony-call-${data.call_id}` : "telephony-incoming-call",
			renotify: true,
			requireInteraction: true,
			data: { url: data.click_action || "/app", call_id: data.call_id || "" },
		};
		const icon = telephonyNotificationAssetUrl(data.notification_icon);
		const image = telephonyNotificationAssetUrl(data.notification_image);
		if (icon) options.icon = icon;
		if (image) options.image = image;
		await this.pushRegistration.showNotification(data.title || payload?.notification?.title || __("Incoming Call"), options);
	}

	getOrCreateTabId() {
		try {
			let tabId = sessionStorage.getItem(TELEPHONY_TAB_STORAGE_KEY);
			if (!tabId) {
				tabId = window.crypto?.randomUUID?.()
					|| `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
				sessionStorage.setItem(TELEPHONY_TAB_STORAGE_KEY, tabId);
			}
			return tabId;
		} catch (_) {
			return null;
		}
	}

	renderUserIdentity() {
		const user = frappe.session?.user || frappe.boot?.user?.name || "";
		const info = frappe.user_info?.(user) || frappe.boot?.user_info?.[user] || {};
		const fullname = String(info.fullname || frappe.boot?.user?.full_name || user || __("User"));
		this.$profileName?.text?.(fullname);
		this.$profileAvatar?.empty?.().attr?.("title", fullname);

		const image = String(info.image || "").trim();
		if (image) {
			$("<img>", { src: image, alt: "" }).appendTo(this.$profileAvatar);
		} else {
			const fallback = typeof frappe.get_abbr === "function" ? frappe.get_abbr(fullname) : fullname.slice(0, 2);
			const initials = String(info.abbr || fallback || "?").slice(0, 2).toUpperCase();
			$("<span>", { class: "tp-softphone-profile-initials" }).text(initials).appendTo(this.$profileAvatar);
		}
		this.renderHeaderNumber();
	}

	renderHeaderNumber() {
		const selectedName = String(this.$from?.val?.() || "");
		const selected = this.numbers.find((item) => item.name === selectedName) || this.numbers[0] || null;
		const label = selected ? String(selected.number || selected.name || "").trim() : "";
		this.$profileNumber?.text?.(label);
	}

	renderNumbers() {
		this.$from.empty();
		for (const item of this.numbers) {
			$("<option>").val(item.name).text(item.number || item.name).appendTo(this.$from);
		}
		const stored = localStorage.getItem(TELEPHONY_VOICE_STORAGE_KEY);
		if (stored && this.numbers.some((item) => item.name === stored)) this.$from.val(stored);
		this.$root.find(".telephony-voice-number-choice").toggle(this.numbers.length > 1);
		if (!this.numbers.length) this.$note.text(__("No enabled SIP identity is assigned."));
	}

	setSoftphoneView(view) {
		const target = ["history", "contacts", "settings"].includes(view) ? view : "dialer";
		if (target !== "history" && this.historySelectedItem) this.closeHistoryDetail({ resize: false });
		if (target !== "contacts" && this.contactsSelectedItem) this.closeContactDetail({ resize: false });
		const history = target === "history";
		const contacts = target === "contacts";
		const settings = target === "settings";
		this.view = target;
		this.root?.classList.toggle("tp-softphone-history-mode", history);
		this.root?.classList.toggle("tp-softphone-contacts-mode", contacts);
		this.root?.classList.toggle("tp-softphone-settings-mode", settings);
		this.$historyView?.attr("aria-hidden", history ? "false" : "true");
		this.$contactsView?.attr("aria-hidden", contacts ? "false" : "true");
		this.$settingsView?.attr("aria-hidden", settings ? "false" : "true");
		this.$root.find('[data-softphone-view]').each((_, element) => {
			const $element = $(element);
			const selected = String($element.data("softphone-view") || "") === target;
			$element.toggleClass("active", selected).attr("aria-selected", selected ? "true" : "false");
		});
		this.renderHistoryFilters();
		this.renderHistoryActiveCall();
		if (history && this.historySelectedItem) this.renderHistoryDetail();
		if (contacts && this.contactsSelectedItem) this.renderContactDetail();
		else this.renderContacts();
		this.setPanelSizeFromViewport();
		this.positionPanelAroundToggle();
		if (history) {
			if (!this.historyLoaded || this.historyDirty) void this.loadHistoryPage({ reset: true });
			else setTimeout(() => this.maybeLoadMoreHistory(), 0);
		} else if (contacts) {
			if (!this.contactsLoaded) void this.loadContacts({ reset: true });
			else setTimeout(() => {
				this.maybeLoadMoreContacts();
				this.$contactSearch?.trigger?.("focus");
			}, 0);
		} else if (settings) {
			void this.refreshDevices();
			void this.runPreflight();
			void this.refreshCallNotificationStatus({ initializeExisting: true });
			this.renderSettingsReadiness();
			this.renderDiagnostics();
		} else {
			setTimeout(() => this.$number.trigger("focus"), 0);
		}
	}

	historyRemoteNumber(item) {
		if (!item) return "";
		return String(item.direction || "").toLowerCase() === "incoming"
			? String(item.from_number || "")
			: String(item.to_number || "");
	}

	historyRemoteLabel(item) {
		if (!item) return "";
		if (item.contact_name) return String(item.contact_name);
		if (String(item.direction || "").toLowerCase() === "incoming" && item.caller_name) return String(item.caller_name);
		return this.historyRemoteNumber(item);
	}

	formatHistoryDuration(value) {
		const total = Math.max(0, Math.round(Number(value) || 0));
		const hours = Math.floor(total / 3600);
		const minutes = Math.floor((total % 3600) / 60);
		const seconds = total % 60;
		return hours > 0
			? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
			: `${minutes}:${String(seconds).padStart(2, "0")}`;
	}

	historyGroupLabel(value) {
		const date = String(value || "").slice(0, 10);
		if (!date) return __("Earlier");
		const today = frappe.datetime?.get_today?.() || new Date().toISOString().slice(0, 10);
		const yesterday = frappe.datetime?.add_days?.(today, -1);
		if (date === today) return __("Today");
		if (yesterday && date === yesterday) return __("Yesterday");
		try { return frappe.datetime?.str_to_user?.(date) || date; } catch (_) { return date; }
	}

	historyTimeLabel(value) {
		const raw = String(value || "");
		const match = raw.match(/[T ](\d{2}:\d{2})/);
		return match ? match[1] : "";
	}

	historyDateTimeLabel(value) {
		const raw = String(value || "").trim();
		if (!raw) return "";
		try {
			return frappe.datetime?.str_to_user?.(raw) || raw.replace("T", " ").slice(0, 16);
		} catch (_) {
			return raw.replace("T", " ").slice(0, 16);
		}
	}

	renderHistoryFilters() {
		this.$root?.find?.("[data-history-filter]")?.each?.((_, element) => {
			const $element = $(element);
			const selected = String($element.data("history-filter") || "all") === this.historyFilter;
			$element.toggleClass("active", selected).attr("aria-pressed", selected ? "true" : "false");
		});
	}

	async setHistoryFilter(filter) {
		const target = ["all", "missed", "incoming", "outgoing"].includes(filter) ? filter : "all";
		if (target === this.historyFilter && !this.historySelectedItem) return;
		this.historyFilter = target;
		this.closeHistoryDetail({ resize: false });
		this.renderHistoryFilters();
		await this.loadHistoryPage({ reset: true });
	}

	openHistoryDetail(itemName) {
		const item = this.historyItems.find((row) => String(row?.name || "") === String(itemName || ""));
		if (!item) return false;
		this.historySelectedItem = item;
		this.historySelectedContact = null;
		this.historySelectedContactLoading = false;
		this.historyContactEditorMode = null;
		this.historyContactSearchResults = [];
		this.historyContactSearchText = "";
		this.historyContactSearchLoading = false;
		this.historyContactSearchRequest += 1;
		this.historyContactSaving = false;
		this.$historyView?.addClass?.("tp-softphone-history-detail-open");
		this.$historyDetail?.attr?.("aria-hidden", "false");
		this.renderHistoryDetail();
		if (item.contact) void this.loadHistorySelectedContact(item);
		this.setPanelSizeFromViewport();
		this.positionPanelAroundToggle();
		return true;
	}

	closeHistoryDetail({ resize = true } = {}) {
		this.historySelectedContactRequest += 1;
		this.historyContactSearchRequest += 1;
		clearTimeout(this.historyContactSearchTimer);
		this.historySelectedContact = null;
		this.historySelectedContactLoading = false;
		this.historyContactEditorMode = null;
		this.historyContactSearchResults = [];
		this.historyContactSearchText = "";
		this.historyContactSearchLoading = false;
		this.historyContactSaving = false;
		this.historySelectedItem = null;
		this.$historyView?.removeClass?.("tp-softphone-history-detail-open");
		this.$historyDetail?.attr?.("aria-hidden", "true");
		this.$historyDetailContent?.empty?.();
		if (resize) {
			this.setPanelSizeFromViewport();
			this.positionPanelAroundToggle();
		}
	}

	async loadHistorySelectedContact(item) {
		const contactName = String(item?.contact || "").trim();
		if (!contactName) return;
		const request = ++this.historySelectedContactRequest;
		this.historySelectedContactLoading = true;
		this.historySelectedContact = null;
		this.renderHistoryDetail();
		try {
			const response = await frappe.call({
				method: "telephony.contacts.get_contact_details",
				args: { contact_name: contactName },
			});
			if (request !== this.historySelectedContactRequest || this.historySelectedItem !== item) return;
			this.historySelectedContact = response.message?.item || null;
		} catch (_) {
			if (request !== this.historySelectedContactRequest) return;
			this.historySelectedContact = null;
		} finally {
			if (request === this.historySelectedContactRequest) {
				this.historySelectedContactLoading = false;
				this.renderHistoryDetail();
			}
		}
	}

	renderHistoryDetail() {
		if (!this.$historyDetailContent?.length || !this.historySelectedItem) return;
		const item = this.historySelectedItem;
		const contact = this.historySelectedContact;
		const number = this.historyRemoteNumber(item) || __("Unknown number");
		const label = this.historyRemoteLabel(item) || number;
		const direction = String(item.direction || __("Call"));
		const outcome = String(item.outcome || __("Unknown"));
		const unsuccessful = ["Missed", "Rejected", "Cancelled", "Canceled", "Not Answered", "No Answer", "Busy", "Failed"].includes(outcome);
		const directionIcon = direction.toLowerCase() === "incoming" ? "arrow-down-left" : "arrow-up-right";
		const directionKey = direction.toLowerCase() === "incoming" ? "incoming" : "outgoing";
		const stamp = item.started_at || item.ended_at || item.creation;
		const unique = (values) => [...new Set((values || []).map((value) => String(value || "").trim()).filter(Boolean))];
		const $content = this.$historyDetailContent.empty();

		const $back = $("<button>", { type: "button", class: "tp-softphone-history-detail-back", "data-action": "history-detail-back" });
		$back.html('<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-left"></use></svg><span></span>');
		$back.find("span").text(__("Recents"));
		$back.appendTo($content);

		const $summary = $("<section>", { class: "tp-softphone-history-summary" }).appendTo($content);
		const $hero = $("<div>", { class: `tp-softphone-history-detail-hero tp-softphone-history-profile${unsuccessful ? " is-unsuccessful" : ""}` }).appendTo($summary);
		const $identity = $("<div>", { class: "tp-softphone-history-detail-identity", "aria-hidden": "true" }).appendTo($hero);
		const $avatar = $("<div>", { class: "tp-softphone-contact-avatar tp-softphone-history-detail-avatar" }).appendTo($identity);
		this.renderContactAvatar($avatar, { full_name: item.contact_name || label, image: item.contact_image || "" });
		$("<span>", { class: `tp-softphone-history-detail-direction-badge is-${directionKey}` })
			.html(`<svg class="icon tp-softphone-history-direction-icon"><use href="#icon-${directionIcon}"></use></svg>`)
			.appendTo($identity);
		const $heroCopy = $("<div>", { class: "tp-softphone-history-detail-hero-copy" }).appendTo($hero);
		$("<strong>").text(label).appendTo($heroCopy);
		if (label !== number) $("<span>", { class: "tp-softphone-history-profile-number" }).text(number).appendTo($heroCopy);
		const $badges = $("<div>", { class: "tp-softphone-history-badges" }).appendTo($heroCopy);
		$("<span>", { class: `tp-softphone-history-badge is-${directionKey}` }).text(direction).appendTo($badges);
		$("<span>", { class: `tp-softphone-history-badge${unsuccessful ? " is-unsuccessful" : ""}` }).text(outcome).appendTo($badges);

		if (item.ivr_route) {
			const $ivr = $("<div>", { class: "tp-softphone-history-ivr" }).appendTo($summary);
			$("<strong>").text(item.ivr_route).appendTo($ivr);
			$("<span>").text(__("IVR Route")).appendTo($ivr);
		}

		const talkSeconds = Number(item.duration_seconds || 0);
		const totalSeconds = Number(item.total_duration_seconds ?? talkSeconds);
		const $duration = $("<div>", { class: "tp-softphone-history-duration" }).appendTo($summary);
		$("<strong>").text(this.formatHistoryDuration(talkSeconds)).appendTo($duration);
		$("<span>").text(__("Talk time")).appendTo($duration);
		if (totalSeconds > talkSeconds) {
			$("<small>").text(`${__("Total call time")}: ${this.formatHistoryDuration(totalSeconds)}`).appendTo($duration);
		}
		$("<div>", { class: "tp-softphone-history-detail-date" }).text(this.historyDateTimeLabel(stamp)).appendTo($summary);

		const $contactSection = $("<section>", { class: "tp-softphone-history-section tp-softphone-history-contact-section" }).appendTo($content);
		$("<div>", { class: "tp-softphone-history-section-title" }).text(__("Contact")).appendTo($contactSection);
		if (item.contact && this.historySelectedContactLoading) {
			$("<div>", { class: "tp-softphone-history-section-loading" }).text(__("Loading contact…")).appendTo($contactSection);
		} else if (item.contact && contact) {
			const phones = unique([contact.mobile_no, contact.phone, ...(contact.additional_phones || [])]);
			const emails = unique([contact.email_id, ...(contact.additional_emails || [])]);
			const contactRows = [
				[__("Company"), contact.company_name],
				[__("Department"), contact.department],
				[__("Designation"), contact.designation],
				[__("Address"), contact.address],
			].filter(([, value]) => String(value || "").trim());
			const $fields = $("<div>", { class: "tp-softphone-history-detail-fields" }).appendTo($contactSection);
			for (const [rowLabel, value] of contactRows) {
				const $row = $("<div>", { class: "tp-softphone-history-detail-field" }).appendTo($fields);
				$("<span>").text(rowLabel).appendTo($row);
				$("<strong>").text(value).appendTo($row);
			}
			for (const phone of phones) {
				const $row = $("<div>", { class: "tp-softphone-history-detail-field tp-softphone-history-contact-phone" }).appendTo($fields);
				$("<span>").text(phone === contact.mobile_no ? __("Mobile") : __("Phone")).appendTo($row);
				$("<button>", { type: "button", class: "tp-softphone-history-inline-action" })
					.attr("data-history-contact-number", phone).data("history-contact-number", phone)
					.text(phone).appendTo($row);
			}
			for (const email of emails) {
				const $row = $("<div>", { class: "tp-softphone-history-detail-field" }).appendTo($fields);
				$("<span>").text(__("Email")).appendTo($row);
				$("<a>", { class: "tp-softphone-history-inline-link", href: `mailto:${email}` }).text(email).appendTo($row);
			}
			$("<button>", { type: "button", class: "tp-softphone-history-section-link", "data-action": "history-open-contact" })
				.text(__("View Contact") + " ›").appendTo($contactSection);
		} else if (item.contact) {
			$("<div>", { class: "tp-softphone-history-section-loading" }).text(__("Contact details are not available.")).appendTo($contactSection);
		} else if (this.historyContactEditorMode === "create") {
			this.renderHistoryContactCreateEditor($contactSection, item, number);
		} else if (this.historyContactEditorMode === "attach") {
			this.renderHistoryContactAttachEditor($contactSection, number);
		} else {
			const $empty = $("<div>", { class: "tp-softphone-history-contact-empty" }).appendTo($contactSection);
			$("<strong>").text(__("Not saved as a contact")).appendTo($empty);
			$("<span>").text(number).appendTo($empty);
			const $contactActions = $("<div>", { class: "tp-softphone-history-contact-create-actions" }).appendTo($contactSection);
			$("<button>", { type: "button", class: "tp-softphone-history-contact-create", "data-action": "history-create-contact" })
				.text(__("Create New Contact")).appendTo($contactActions);
			$("<button>", { type: "button", class: "tp-softphone-history-contact-add-existing", "data-action": "history-add-existing-contact" })
				.text(__("Add to Existing Contact")).appendTo($contactActions);
		}

		const $callSection = $("<section>", { class: "tp-softphone-history-section" }).appendTo($content);
		$("<div>", { class: "tp-softphone-history-section-title" }).text(__("Call Details")).appendTo($callSection);
		const callRows = [
			[__("Caller Name"), item.caller_name],
			[__("From"), item.from_number],
			[__("To"), item.to_number],
			[__("Answered by"), item.answered_by],
			[__("Started"), item.started_at ? this.historyDateTimeLabel(item.started_at) : ""],
			[__("Connected"), item.connected_at ? this.historyDateTimeLabel(item.connected_at) : ""],
			[__("Ended"), item.ended_at ? this.historyDateTimeLabel(item.ended_at) : ""],
			[__("Recording"), item.recording ? __("Available") : ""],
			[__("Failure reason"), item.failure_reason],
		].filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "");
		const $callFields = $("<div>", { class: "tp-softphone-history-detail-fields" }).appendTo($callSection);
		for (const [rowLabel, value] of callRows) {
			const $row = $("<div>", { class: "tp-softphone-history-detail-field" }).appendTo($callFields);
			$("<span>").text(rowLabel).appendTo($row);
			$("<strong>").text(value).appendTo($row);
		}
		$("<button>", { type: "button", class: "tp-softphone-history-section-link", "data-action": "history-open-record" })
			.text(__("Open Call Log") + " ›").appendTo($callSection);

		const $actions = $("<div>", { class: "tp-softphone-history-detail-actions tp-softphone-history-detail-primary-actions" }).appendTo($content);
		if (this.historyRemoteNumber(item)) {
			const $call = $("<button>", { type: "button", class: "tp-softphone-history-detail-call", "data-action": "history-call-again" })
				.attr("data-history-number", this.historyRemoteNumber(item))
				.data("history-number", this.historyRemoteNumber(item));
			$call.html('<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-phone-call"></use></svg><span></span>');
			$call.find("span").text(this.isSelectingTransferTarget() ? __("Use this number") : __("Call Back"));
			$call.prop("disabled", this.historyHasActiveCall() && !this.isSelectingTransferTarget()).appendTo($actions);
		}
	}

	historyContactNameParts(item) {
		const number = String(this.historyRemoteNumber(item) || "").trim();
		const raw = String(item?.caller_name || "").trim();
		if (!raw || raw === number || /^[+\d\s().-]+$/.test(raw)) return { first_name: "", last_name: "" };
		const parts = raw.split(/\s+/).filter(Boolean);
		return { first_name: parts.shift() || "", last_name: parts.join(" ") };
	}

	openHistoryContactEditor(mode) {
		if (!this.historySelectedItem || this.historySelectedItem.contact) return false;
		const target = mode === "attach" ? "attach" : "create";
		this.historyContactSearchRequest += 1;
		clearTimeout(this.historyContactSearchTimer);
		this.historyContactEditorMode = target;
		this.historyContactSearchResults = [];
		this.historyContactSearchText = "";
		this.historyContactSearchLoading = target === "attach";
		this.historyContactSaving = false;
		this.renderHistoryDetail();
		if (target === "attach") {
			void this.searchHistoryContacts("");
		} else {
			setTimeout(() => this.$historyDetail.find('[name="first_name"]').trigger("focus"), 0);
		}
		return true;
	}

	closeHistoryContactEditor() {
		this.historyContactSearchRequest += 1;
		clearTimeout(this.historyContactSearchTimer);
		this.historyContactEditorMode = null;
		this.historyContactSearchResults = [];
		this.historyContactSearchText = "";
		this.historyContactSearchLoading = false;
		this.historyContactSaving = false;
		this.renderHistoryDetail();
	}

	renderHistoryContactCreateEditor($section, item, number) {
		const defaults = this.historyContactNameParts(item);
		const $editor = $("<div>", { class: "tp-softphone-history-contact-editor" }).appendTo($section);
		const $header = $("<div>", { class: "tp-softphone-history-contact-editor-header" }).appendTo($editor);
		const $heading = $("<div>").appendTo($header);
		$("<strong>").text(__("Create New Contact")).appendTo($heading);
		$("<span>").text(__("Save this caller without leaving the softphone.")).appendTo($heading);
		$("<button>", { type: "button", "data-action": "history-contact-editor-cancel" }).text(__("Cancel")).appendTo($header);

		const $form = $("<form>", { class: "tp-softphone-history-contact-form", "data-history-contact-create-form": "1" }).appendTo($editor);
		const addField = (name, label, value = "", { required = false, type = "text", readonly = false } = {}) => {
			const $field = $("<label>", { class: "tp-softphone-history-contact-form-field" }).appendTo($form);
			$("<span>").text(label + (required ? " *" : "")).appendTo($field);
			const $input = $("<input>", { type, name, autocomplete: "off" }).val(value || "").appendTo($field);
			if (required) $input.attr("required", "required");
			if (readonly) $input.attr("readonly", "readonly");
			return $input;
		};
		addField("first_name", __("First Name"), defaults.first_name, { required: true });
		addField("last_name", __("Last Name"), defaults.last_name);
		addField("phone", __("Phone"), number, { required: true, readonly: true });
		addField("email", __("Email"), "", { type: "email" });
		addField("company_name", __("Company"));
		addField("department", __("Department"));
		addField("designation", __("Designation"));
		const $actions = $("<div>", { class: "tp-softphone-history-contact-editor-actions" }).appendTo($form);
		$("<button>", { type: "button", class: "tp-softphone-history-contact-editor-secondary", "data-action": "history-contact-editor-cancel" })
			.text(__("Cancel")).appendTo($actions);
		$("<button>", { type: "submit", class: "tp-softphone-history-contact-editor-primary" })
			.prop("disabled", this.historyContactSaving).text(this.historyContactSaving ? __("Saving…") : __("Save Contact")).appendTo($actions);
	}

	renderHistoryContactAttachEditor($section, number) {
		const $editor = $("<div>", { class: "tp-softphone-history-contact-editor" }).appendTo($section);
		const $header = $("<div>", { class: "tp-softphone-history-contact-editor-header" }).appendTo($editor);
		const $heading = $("<div>").appendTo($header);
		$("<strong>").text(__("Add to Existing Contact")).appendTo($heading);
		$("<span>").text(`${__("Add")} ${number} ${__("to a Contact")}`).appendTo($heading);
		$("<button>", { type: "button", "data-action": "history-contact-editor-cancel" }).text(__("Cancel")).appendTo($header);

		const $search = $("<div>", { class: "tp-softphone-history-contact-search" }).appendTo($editor);
		$('<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-search"></use></svg>').appendTo($search);
		$("<input>", { type: "search", placeholder: __("Search contacts"), "data-history-contact-search": "1", autocomplete: "off" })
			.val(this.historyContactSearchText).appendTo($search);
		const $results = $("<div>", { class: "tp-softphone-history-contact-search-results" }).appendTo($editor);
		if (this.historyContactSearchLoading) {
			$("<div>", { class: "tp-softphone-history-contact-search-state" }).text(__("Searching contacts…")).appendTo($results);
		} else if (!this.historyContactSearchResults.length) {
			$("<div>", { class: "tp-softphone-history-contact-search-state" }).text(__("No contacts found.")).appendTo($results);
		} else {
			for (const result of this.historyContactSearchResults) {
				const value = String(result?.value || "").trim();
				if (!value) continue;
				const label = String(result?.label || value).trim();
				const description = String(result?.description || "").trim();
				const $choice = $("<button>", { type: "button", class: "tp-softphone-history-contact-choice" })
					.attr("data-history-contact-choice", value).data("history-contact-choice", value)
					.prop("disabled", this.historyContactSaving).appendTo($results);
				const $avatar = $("<span>", { class: "tp-softphone-history-contact-choice-avatar" }).text(this.partyInitials(label)).appendTo($choice);
				const $copy = $("<span>", { class: "tp-softphone-history-contact-choice-copy" }).appendTo($choice);
				$("<strong>").text(label).appendTo($copy);
				if (description && description !== label) $("<small>").text(description).appendTo($copy);
				$('<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-right"></use></svg>').appendTo($choice);
			}
		}
		$("<button>", { type: "button", class: "tp-softphone-history-contact-editor-secondary tp-softphone-history-contact-editor-cancel", "data-action": "history-contact-editor-cancel" })
			.text(__("Cancel")).appendTo($editor);
	}

	async searchHistoryContacts(query) {
		if (this.historyContactEditorMode !== "attach") return;
		const request = ++this.historyContactSearchRequest;
		this.historyContactSearchText = String(query || "");
		this.historyContactSearchLoading = true;
		this.renderHistoryDetail();
		try {
			const response = await frappe.call({
				method: "frappe.desk.search.search_link",
				args: { doctype: "Contact", txt: this.historyContactSearchText, page_length: 20 },
			});
			if (request !== this.historyContactSearchRequest || this.historyContactEditorMode !== "attach") return;
			this.historyContactSearchResults = Array.isArray(response.message) ? response.message : [];
		} catch (_) {
			if (request !== this.historyContactSearchRequest) return;
			this.historyContactSearchResults = [];
		} finally {
			if (request === this.historyContactSearchRequest && this.historyContactEditorMode === "attach") {
				this.historyContactSearchLoading = false;
				this.renderHistoryDetail();
				setTimeout(() => {
					const $input = this.$historyDetail.find("[data-history-contact-search]");
					$input.trigger("focus");
					const input = $input?.[0];
					if (input?.setSelectionRange) input.setSelectionRange(input.value.length, input.value.length);
				}, 0);
			}
		}
	}

	async saveHistoryContactFromForm($form) {
		const item = this.historySelectedItem;
		const number = this.historyRemoteNumber(item);
		if (!item || !number || this.historyContactSaving) return false;
		const value = (name) => String($form.find(`[name="${name}"]`).val() || "").trim();
		const firstName = value("first_name");
		if (!firstName) {
			frappe.show_alert({ message: __("First name is required."), indicator: "orange" });
			$form.find('[name="first_name"]').trigger("focus");
			return false;
		}
		this.historyContactSaving = true;
		$form.find("button, input").prop("disabled", true);
		$form.find('button[type="submit"]').text(__("Saving…"));
		try {
			const response = await frappe.call({
				method: "telephony.contacts.create_contact_from_call",
				args: {
					first_name: firstName,
					last_name: value("last_name"),
					phone: number,
					email: value("email"),
					company_name: value("company_name"),
					department: value("department"),
					designation: value("designation"),
				},
			});
			if (this.historySelectedItem !== item) return false;
			const contact = response.message?.item || null;
			if (!contact) return false;
			this.applyHistoryContactToItem(item, contact, number);
			frappe.show_alert({ message: `${this.contactDisplayName(contact)} ${__("created")}`, indicator: "green" });
			return true;
		} catch (error) {
			this.historyContactSaving = false;
			$form.find("button, input").prop("disabled", false);
			$form.find('[name="phone"]').prop("disabled", false).attr("readonly", "readonly");
			$form.find('button[type="submit"]').text(__("Save Contact"));
			frappe.show_alert({ message: error?.message || __("Could not create the Contact."), indicator: "red" });
			return false;
		}
	}

	applyHistoryContactToItem(item, contact, number) {
		const contactNameLabel = this.contactDisplayName(contact);
		item.contact = contact.name;
		item.contact_name = contactNameLabel;
		item.contact_image = contact.image || "";
		this.historySelectedContact = contact;
		this.historySelectedContactLoading = false;
		this.historyContactEditorMode = null;
		this.historyContactSearchResults = [];
		this.historyContactSearchText = "";
		this.historyContactSearchLoading = false;
		this.historyContactSearchRequest += 1;
		this.historyContactSaving = false;
		this.historyDirty = true;
		this.contactResolutionCache.set(number, {
			contact: contact.name,
			contact_name: contactNameLabel,
			company_name: contact.company_name || "",
			image: contact.image || "",
			matched_number: number,
		});
		this.rerenderHistoryListPreservingScroll();
		this.renderHistoryDetail();
		return contactNameLabel;
	}

	async addHistoryNumberToContact(contactName) {
		const item = this.historySelectedItem;
		const number = this.historyRemoteNumber(item);
		const target = String(contactName || "").trim();
		if (!item || !number || !target || this.historyContactSaving) return false;
		this.historyContactSaving = true;
		this.renderHistoryDetail();
		try {
			const response = await frappe.call({
				method: "telephony.contacts.add_number_to_contact",
				args: { contact_name: target, phone: number },
			});
			if (this.historySelectedItem !== item) return false;
			const contact = response.message?.item || null;
			if (!contact) return false;
			const contactNameLabel = this.applyHistoryContactToItem(item, contact, number);
			frappe.show_alert({
				message: response.message?.added
					? `${number} ${__("added to")} ${contactNameLabel}`
					: `${number} ${__("is already on")} ${contactNameLabel}`,
				indicator: "green",
			});
			return true;
		} catch (error) {
			this.historyContactSaving = false;
			this.renderHistoryDetail();
			frappe.show_alert({ message: error?.message || __("Could not add the number to the Contact."), indicator: "red" });
			return false;
		}
	}

	rerenderHistoryListPreservingScroll() {
		if (!this.$historyList?.length) return;
		const scrollTop = Number(this.$historyList.scrollTop() || 0);
		this.historyLastGroup = null;
		this.$historyList.empty();
		this.appendHistoryRows(this.historyItems);
		this.$historyList.scrollTop(scrollTop);
	}

	async callFromHistory(number) {
		const target = String(number || "").trim();
		if (!target) return false;
		if (this.isSelectingTransferTarget()) return this.prefillFromHistory(target);
		if (this.historyHasActiveCall()) {
			frappe.show_alert({ message: __("End the active call before starting another call."), indicator: "orange" });
			return false;
		}
		if (!this.prefill(target)) return false;
		await this.dial();
		return true;
	}

	historyHasActiveCall() {
		return Boolean(this.currentCall && !["ended", "failed"].includes(this.currentCall.state));
	}

	isSelectingTransferTarget() {
		if (this.blindTransfer) {
			return !this.blindTransfer.waitingForHold && !this.transferPending;
		}
		return Boolean(
			this.attendedTransfer?.state === "preparing"
			&& !this.attendedTransfer.waitingForHold
			&& !this.attendedTransfer.pendingDial
			&& !this.transferPending
		);
	}

	callDisplayLabel(call, fallback = null) {
		if (!call) return fallback || __("Unknown caller");
		return call.contact_name || call.party || call.caller_name || call.number || fallback || __("Unknown caller");
	}

	renderHistoryActiveCall() {
		if (!this.$historyActive?.length) return;
		const active = this.historyHasActiveCall();
		this.$historyActive.toggle(active);
		this.$historyList?.find?.("[data-history-number]")?.prop?.("disabled", active && !this.isSelectingTransferTarget());
		if (!active) {
			this.$historyActiveParty.text("");
			this.$historyActiveTimer.text("");
			return;
		}
		this.$historyActiveParty.text(this.callDisplayLabel(this.currentCall, __("Active call")));
		if (this.currentCall.connectedAt) {
			this.$historyActiveTimer.text(this.formatHistoryDuration((Date.now() - this.currentCall.connectedAt) / 1000));
		} else {
			this.$historyActiveTimer.text(voiceStateLabels[this.currentCall.state] || this.currentCall.state || "");
		}
	}

	appendHistoryRows(items) {
		for (const item of items || []) {
			const stamp = item.started_at || item.ended_at || item.creation;
			const group = this.historyGroupLabel(stamp);
			if (group !== this.historyLastGroup) {
				$("<div>", { class: "tp-softphone-history-group" }).text(group).appendTo(this.$historyList);
				this.historyLastGroup = group;
			}
			const direction = String(item.direction || "");
			const directionKey = direction.toLowerCase() === "incoming" ? "incoming" : "outgoing";
			const outcome = String(item.outcome || "");
			const number = this.historyRemoteNumber(item);
			const label = this.historyRemoteLabel(item) || number || __("Unknown number");
			const unsuccessful = ["Missed", "Rejected", "Cancelled", "Canceled", "Not Answered", "No Answer", "Busy", "Failed"].includes(outcome);
			const classes = ["tp-softphone-history-row", `is-${directionKey}`, unsuccessful ? "is-unsuccessful" : ""].filter(Boolean).join(" ");
			const $row = $("<div>", {
				class: classes, role: "button", tabindex: 0,
				"data-history-id": item.name || "",
				"aria-label": `${label} · ${direction || __("Call")} · ${outcome || __("Unknown")}`,
			});
			$row.data("history-id", item.name || "");
			const directionIcon = directionKey === "incoming" ? "arrow-down-left" : "arrow-up-right";
			if (item.contact_name || item.contact_image || item.contact) {
				const $identity = $("<div>", { class: "tp-softphone-history-identity", "aria-hidden": "true" }).appendTo($row);
				const $avatar = $("<div>", { class: "tp-softphone-contact-avatar tp-softphone-history-avatar" }).appendTo($identity);
				this.renderContactAvatar($avatar, { full_name: item.contact_name || label, image: item.contact_image || "" });
				$("<span>", { class: `tp-softphone-history-direction-badge is-${directionKey}` })
					.html(`<svg class="icon tp-softphone-history-direction-icon"><use href="#icon-${directionIcon}"></use></svg>`)
					.appendTo($identity);
			} else {
				$("<div>", { class: "tp-softphone-history-direction", "aria-hidden": "true" })
					.html(`<svg class="icon icon-sm tp-softphone-history-direction-icon"><use href="#icon-${directionIcon}"></use></svg>`)
					.appendTo($row);
			}
			const $copy = $("<div>", { class: "tp-softphone-history-copy" }).appendTo($row);
			$("<div>", { class: "tp-softphone-history-number" }).text(label).appendTo($copy);
			$("<div>", { class: "tp-softphone-history-meta" }).text([label !== number ? number : "", direction || __("Call")].filter(Boolean).join(" · ")).appendTo($copy);
			const $timing = $("<div>", { class: "tp-softphone-history-timing" }).appendTo($row);
			$("<div>", { class: "tp-softphone-history-time" }).text(this.historyTimeLabel(stamp)).appendTo($timing);
			const result = outcome === "Completed" ? this.formatHistoryDuration(item.duration_seconds) : (outcome || __("Unknown"));
			$("<div>", { class: `tp-softphone-history-result${unsuccessful ? " is-missed" : ""}` }).text(result).appendTo($timing);
			if (number) {
				$("<button>", {
					type: "button", class: "tp-softphone-history-call", title: __("Call back"), "aria-label": __("Call back"),
				})
					.attr("data-history-number", number)
					.data("history-number", number)
					.prop("disabled", this.historyHasActiveCall() && !this.isSelectingTransferTarget())
					.html('<svg class="icon icon-sm tp-softphone-history-call-icon" aria-hidden="true"><use href="#icon-phone-call"></use></svg>')
					.appendTo($row);
			} else {
				$("<span>", { class: "tp-softphone-history-call-placeholder" }).appendTo($row);
			}
			$row.appendTo(this.$historyList);
		}
	}

	renderHistoryState() {
		if (!this.$historyState?.length) return;
		let message = "";
		if (this.historyLoading) message = this.historyItems.length ? __("Loading more…") : __("Loading call history…");
		else if (this.historyError) message = this.historyError;
		else if (this.historyLoaded && !this.historyItems.length) {
			message = this.historyFilter === "missed" ? __("No missed calls.")
				: this.historyFilter === "incoming" ? __("No incoming calls.")
				: this.historyFilter === "outgoing" ? __("No outgoing calls.")
				: __("No calls yet.");
		}
		this.$historyState.text(message).toggle(Boolean(message));
	}

	async loadHistoryPage({ reset = false } = {}) {
		if (this.historyLoading && !reset) return;
		if (!reset && !this.historyHasMore) return;
		if (reset) {
			this.historyRequestGeneration += 1;
			this.historyItems = [];
			this.historyCursor = null;
			this.historyHasMore = true;
			this.historyLoaded = false;
			this.historyError = null;
			this.historyLastGroup = null;
			this.$historyList.empty();
		}
		const generation = this.historyRequestGeneration;
		this.historyLoading = true;
		this.renderHistoryState();
		try {
			const args = {};
			if (this.historyCursor) args.cursor = this.historyCursor;
			if (this.historyFilter && this.historyFilter !== "all") args.history_filter = this.historyFilter;
			const response = await frappe.call({
				method: "telephony.call_history.get_my_call_history",
				args,
			});
			if (generation !== this.historyRequestGeneration) return;
			const page = response.message || {};
			const items = Array.isArray(page.items) ? page.items : [];
			this.historyItems.push(...items);
			this.appendHistoryRows(items);
			this.historyCursor = page.next_cursor || null;
			this.historyHasMore = Boolean(page.has_more && this.historyCursor);
			this.historyLoaded = true;
			this.historyDirty = false;
			this.historyError = null;
		} catch (error) {
			if (generation === this.historyRequestGeneration) {
				this.historyError = error?.message || __("Could not load call history.");
			}
		} finally {
			if (generation === this.historyRequestGeneration) {
				this.historyLoading = false;
				this.renderHistoryState();
				setTimeout(() => this.maybeLoadMoreHistory(), 0);
			}
		}
	}

	maybeLoadMoreHistory() {
		if (this.view !== "history" || this.historySelectedItem || this.historyLoading || !this.historyHasMore) return;
		const list = this.$historyList?.[0];
		if (!list) return;
		const remaining = Math.max(0, list.scrollHeight - list.scrollTop - list.clientHeight);
		if (remaining <= TELEPHONY_HISTORY_PREFETCH_PX) void this.loadHistoryPage();
	}

	prefillFromHistory(number) {
		if (!number) return false;
		if (this.isSelectingTransferTarget()) {
			this.$number.val(String(number).trim()).trigger("input");
			this.setSoftphoneView("dialer");
			this.ensureKeypadVisible();
			this.$note.text(this.blindTransfer
				? __("Transfer target selected. Press Transfer to send the call.")
				: __("Transfer target selected. Press Call Target to start the consultation call."));
			return true;
		}
		if (this.historyHasActiveCall()) {
			frappe.show_alert({ message: __("End the active call before starting another call."), indicator: "orange" });
			return false;
		}
		return this.prefill(number);
	}

	async resolveContactNumber(number) {
		const value = String(number || "").trim();
		if (!value) return null;
		if (this.contactResolutionCache.has(value)) return this.contactResolutionCache.get(value);
		if (this.contactResolutionPending.has(value)) return this.contactResolutionPending.get(value);
		const pending = (async () => {
			try {
				const response = await frappe.call({
					method: "telephony.contacts.resolve_numbers",
					args: { numbers: [value] },
				});
				const match = response.message?.matches?.[value] || null;
				this.contactResolutionCache.set(value, match);
				return match;
			} finally {
				this.contactResolutionPending.delete(value);
			}
		})();
		this.contactResolutionPending.set(value, pending);
		return pending;
	}

	async resolveCallContact(call, { notify = false } = {}) {
		if (!call?.call_id) return null;
		try {
			const match = call.number ? await this.resolveContactNumber(call.number) : null;
			if (match?.contact_name) {
				call.contact = match.contact || "";
				call.contact_name = match.contact_name;
				call.contact_image = match.image || "";
				call.party = match.contact_name;
				if ([this.currentCall, this.waitingCall, this.parkedCall].includes(call)) this.refresh();
			}
		} catch (_) {
			// Caller-ID resolution is best effort; SIP identity remains the fallback.
		}
		if (notify && this.currentCall === call && call.state === "ringing" && window.Notification?.permission === "granted") {
			new Notification(__("Incoming Telephony call"), { body: call.party || call.number || __("Unknown caller") });
		}
		return call.contact_name || null;
	}

	contactDisplayName(item) {
		return String(item?.full_name || [item?.first_name, item?.last_name].filter(Boolean).join(" ") || item?.company_name || __("Contact"));
	}

	contactInitials(item) {
		const name = this.contactDisplayName(item).trim();
		const parts = name.split(/\s+/).filter(Boolean);
		return (parts.length > 1 ? `${parts[0][0]}${parts.at(-1)[0]}` : name.slice(0, 2)).toUpperCase() || "?";
	}

	partyInitials(value) {
		const text = String(value || "").trim();
		if (!text) return "T";
		const parts = text.split(/\s+/).filter(Boolean);
		if (/^[+\d*#(). -]+$/.test(text)) return "☎";
		return (parts.length > 1 ? `${parts[0][0]}${parts.at(-1)[0]}` : text.slice(0, 2)).toUpperCase();
	}

	attendedTransferCalls() {
		const transfer = this.attendedTransfer;
		if (!transfer) return { transfer: null, original: null, consult: null };
		const calls = [this.currentCall, this.parkedCall].filter(Boolean);
		return {
			transfer,
			original: calls.find((call) => call.call_id === transfer.originalCallId) || null,
			consult: transfer.consultCallId
				? calls.find((call) => call.call_id === transfer.consultCallId) || null
				: null,
		};
	}

	callElapsedLabel(call) {
		if (!call?.connectedAt) return "00:00";
		const seconds = Math.max(0, Math.floor((Date.now() - call.connectedAt) / 1000));
		const hours = Math.floor(seconds / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		const secs = seconds % 60;
		return hours > 0
			? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
			: `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
	}

	attendedLegStateLabel(call) {
		if (!call) return __("Waiting");
		return ({
			new: __("Starting"),
			dialing: __("Calling"),
			ringing: __("Ringing"),
			connecting: __("Connecting"),
			connected: __("Connected"),
			held: __("On Hold"),
			disconnecting: __("Ending"),
			ended: __("Ended"),
			failed: __("Failed"),
		})[call.state] || call.state || __("Waiting");
	}

	updateAttendedTransferTimers() {
		if (this.blindTransfer) {
			const original = this.currentCall?.call_id === this.blindTransfer.callId ? this.currentCall : null;
			this.$attendedOriginalTime?.text?.(this.callElapsedLabel(original));
			this.$attendedConsultTime?.text?.("");
			return;
		}
		if (!this.attendedTransfer) return;
		const { original, consult } = this.attendedTransferCalls();
		this.$attendedOriginalTime?.text?.(this.callElapsedLabel(original));
		this.$attendedConsultTime?.text?.(this.callElapsedLabel(consult));
	}

	renderBlindTransferSummary() {
		for (const name of ["tp-softphone-blind-preparing", "tp-softphone-blind-sending"]) {
			this.root?.classList?.remove?.(name);
		}
		const transfer = this.blindTransfer;
		if (!transfer) {
			this.$attendedSummary?.attr?.("aria-hidden", "true");
			return false;
		}
		const original = this.currentCall?.call_id === transfer.callId ? this.currentCall : null;
		const originalParty = original?.party || original?.number || __("Original caller");
		const sending = Boolean(this.transferPending);
		this.root?.classList?.add?.(sending ? "tp-softphone-blind-sending" : "tp-softphone-blind-preparing");
		this.$attendedSummary?.attr?.("aria-hidden", "false");
		this.$attendedStageTitle?.text?.(sending ? __("Transferring Call") : __("Blind Transfer"));
		this.$attendedStageDetail?.text?.(transfer.waitingForHold
			? __("Putting the original call on hold…")
			: sending
				? `${__("Sending the call to")} ${transfer.target || __("the transfer target")}…`
				: __("Original call is on hold. Enter the transfer target."));
		this.$attendedOriginalParty?.text?.(originalParty);
		this.$attendedOriginalState?.text?.(this.attendedLegStateLabel(original));
		this.$attendedConsultParty?.text?.("");
		this.$attendedConsultState?.text?.("");
		this.updateAttendedTransferTimers();
		return true;
	}

	renderTransferSummary(canCompleteAttended = false) {
		if (this.blindTransfer) {
			this.renderBlindTransferSummary();
			return;
		}
		this.renderBlindTransferSummary();
		this.renderAttendedTransferSummary(canCompleteAttended);
	}

	renderAttendedTransferSummary(canCompleteAttended = false) {
		const { transfer, original, consult } = this.attendedTransferCalls();
		const active = Boolean(transfer);
		this.$attendedSummary?.attr?.("aria-hidden", active ? "false" : "true");
		for (const name of [
			"tp-softphone-attended-preparing",
			"tp-softphone-attended-consulting",
			"tp-softphone-attended-ready",
			"tp-softphone-attended-has-consult",
			"tp-softphone-attended-original-connected",
			"tp-softphone-attended-original-held",
			"tp-softphone-attended-consult-connected",
			"tp-softphone-attended-consult-held",
		]) this.root?.classList?.remove?.(name);
		if (!active) return;

		const originalParty = original?.party || original?.number || __("Original caller");
		const consultParty = consult?.party || consult?.number || transfer.target || __("Transfer target");
		let title = __("Attended Transfer");
		let detail = transfer.waitingForHold
			? __("Putting the original call on hold…")
			: transfer.pendingDial
				? __("Starting the consultation call. The original call remains on hold.")
				: __("Original call is on hold. Enter the transfer target.");
		let stageClass = "tp-softphone-attended-preparing";

		if (transfer.pendingDial && transfer.state === "preparing") title = __("Calling Transfer Target");
		if (transfer.state === "consulting") {
			stageClass = canCompleteAttended ? "tp-softphone-attended-ready" : "tp-softphone-attended-consulting";
			if (canCompleteAttended) {
				title = __("Ready to Complete Transfer");
				detail = consult?.state === "connected"
					? __("You are speaking with the transfer target.")
					: original?.state === "connected"
						? __("The consult call is on hold. You can swap back or complete the transfer.")
						: __("Both call legs are available. Complete or cancel the transfer.");
			} else if (["dialing", "ringing", "connecting"].includes(consult?.state)) {
				title = __("Calling Transfer Target");
				detail = `${__("Calling")} ${consultParty}. ${__("The original call remains on hold.")}`;
			} else {
				title = __("Consult Call Active");
				detail = __("Speak with the transfer target, then complete or cancel the transfer.");
			}
		}

		this.root?.classList?.add?.(stageClass);
		if (original?.state === "connected") this.root?.classList?.add?.("tp-softphone-attended-original-connected");
		if (original?.state === "held") this.root?.classList?.add?.("tp-softphone-attended-original-held");
		if (consult?.state === "connected") this.root?.classList?.add?.("tp-softphone-attended-consult-connected");
		if (consult?.state === "held") this.root?.classList?.add?.("tp-softphone-attended-consult-held");
		if (consult || transfer.consultCallId || transfer.pendingDial) this.root?.classList?.add?.("tp-softphone-attended-has-consult");
		this.$attendedStageTitle?.text?.(title);
		this.$attendedStageDetail?.text?.(detail);
		this.$attendedOriginalParty?.text?.(originalParty);
		this.$attendedOriginalState?.text?.(this.attendedLegStateLabel(original));
		this.$attendedConsultParty?.text?.(consultParty);
		this.$attendedConsultState?.text?.(this.attendedLegStateLabel(consult));
		this.updateAttendedTransferTimers();
	}


	renderContactAvatar($avatar, item) {
		if (!$avatar?.length) return;
		const initials = this.contactInitials(item);
		const image = String(item?.image || "").trim();
		$avatar.empty();
		if (!image) {
			$avatar.text(initials);
			return;
		}
		const $image = $("<img>", { src: image, alt: "", loading: "lazy" });
		$image.on("error", () => {
			$image.remove();
			$avatar.text(initials);
		});
		$image.appendTo($avatar);
	}

	openContactDetail(itemName) {
		const item = this.contactsItems.find((row) => String(row?.name || "") === String(itemName || ""));
		if (!item) return false;
		this.contactsSelectedItem = item;
		this.$contactsView?.addClass?.("tp-softphone-contact-detail-open");
		this.$contactDetail?.attr?.("aria-hidden", "false");
		this.renderContactDetail();
		this.setPanelSizeFromViewport();
		this.positionPanelAroundToggle();
		return true;
	}

	closeContactDetail({ resize = true } = {}) {
		this.contactsSelectedItem = null;
		this.$contactsView?.removeClass?.("tp-softphone-contact-detail-open");
		this.$contactDetail?.attr?.("aria-hidden", "true");
		this.$contactDetailContent?.empty?.();
		if (resize) {
			this.setPanelSizeFromViewport();
			this.positionPanelAroundToggle();
		}
	}

	renderContactDetail() {
		if (!this.$contactDetailContent?.length || !this.contactsSelectedItem) return;
		const item = this.contactsSelectedItem;
		const name = this.contactDisplayName(item);
		const unique = (values) => [...new Set((values || []).map((value) => String(value || "").trim()).filter(Boolean))];
		const phones = unique([item.mobile_no, item.phone, ...(item.additional_phones || [])]);
		const emails = unique([item.email_id, ...(item.additional_emails || [])]);
		const links = Array.isArray(item.links) ? item.links : [];
		const $content = this.$contactDetailContent.empty();

		const $back = $("<button>", { type: "button", class: "tp-softphone-history-detail-back", "data-action": "contact-detail-back" });
		$back.html('<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-left"></use></svg><span></span>');
		$back.find("span").text(__("Contacts"));
		$back.appendTo($content);

		const $summaryCard = $("<section>", { class: "tp-softphone-contact-summary-card" }).appendTo($content);
		const $hero = $("<div>", { class: "tp-softphone-contact-detail-hero" }).appendTo($summaryCard);
		const $avatar = $("<div>", { class: "tp-softphone-contact-avatar tp-softphone-contact-detail-avatar" }).appendTo($hero);
		this.renderContactAvatar($avatar, item);
		const $heroCopy = $("<div>", { class: "tp-softphone-contact-detail-hero-copy" }).appendTo($hero);
		$("<strong>").text(name).appendTo($heroCopy);
		const summary = [item.company_name, item.designation, item.department]
			.map((value) => String(value || "").trim()).filter((value, index, values) => value && values.indexOf(value) === index).join(" · ");
		if (summary) $("<span>").text(summary).appendTo($heroCopy);
		if (String(item.status || "").trim()) {
			$("<span>", { class: "tp-softphone-contact-status-badge" }).text(item.status).appendTo($heroCopy);
		}

		const fields = [];
		for (const [label, value] of [
			[__("Company"), item.company_name],
			[__("Department"), item.department],
			[__("Designation"), item.designation],
			[__("Address"), item.address],
			[__("Status"), item.status],
			[__("User"), item.user],
		]) if (String(value || "").trim()) fields.push([label, value]);
		for (const link of links) {
			const title = String(link?.link_title || link?.link_name || "").trim();
			if (title) fields.push([String(link?.link_doctype || __("Linked record")), title]);
		}
		if (emails.length || fields.length) {
			const $detailsCard = $("<section>", { class: "tp-softphone-contact-card tp-softphone-contact-details-card" }).appendTo($content);
			$("<div>", { class: "tp-softphone-contact-card-title" }).text(__("Contact Details")).appendTo($detailsCard);
			const $fields = $("<div>", { class: "tp-softphone-history-detail-fields" }).appendTo($detailsCard);
			for (const email of emails) {
				const $row = $("<div>", { class: "tp-softphone-history-detail-field tp-softphone-contact-email-row" }).appendTo($fields);
				$("<span>").text(__("Email")).appendTo($row);
				$("<a>", { class: "tp-softphone-contact-detail-link", href: `mailto:${email}` }).text(email).appendTo($row);
			}
			for (const [label, value] of fields) {
				const $row = $("<div>", { class: "tp-softphone-history-detail-field" }).appendTo($fields);
				$("<span>").text(label).appendTo($row);
				$("<strong>").text(value).appendTo($row);
			}
		}

		if (phones.length) {
			const $phoneCard = $("<section>", { class: "tp-softphone-contact-card tp-softphone-contact-phone-card" }).appendTo($content);
			$("<div>", { class: "tp-softphone-contact-card-title" }).text(__("Phone Numbers")).appendTo($phoneCard);
			const $actions = $("<div>", { class: "tp-softphone-history-detail-actions tp-softphone-contact-phone-actions" }).appendTo($phoneCard);
			for (const phone of phones) {
				const $call = $("<button>", { type: "button", class: "tp-softphone-history-detail-call" })
					.attr("data-contact-detail-number", phone).data("contact-detail-number", phone);
				$call.html('<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-phone-call"></use></svg><span></span>');
				$call.find("span").text(this.isSelectingTransferTarget() ? `${__("Use")} ${phone}` : `${__("Call")} ${phone}`);
				$call.prop("disabled", this.historyHasActiveCall() && !this.isSelectingTransferTarget()).appendTo($actions);
			}
		}

		const $recordCard = $("<div>", { class: "tp-softphone-contact-record-card" }).appendTo($content);
		$("<button>", { type: "button", class: "tp-softphone-history-detail-record", "data-action": "contact-open-record" })
			.text(__("Open full contact record") + " ›").appendTo($recordCard);
	}

	renderContacts() {
		if (!this.$contactsList?.length) return;
		this.$contactsList.empty();
		this.appendContactRows(this.contactsItems);
		this.renderContactsState();
	}

	appendContactRows(items) {
		for (const item of items || []) {
			const number = String(item.number || item.mobile_no || item.phone || "").trim();
			if (!number) continue;
			const name = this.contactDisplayName(item);
			const $row = $("<div>", { class: "tp-softphone-contact-row" });
			const $open = $("<button>", {
				type: "button", class: "tp-softphone-contact-open", title: __("View contact"), "aria-label": `${__("View contact")} ${name}`,
			}).attr("data-contact-id", String(item.name || "")).data("contact-id", String(item.name || "")).appendTo($row);
			const $avatar = $("<div>", { class: "tp-softphone-contact-avatar" }).appendTo($open);
			this.renderContactAvatar($avatar, item);
			const $copy = $("<div>", { class: "tp-softphone-contact-copy" }).appendTo($open);
			$("<div>", { class: "tp-softphone-contact-name" }).text(name).appendTo($copy);
			const hintLabel = String(item.match_hint?.label || "").trim();
			const hintValue = String(item.match_hint?.value || "").trim();
			const hint = hintValue ? `${hintLabel ? `${__(hintLabel)}: ` : ""}${hintValue}` : "";
			const subtitle = [number, item.company_name && item.company_name !== name ? item.company_name : "", hint]
				.filter((value, index, values) => value && values.indexOf(value) === index).join(" · ");
			$("<div>", { class: "tp-softphone-contact-number" }).text(subtitle).appendTo($copy);
			$("<button>", { type: "button", class: "tp-softphone-contact-call", title: __("Call"), "aria-label": `${__("Call")} ${name}` })
				.attr("data-contact-number", number).data("contact-number", number)
				.prop("disabled", this.historyHasActiveCall() && !this.isSelectingTransferTarget())
				.html('<svg class="icon icon-sm tp-softphone-contact-call-icon" aria-hidden="true"><use href="#icon-phone-call"></use></svg>')
				.appendTo($row);
			$row.appendTo(this.$contactsList);
		}
	}

	renderContactsState() {
		if (!this.$contactsState?.length) return;
		if (this.contactsLoading) {
			this.$contactsState.text(this.contactsItems.length ? __("Loading more…") : __("Loading contacts…")).show();
			return;
		}
		if (this.contactsError) {
			this.$contactsState.text(this.contactsError).show();
			return;
		}
		if (this.contactsLoaded && !this.contactsItems.length) {
			this.$contactsState.text(__("No matching contacts.")).show();
			return;
		}
		this.$contactsState.text("").hide();
	}

	async loadContacts({ reset = false } = {}) {
		if (this.contactsLoading && !reset) return;
		if (!reset && !this.contactsHasMore) return;
		if (reset) {
			this.contactsRequestGeneration += 1;
			this.contactsItems = [];
			this.contactsCursor = null;
			this.contactsHasMore = true;
			this.contactsLoaded = false;
			this.contactsError = null;
			this.$contactsList.empty();
		}
		const generation = this.contactsRequestGeneration;
		this.contactsLoading = true;
		this.contactsError = null;
		this.renderContactsState();
		try {
			const search = String(this.$contactSearch?.val?.() || "").trim();
			const args = search ? { search } : {};
			if (this.contactsCursor) args.cursor = this.contactsCursor;
			const response = await frappe.call({
				method: "telephony.contacts.get_contacts",
				args,
			});
			if (generation !== this.contactsRequestGeneration) return;
			const result = response.message || {};
			const items = Array.isArray(result.items) ? result.items : [];
			const existing = new Set(this.contactsItems.map((item) => String(item?.name || "")));
			const freshItems = items.filter((item) => !existing.has(String(item?.name || "")));
			this.contactsItems.push(...freshItems);
			this.appendContactRows(freshItems);
			this.contactsCursor = result.next_cursor || null;
			this.contactsHasMore = Boolean(result.has_more && this.contactsCursor);
			this.contactsLoaded = true;
			this.contactsError = null;
		} catch (error) {
			if (generation !== this.contactsRequestGeneration) return;
			this.contactsLoaded = true;
			this.contactsError = error?.message || __("Could not load contacts.");
		} finally {
			if (generation === this.contactsRequestGeneration) {
				this.contactsLoading = false;
				this.renderContactsState();
				setTimeout(() => this.maybeLoadMoreContacts(), 0);
			}
		}
	}

	maybeLoadMoreContacts() {
		if (this.view !== "contacts" || this.contactsSelectedItem || this.contactsLoading || !this.contactsHasMore) return;
		const list = this.$contactsList?.[0];
		if (!list) return;
		const remaining = Math.max(0, list.scrollHeight - list.scrollTop - list.clientHeight);
		if (remaining <= TELEPHONY_CONTACTS_PREFETCH_PX) void this.loadContacts();
	}

	toggle(forceOpen = null, { persist = true, focus = true } = {}) {
		const opening = forceOpen === null ? !this.$panel.hasClass("tp-open") : Boolean(forceOpen);
		if (!opening) {
			this.panelBodyPosition = null;
			this.panelDockSide = null;
		}
		this.$panel.toggleClass("tp-open", opening).attr("aria-hidden", opening ? "false" : "true");
		this.$toggleMain.attr("aria-expanded", opening ? "true" : "false");
		if (persist) {
			this.layoutPrefs.panelOpen = opening;
			this.saveLayoutPrefs();
		}
		this.updateToggleLayoutForCall();
		this.setPanelSizeFromViewport();
		this.positionPanelAroundToggle();
		if (!opening) this.clampToViewport();
		if (opening && focus && this.view === "dialer") setTimeout(() => this.$number.trigger("focus"), 0);
	}

	setStatus(state, note = "") {
		const label = voiceStateLabels[state] || state || __("Ready");
		this.$status.text(label);
		if (note) this.$note.text(note);
	}

	refresh() {
		const call = this.currentCall;
		const state = call?.state || "idle";
		const terminal = state === "ended" || state === "failed";
		const active = Boolean(call && !terminal);
		const incomingRinging = call?.direction === "incoming" && state === "ringing";
		const inCall = state === "connected" || state === "held";
		const handsetStatus = call?.handset?.status || (active ? "available" : "none");
		const handsetOwner = handsetStatus === "owner";
		const handsetOrphaned = active && !incomingRinging && handsetStatus === "orphaned";
		const mediaResumeRequired = active && handsetOwner && state === "connected" && this.mediaResumeRequired && !this.mediaRunning;
		const handsetNeedsResume = handsetOrphaned || mediaResumeRequired;
		const handsetObserver = active && handsetStatus === "observer";
		const blindActive = Boolean(this.blindTransfer);
		const attendedState = this.attendedTransfer?.state || "idle";
		const attendedActive = attendedState !== "idle";
		const attendedPreparing = attendedState === "preparing";
		const attendedConsulting = attendedState === "consulting";
		const attendedCalls = [this.currentCall, this.parkedCall].filter(Boolean);
		const attendedOriginal = attendedCalls.find((item) => item.call_id === this.attendedTransfer?.originalCallId) || null;
		const attendedConsult = attendedCalls.find((item) => item.call_id === this.attendedTransfer?.consultCallId) || null;
		const canCompleteAttended = Boolean(
			attendedConsulting
			&& attendedOriginal
			&& attendedConsult
			&& ["connected", "held"].includes(attendedOriginal.state)
			&& ["connected", "held"].includes(attendedConsult.state)
			&& attendedOriginal.handset?.status === "owner"
			&& attendedConsult.handset?.status === "owner"
		);
		const canSwapCalls = Boolean(
			this.currentCall?.state === "connected"
			&& handsetOwner
			&& this.parkedCall?.state === "held"
			&& this.parkedCall?.handset?.status === "owner"
		);
		const showCall = (!active && !attendedActive) || attendedPreparing;
		const setVisible = (selector, visible) => this.$root.find(selector).css("display", visible ? "" : "none");

		setVisible('[data-action="call"]', showCall);
		setVisible('[data-action="answer"]', incomingRinging && !handsetObserver);
		setVisible('[data-action="resume-handset"]', handsetNeedsResume);
		setVisible('[data-action="end"]', (incomingRinging && !handsetObserver) || (active && handsetOwner));
		setVisible('[data-action="mute"]', active && handsetOwner && !blindActive && !attendedPreparing && (!attendedConsulting || inCall));
		setVisible('[data-action="hold"]', inCall && handsetOwner && !blindActive && !attendedPreparing);
		setVisible('[data-action="transfer"]', inCall && handsetOwner && !attendedActive);
		setVisible('[data-action="attended-transfer"]', inCall && handsetOwner && !blindActive && !attendedPreparing);
		setVisible('[data-action="swap"]', canSwapCalls);
		setVisible('[data-action="cancel-transfer"]', blindActive || attendedActive);
		setVisible('[data-action="keypad"]', active && !incomingRinging && !blindActive && !attendedPreparing && (!attendedConsulting || inCall) && handsetOwner);
		setVisible('[data-action="transfer-contacts"]',
			(blindActive && !this.blindTransfer?.waitingForHold && !this.transferPending)
			|| (attendedPreparing && !this.attendedTransfer?.waitingForHold && !this.attendedTransfer?.pendingDial));

		const canCall = Boolean(
			this.controlReady
			&& this.numbers.length
			&& !this.transferPending
			&& (
				(!active && !attendedActive)
				|| (attendedPreparing && !this.attendedTransfer?.waitingForHold && !this.attendedTransfer?.pendingDial)
			)
		);
		this.$root.find('[data-action="call"]')
			.prop("disabled", !canCall)
			.attr({ "aria-label": attendedPreparing ? __("Call transfer target") : __("Call"), title: attendedPreparing ? __("Call transfer target") : __("Call") })
			.find(".tp-softphone-btn-label").text(attendedPreparing ? __("Call Target") : __("Call"));
		this.$root.find('[data-action="answer"]').prop("disabled", !incomingRinging || handsetObserver).toggleClass("tp-softphone-highlight", incomingRinging && !handsetObserver);
		this.$root.find('[data-action="resume-handset"]').prop("disabled", !handsetNeedsResume || this.handsetClaimPending);
		this.$root.find('[data-action="end"]').prop("disabled", !((incomingRinging && !handsetObserver) || handsetOwner));
		this.$root.find('[data-action="mute"]').prop("disabled", !handsetOwner || state !== "connected" || !this.mediaRunning);
		this.$root.find('[data-action="hold"]').prop("disabled", !handsetOwner || !inCall || this.transferPending);

		const blindReady = !this.blindTransfer?.waitingForHold && (!blindActive || state === "held");
		const transferLabel = blindActive ? __("Transfer") : __("Blind Transfer");
		this.$root.find('[data-action="transfer"]')
			.prop("disabled", !handsetOwner || !this.capabilities.transfer || this.transferPending || !blindReady)
			.attr({
				"aria-label": transferLabel,
				title: this.capabilities.transfer ? transferLabel : __("Transfer is not supported by the active SIP engine"),
			})
			.find(".tp-softphone-btn-label").text(blindActive ? __("Transfer") : __("Blind Transfer"));

		const attendedLabel = attendedConsulting
			? __("Complete attended transfer")
			: attendedPreparing
				? __("Cancel attended transfer")
				: __("Attended transfer");
		const canAttended = attendedPreparing
			? handsetOwner && !this.transferPending
			: attendedConsulting
				? canCompleteAttended && !this.transferPending
				: handsetOwner && this.capabilities.attended_transfer && inCall && !this.transferPending;
		this.$root.find('[data-action="attended-transfer"]')
			.prop("disabled", !canAttended)
			.attr({ "aria-label": attendedLabel, title: attendedLabel })
			.find(".tp-softphone-btn-label").text(attendedConsulting ? __("Complete Transfer") : attendedPreparing ? __("Cancel") : __("Attended Transfer"));
		this.$root.find('[data-action="swap"]').prop("disabled", !canSwapCalls || this.transferPending);
		this.$root.find('[data-action="cancel-transfer"]')
			.prop("disabled", !(blindActive || attendedActive) || this.transferPending)
			.find(".tp-softphone-btn-label").text(blindActive || attendedActive ? __("Cancel Transfer") : __("Back"));

		const currentIsConsult = Boolean(attendedConsulting && call?.call_id && call.call_id === this.attendedTransfer?.consultCallId);
		const currentIsOriginal = Boolean(attendedConsulting && call?.call_id && call.call_id === this.attendedTransfer?.originalCallId);
		const holdLabel = state === "held"
			? (currentIsConsult ? __("Resume Consult") : currentIsOriginal ? __("Resume Original") : __("Resume"))
			: (currentIsConsult ? __("Hold Consult") : currentIsOriginal ? __("Hold Original") : __("Hold"));
		this.$root.find('[data-action="hold"]').attr({ "aria-label": holdLabel, title: holdLabel }).find(".tp-softphone-btn-label").text(holdLabel);
		const muteLabel = this.muted ? __("Unmute") : __("Mute");
		this.$root.find('[data-action="mute"]').attr({ "aria-label": muteLabel, title: muteLabel }).find(".tp-softphone-btn-label").text(muteLabel);
		this.$root.find(".tp-softphone-toggle-inline-btn.tp-softphone-mute").attr("aria-label", muteLabel);
		this.$root.find(".tp-softphone-toggle-inline-btn").prop("disabled", !handsetOwner);

		this.root?.classList.toggle("tp-softphone-muted", this.muted);
		this.root?.classList.toggle("tp-softphone-held", state === "held");
		this.root?.classList.toggle("tp-softphone-transferring", blindActive || this.transferPending);
		this.root?.classList.toggle("tp-softphone-attended", attendedActive);
		this.root?.classList.toggle("tp-softphone-blind", blindActive);
		this.root?.classList.toggle("tp-softphone-call-active", active);
		this.root?.classList.toggle("tp-softphone-call-ringing", incomingRinging);
		this.root?.classList.toggle("tp-softphone-transfer-entry", blindActive || attendedPreparing);
		this.$number?.attr?.("placeholder", blindActive || attendedPreparing ? __("Enter transfer target") : __("Enter number or name"));
		this.renderTransferSummary(canCompleteAttended);
		if (!active && !attendedActive && this.view === "dialer") {
			this.keypadManuallyShown = false;
			this.root?.classList.add("tp-softphone-keypad-visible");
		} else if (active && !blindActive && !attendedPreparing && !this.keypadManuallyShown) {
			this.root?.classList?.remove?.("tp-softphone-keypad-visible");
		}
		const keypadVisible = Boolean(this.root?.classList.contains("tp-softphone-keypad-visible"));
		this.$root.find('[data-action="keypad"]').attr({
			"aria-label": keypadVisible ? __("Hide keypad") : __("Show keypad"),
			title: keypadVisible ? __("Hide keypad") : __("Show keypad"),
		});

		this.renderWaitingCall();
		this.renderQuality();
		const partyLabel = this.callDisplayLabel(call);
		if (call) this.$party.text(partyLabel);
		else this.$party.text(__("Ready"));
		if (call?.contact_image) {
			this.renderContactAvatar(this.$partyAvatar, { full_name: partyLabel, image: call.contact_image });
		} else {
			this.$partyAvatar?.empty?.().text?.(this.partyInitials(partyLabel));
		}
		const partyNumber = call?.number && call.number !== partyLabel ? call.number : "";
		this.$partyNumber?.text?.(partyNumber);
		const baseCallStateLabel = incomingRinging
			? __("Incoming Call")
			: state === "held"
				? __("On Hold")
				: inCall
					? __("Active Call")
					: active
						? (voiceStateLabels[state] || state)
						: __("Ready");
		const ivrAnnouncement = call?.direction === "incoming" ? String(call?.ivr_route || "").trim() : "";
		const callStateLabel = ivrAnnouncement ? `${baseCallStateLabel} · ${ivrAnnouncement}` : baseCallStateLabel;
		this.$callStateLabel?.text?.(callStateLabel).attr?.("title", callStateLabel);
		// Keep the header status about Telephony service readiness. Call state belongs
		// in the dedicated call-state strip below the tabs.
		this.setStatus(this.controlReady ? "idle" : "connecting");
		if (!this.numbers.length && !active) this.$note.text(__("No enabled SIP identity is assigned."));
		else if (!active && this.controlReady) this.$note.text(__("Calls use your authenticated Frappe session."));
		else if (handsetOrphaned) this.$note.text(__("The call is still connected. Resume it on this browser before the reconnect grace expires."));
		else if (mediaResumeRequired) this.$note.text(__("Call ownership was restored. Press Resume to reactivate browser audio."));
		else if (handsetObserver) this.$note.text(__("This call is active in another browser session."));
		else if (call?.direction === "incoming" && call?.ivr_route) this.$note.text(`${__("IVR Route")}: ${call.ivr_route}`);

		const actionButtons = ["call", "answer", "resume-handset", "end", "mute", "hold", "transfer", "attended-transfer", "swap", "cancel-transfer", "keypad", "transfer-contacts"]
			.map((name) => this.$root.find(`[data-action="${name}"]`)[0])
			.filter((button) => button && button.style.display !== "none");
		const columns = actionButtons.length <= 1 ? 1 : Math.min(3, actionButtons.length);
		this.visibleActionRows = Math.max(1, Math.ceil(actionButtons.length / columns));
		if (this.$actions?.length) this.$actions.css("grid-template-columns", `repeat(${columns}, minmax(0, 1fr))`);
		this.$root.find('[data-action="end"] .tp-softphone-btn-label').text(incomingRinging ? __("Decline") : __("Hang Up"));
		this.renderHistoryActiveCall();
		this.renderContacts();
		this.setPanelSizeFromViewport();
		this.updateToggleLayoutForCall();
	}

	async startControl() {
		if (this.voiceWorker || this.voiceStarting) return;
		this.voiceStarting = true;
		clearTimeout(this.voiceReconnectTimer);
		this.voiceReconnectTimer = null;
		try {
			const sitename = String(frappe.boot?.sitename || "").trim();
			if (!sitename) throw new Error(__("Frappe site identity is unavailable."));
			const url = frappe.realtime?.get_host?.(frappe.boot?.socketio_port || 9000)
				|| `${window.location.origin}/${sitename}`;
			const worker = new Worker(frappe.assets.bundled_asset("telephony_voice_transport_worker.bundle.js"));
			this.voiceWorker = worker;
			worker.onmessage = ({ data }) => this.handleControl(data || {});
			worker.onerror = (event) => {
				if (this.voiceWorker !== worker) return;
				this.controlReady = false;
				this.voiceWorker = null;
				worker.terminate();
				if (this.mediaRunning) void this.stopMedia({ notify: false });
				this.setStatus("failed", event.message || __("Telephony voice transport failed."));
				this.scheduleVoiceReconnect();
			};
			worker.postMessage({ type: "start", url, tabId: this.tabId });
		} catch (error) {
			this.controlReady = false;
			this.setStatus("failed", error.message || __("Telephony voice connection failed."));
			this.scheduleVoiceReconnect();
		} finally {
			this.voiceStarting = false;
		}
	}

	scheduleVoiceReconnect() {
		if (this.voiceReconnectTimer || frappe.session?.user === "Guest") return;
		this.voiceReconnectTimer = setTimeout(() => {
			this.voiceReconnectTimer = null;
			void this.startControl();
		}, 1000);
	}

	handleControl(data) {
		if (!data.type) return;
		if (data.type === "ready") {
			this.controlReady = true;
			this.sessionId = data.session_id || null;
			this.registration = data.registration || {};
			this.capabilities = data.capabilities || {};
			void this.runPreflight();
			this.refresh();
			return;
		}
		if (data.type === "disconnect") {
			this.controlReady = false;
			this.sessionId = null;
			if (this.currentCall?.handset?.status === "owner") {
				this.currentCall.handset = { ...this.currentCall.handset, status: "orphaned", owner: false, can_claim: true };
			}
			this.stopRingback();
			const worker = this.voiceWorker;
			this.voiceWorker = null;
			worker?.terminate();
			if (this.mediaRunning || this.mediaPendingCallId || this.mediaSetupPromise) {
				this.mediaFlowEvents.push({ type: "disconnect", reason: data.reason || null, at: Date.now() });
				void this.stopMedia({ notify: false });
			}
			void this.discardPreparedAudio();
			void this.discardPreparedMicrophone();
			this.setStatus("connecting", __("Reconnecting Telephony voice…"));
			void this.runPreflight();
			this.refresh();
			this.scheduleVoiceReconnect();
			return;
		}
		if (data.type === "incoming") { this.receiveIncoming(data.call || {}); return; }
		if (data.type === "created") { this.receiveCreated(data.call || {}); return; }
		if (data.type === "snapshot") { this.receiveSnapshot(data.snapshot || {}); return; }
		if (data.type === "state") { this.receiveState(data.state || {}); return; }
		if (data.type === "handset") { this.receiveHandset(data.callId, data.handset || {}); return; }
		if (data.type === "taken" || data.type === "declined") { this.finishOffer(data.call || {}); return; }
		if (data.type === "transfer_result") { this.receiveTransferResult(data); return; }
		if (data.type === "media_ready") {
			const readyCallId = data.metadata?.call_id || null;
			if (
				!this.audioContext || !this.captureNode || !this.playbackNode || !this.currentCall?.call_id
				|| !readyCallId || readyCallId !== this.currentCall.call_id || readyCallId !== this.mediaPendingCallId
			) return;
			// A restored call can receive media_ready while Safari is still showing
			// the microphone permission prompt and the new AudioContext is suspended.
			// Server bridge readiness is independent of browser playback readiness;
			// keep the bridge alive and let _startMediaOnce resume the AudioContext
			// after microphone acquisition completes.
			this.mediaPendingCallId = null;
			this.mediaRunning = true;
			this.mediaResumeRequired = false;
			this.mediaCallId = readyCallId;
			this.serverMediaStatus = { sip: data.metadata?.sip || {}, running: true };
			const startup = this.mediaStartupTiming?.callId === readyCallId ? this.mediaStartupTiming : null;
			if (startup) startup.realtime_attach_ms = Math.max(0, performance.now() - startup.mediaStartPostedAt);
			this.applyMuteState();
			void this.applySpeakerSelection();
			this.resolveMediaAttach(readyCallId);
			this.$note.text(__("Secure Frappe realtime audio connected."));
			this.refresh();
			return;
		}
		if (data.type === "stats") { this.mediaStats = data; this.transportRttMs = data.rtt_ms ?? this.transportRttMs; return; }
		if (data.type === "latency") { this.transportRttMs = data.rtt_ms ?? null; return; }
		if (data.type === "media_status") { this.serverMediaStatus = data.status || {}; return; }
		if (data.type === "media_xoff" || data.type === "media_xon") {
			this.mediaFlowEvents.push({ type: data.type, queue_length: data.queueLength || 0, at: Date.now() });
			if (this.mediaFlowEvents.length > 100) this.mediaFlowEvents.shift();
			return;
		}
		if (data.type === "media_stopped") {
			const stoppedCallId = data.callId || null;
			if (
				data.stale
				|| (stoppedCallId && this.mediaPendingCallId && stoppedCallId !== this.mediaPendingCallId)
				|| (stoppedCallId && this.mediaCallId && stoppedCallId !== this.mediaCallId)
			) return;
			this.mediaRunning = false;
			if (!stoppedCallId || stoppedCallId === this.mediaCallId) this.mediaCallId = null;
			if (!stoppedCallId || stoppedCallId === this.mediaPendingCallId) this.mediaPendingCallId = null;
			if (this.serverMediaStatus) this.serverMediaStatus = { ...this.serverMediaStatus, running: false };
			this.refresh();
			return;
		}
		if (data.type === "error") {
			const message = data.message || __("Telephony call error.");
			if (this.mediaAttachWaiter && String(data.code || "").startsWith("media_")) {
				this.rejectMediaAttach(this.mediaAttachWaiter.callId, new Error(message));
				return;
			}
			this.receiveError(message);
		}
	}

	receiveIncoming(call) {
		if (!call.call_id) return;
		this.setSoftphoneView("dialer");
		const incoming = {
			call_id: call.call_id,
			direction: "incoming",
			party: call.caller_name || call.caller_id || __("Unknown caller"),
			number: call.caller_id || "",
			called_number: call.called_number || "",
			caller_name: call.caller_name || "",
			ivr_route: call.ivr_route || "",
			state: "ringing",
			handset: call.handset || { status: "available", owner: false, can_claim: true },
		};
		if (this.currentCall && this.currentCall.call_id !== call.call_id && !["ended", "failed"].includes(this.currentCall.state)) {
			this.waitingCall = incoming;
			this.maybeOpenDeepLinkedCall(incoming);
			void this.resolveCallContact(incoming);
			this.callSequences.delete(call.call_id);
			try { frappe.utils?.play_sound?.("alert"); } catch (_) {}
			this.toggle(true);
			this.refresh();
			frappe.show_alert({ message: __("Another Telephony call is ringing."), indicator: "orange" });
			try { navigator.vibrate?.([180, 90, 180]); } catch (_) {}
			return;
		}
		this.muted = false;
		this.currentCall = incoming;
		this.maybeOpenDeepLinkedCall(incoming);
		this.root?.classList?.remove?.("tp-softphone-keypad-visible");
		this.keypadManuallyShown = false;
		this.callSequences.delete(call.call_id);
		this.startRinging();
		this.toggle(true);
		this.refresh();
		void this.resolveCallContact(incoming, { notify: true });
		try { navigator.vibrate?.([250, 120, 250]); } catch (_) {}
	}

	receiveCreated(call) {
		if (!call.call_id) return;
		if (
			this.currentCall
			&& this.currentCall.call_id !== call.call_id
			&& !["ended", "failed"].includes(this.currentCall.state)
			&& call.handset?.status !== "owner"
		) {
			return;
		}
		this.stopRingback();
		this.muted = false;
		this.currentCall = {
			call_id: call.call_id,
			direction: "outgoing",
			party: call.number || this.$number.val() || "",
			number: call.number || "",
			state: "dialing",
			handset: call.handset || { status: "owner", owner: true, can_claim: false },
		};
		this.root?.classList?.remove?.("tp-softphone-keypad-visible");
		this.keypadManuallyShown = false;
		this.callSequences.delete(call.call_id);
		if (this.attendedTransfer?.pendingDial) {
			this.attendedTransfer.consultCallId = call.call_id;
			this.attendedTransfer.pendingDial = false;
			this.attendedTransfer.state = "consulting";
			this.$note.text(__("Consultation call started. Speak with the transfer target, then complete the transfer."));
		}
		this.refresh();
		void this.resolveCallContact(this.currentCall);
	}

	receiveSnapshot(snapshot) {
		if (!snapshot.call_id || !snapshot.direction || !snapshot.state) return;
		const restored = {
			call_id: snapshot.call_id,
			direction: snapshot.direction,
			party: snapshot.direction === "incoming"
				? (snapshot.caller_name || snapshot.from_number || __("Unknown caller"))
				: (snapshot.to_number || __("Outgoing call")),
			number: snapshot.direction === "incoming" ? (snapshot.from_number || "") : (snapshot.to_number || ""),
			called_number: snapshot.to_number || "",
			caller_name: snapshot.caller_name || "",
			ivr_route: snapshot.ivr_route || "",
			state: snapshot.state,
			handset: snapshot.handset || { status: "available", owner: false, can_claim: true },
		};
		this.syncDurationFromServer(restored, snapshot);
		this.maybeOpenDeepLinkedCall(restored);
		void this.resolveCallContact(restored);
		if (snapshot.auto_restored) this.autoRestoredCallIds.add(snapshot.call_id);
		this.callSequences.delete(snapshot.call_id);
		if (this.currentCall && this.currentCall.call_id !== snapshot.call_id && !["ended", "failed"].includes(this.currentCall.state)) {
			if (snapshot.state === "ringing" && snapshot.direction === "incoming") {
				this.waitingCall = restored;
				try { frappe.utils?.play_sound?.("alert"); } catch (_) {}
			} else if (snapshot.state === "held") {
				this.parkedCall = restored;
			} else if (snapshot.state === "connected") {
				const previous = this.currentCall;
				if (previous.state === "held") this.parkedCall = previous;
				else if (previous.direction === "incoming" && previous.state === "ringing") this.waitingCall = previous;
				this.currentCall = restored;
				if (this.waitingCall?.call_id === restored.call_id) this.waitingCall = null;
				if (this.parkedCall?.call_id === restored.call_id) this.parkedCall = null;
				this.startDuration();
			}
			this.refresh();
			if (snapshot.auto_restored) this.scheduleAutoResumeRestoredHandset();
			return;
		}
		this.currentCall = restored;
		if (snapshot.state === "ringing" && snapshot.direction === "incoming") this.startRinging();
		if (snapshot.state === "ringing" && snapshot.direction === "outgoing") this.startRingback();
		if (["connected", "held"].includes(snapshot.state)) this.startDuration();
		this.refresh();
		if (snapshot.auto_restored) this.scheduleAutoResumeRestoredHandset();
	}

	scheduleAutoResumeRestoredHandset() {
		clearTimeout(this.autoRestoreTimer);
		this.autoRestoreTimer = setTimeout(() => {
			this.autoRestoreTimer = null;
			void this.autoResumeRestoredHandset();
		}, 75);
	}

	async autoResumeRestoredHandset() {
		const call = this.currentCall;
		if (!call?.call_id || !this.autoRestoredCallIds.has(call.call_id)) return;
		this.autoRestoredCallIds.delete(call.call_id);
		if (call.handset?.status !== "owner" || call.state !== "connected") return;
		if (this.mediaRunning || this.mediaPendingCallId === call.call_id || this.mediaSetupPromise) return;
		try {
			// A page reload has no transient user activation. Let media setup acquire
			// the already-permitted microphone first, then retry AudioContext.resume().
			// Browsers that still require a gesture fall back to the visible Resume control.
			await this.primeAudio({ requireRunning: false });
			if (this.currentCall?.call_id !== call.call_id || this.currentCall.handset?.status !== "owner" || this.currentCall.state !== "connected") return;
			await this.startMedia(call.call_id);
			this.mediaResumeRequired = false;
			this.$note.text(__("Call resumed automatically after refresh."));
		} catch (_) {
			this.mediaResumeRequired = true;
			this.$note.text(__("Call restored. Your browser requires Resume to reactivate microphone and speaker audio."));
		}
		this.refresh();
	}

	receiveState(state) {
		if (!state.call_id) return;
		if (["ended", "failed"].includes(state.state)) {
			this.historyDirty = true;
			if (this.view === "history") {
				setTimeout(() => {
					if (this.view === "history" && this.historyDirty && !this.historyLoading) void this.loadHistoryPage({ reset: true });
				}, 500);
			}
		}
		const sequence = Number(state.sequence);
		const previous = this.callSequences.get(state.call_id);
		if (Number.isFinite(sequence) && previous !== undefined && sequence <= previous) return;
		if (Number.isFinite(sequence)) this.callSequences.set(state.call_id, sequence);

		if (this.waitingCall?.call_id === state.call_id) {
			this.waitingCall.state = state.state;
			this.syncDurationFromServer(this.waitingCall, state);
			if (state.handset) this.waitingCall.handset = state.handset;
			if (["ended", "failed", "disconnecting"].includes(state.state)) {
				this.waitingCall = null;
				if (this.currentCall?.state !== "ringing") this.stopRinging();
			}
			this.refresh();
			return;
		}
		if (this.parkedCall?.call_id === state.call_id) {
			this.parkedCall.state = state.state;
			this.syncDurationFromServer(this.parkedCall, state);
			if (state.handset) this.parkedCall.handset = state.handset;
			if (["ended", "failed"].includes(state.state)) {
				const endedCall = this.parkedCall;
				this.parkedCall = null;
				this.callSequences.delete(state.call_id);
				if (this.transferredCallIds.has(state.call_id)) {
					this.transferredCallIds.delete(state.call_id);
					this.$note.text(__("Transferred call released by the PBX."));
				} else {
					void this.recoverAttendedTransferAfterLegEnded(endedCall.call_id, this.currentCall);
				}
			}
			this.refresh();
			return;
		}
		if (!this.currentCall || state.call_id !== this.currentCall.call_id) return;

		this.currentCall.state = state.state;
		this.syncDurationFromServer(this.currentCall, state);
		if (state.handset) this.currentCall.handset = state.handset;
		if (state.state === "ringing" && this.currentCall.direction === "outgoing") this.startRingback();
		if (state.state !== "ringing") this.stopRingback();
		if (state.state !== "ringing" && !this.waitingCall) this.stopRinging();
		if (state.state === "held") {
			this.startDuration();
			if (this.mediaRunning) void this.stopMedia();
			this.$note.text(__("Call is on hold."));
			if (this.blindTransfer?.waitingForHold && this.blindTransfer.callId === state.call_id) {
				this.blindTransfer.waitingForHold = false;
				this.$note.text(__("Enter number to transfer to, then press Transfer again."));
				this.ensureKeypadVisible();
			}
			if (
				this.attendedTransfer?.waitingForHold
				&& this.attendedTransfer.originalCallId === state.call_id
			) {
				this.attendedTransfer.waitingForHold = false;
				this.attendedTransfer.state = "preparing";
				this.$note.text(__("Enter number then press Call to consult."));
				this.ensureKeypadVisible();
			}
			if (this.pendingCallSwitch?.fromCallId === state.call_id) {
				void this.completePendingCallSwitch(state.call_id);
			}
		}
		if (state.state === "connected") {
			this.startDuration();
			this.mediaConnectedAt.set(state.call_id, performance.now());
			if (this.currentCall.handset?.status === "owner" && this.preparedAudioContext) {
				void this.startMedia(state.call_id).catch((error) => this.receiveError(error.message || String(error)));
			} else if (this.currentCall.handset?.status !== "owner" && this.preparedAudioContext) {
				void this.discardPreparedAudio();
			} else if (this.mediaRunning) {
				this.$note.text(__("Secure Frappe realtime audio connected."));
			}
		}
		if (state.state === "ended" || state.state === "failed") this.finishCall(state);
		this.refresh();
	}

	receiveHandset(callId, handset) {
		if (!callId) return;
		const isCurrent = this.currentCall?.call_id === callId;
		const previousStatus = isCurrent ? this.currentCall?.handset?.status : null;
		const targets = [this.currentCall, this.waitingCall, this.parkedCall].filter((call) => call?.call_id === callId);
		for (const target of targets) target.handset = handset;
		if (!isCurrent) {
			this.refresh();
			return;
		}
		this.handsetClaimPending = false;
		if (handset.status === "orphaned") {
			frappe.show_alert({ message: __("The active Telephony call can be resumed on this browser."), indicator: "orange" });
		}
		const lostOwnership = previousStatus === "owner" && handset.status !== "owner";
		if (handset.status === "observer" && this.currentCall.direction === "incoming" && this.currentCall.state === "ringing") {
			this.stopRinging();
		}
		if (lostOwnership) {
			if (this.preparedAudioContext) void this.discardPreparedAudio();
			void this.discardPreparedMicrophone();
			if (this.mediaRunning || this.mediaPendingCallId === callId || this.mediaSetupPromise) void this.stopMedia();
		}
		if (handset.status === "owner" && this.currentCall.direction === "outgoing" && this.currentCall.state === "ringing") {
			this.startRingback();
		}
		const resumedOwnership = previousStatus === "orphaned" && handset.status === "owner";
		if (resumedOwnership && this.currentCall.state === "connected" && !this.mediaRunning && this.mediaPendingCallId !== callId) {
			void this.startMedia(callId).catch((error) => this.receiveError(error.message || String(error)));
		}
		this.refresh();
	}

	async resumeHandset() {
		if (!this.currentCall?.call_id || !this.voiceWorker) return;
		if (this.currentCall.handset?.status === "owner" && this.mediaResumeRequired && this.currentCall.state === "connected") {
			this.handsetClaimPending = true;
			try {
				await this.primeAudio();
				await this.startMedia(this.currentCall.call_id);
				this.mediaResumeRequired = false;
				this.$note.text(__("Call audio resumed."));
			} catch (error) {
				this.receiveError(error.message || String(error));
			} finally {
				this.handsetClaimPending = false;
				this.refresh();
			}
			return;
		}
		if (this.currentCall.handset?.status !== "orphaned") return;
		const knownCalls = [this.currentCall, this.waitingCall, this.parkedCall]
			.filter((call, index, values) => call?.call_id && values.findIndex((item) => item?.call_id === call.call_id) === index);
		const connected = knownCalls.find((call) => call.state === "connected");
		if (connected && connected.call_id !== this.currentCall.call_id) {
			const previous = this.currentCall;
			this.currentCall = connected;
			if (this.waitingCall?.call_id === connected.call_id) this.waitingCall = null;
			if (this.parkedCall?.call_id === connected.call_id) this.parkedCall = null;
			if (previous.state === "held") this.parkedCall = previous;
			else if (previous.direction === "incoming" && previous.state === "ringing") this.waitingCall = previous;
		}
		const orphanedCalls = [this.currentCall, this.waitingCall, this.parkedCall]
			.filter((call, index, values) => call?.call_id && call.handset?.status === "orphaned"
				&& values.findIndex((item) => item?.call_id === call.call_id) === index);
		if (!orphanedCalls.length) return;
		this.handsetClaimPending = true;
		try {
			if (["ringing", "connecting", "connected"].includes(this.currentCall.state)) await this.primeAudio();
			for (const call of orphanedCalls) {
				this.voiceWorker.postMessage({ type: "handset_claim", callId: call.call_id });
			}
			this.refresh();
		} catch (error) {
			this.handsetClaimPending = false;
			this.receiveError(error.message || String(error));
		}
	}

	finishOffer(call) {
		if (!call.call_id) return;
		if (this.waitingCall?.call_id === call.call_id) {
			this.waitingCall = null;
			this.callSequences.delete(call.call_id);
			if (this.currentCall?.state !== "ringing") this.stopRinging();
			this.refresh();
			return;
		}
		if (this.currentCall?.call_id !== call.call_id) return;
		this.stopRinging();
		this.currentCall = null;
		this.handsetClaimPending = false;
		this.callSequences.delete(call.call_id);
		void this.discardPreparedAudio();
		void this.discardPreparedMicrophone();
		this.refresh();
	}

	receiveError(message) {
		const blindNeedsRecovery = Boolean(this.blindTransfer && (this.blindTransfer.waitingForHold || this.transferPending));
		const attendedHoldFailed = Boolean(this.attendedTransfer?.waitingForHold);
		const pendingConsultDial = Boolean(this.attendedTransfer?.pendingDial && !this.attendedTransfer?.consultCallId);
		this.pendingCallSwitch = null;
		this.transferPending = false;
		this.$note.text(message);
		frappe.show_alert({ message, indicator: "red" });
		if (attendedHoldFailed) {
			this.attendedTransfer = null;
			this.refresh();
			return;
		}
		if (pendingConsultDial) {
			void this.cancelAttendedTransfer({ skipConsultHangup: true });
			return;
		}
		if (blindNeedsRecovery) {
			void this.cancelBlindTransfer();
			return;
		}
		if (!this.currentCall) {
			this.$root.find('[data-action="call"]').prop("disabled", false);
			void this.discardPreparedAudio();
			void this.discardPreparedMicrophone();
		}
		this.refresh();
	}

	async dial() {
		if (this.attendedTransfer?.state === "preparing") {
			await this.startAttendedConsultCall();
			return;
		}
		if (this.currentCall && !["ended", "failed"].includes(this.currentCall.state)) return;
		const number = String(this.$number.val() || "").trim();
		if (!/^[0-9+*#]{1,64}$/.test(number)) {
			this.$note.text(__("Enter a valid phone number."));
			return;
		}
		if (!this.controlReady || !this.numbers.length) return;
		this.$note.text(__("Preparing microphone…"));
		try {
			// Keep the post-jitter-fix startup behavior for both transports: acquire
			// microphone access from the user gesture before SIP can connect. The
			// direct path consumes this prepared stream in its T-064 media graph.
			await this.prepareCallAudio();
		} catch (error) {
			this.receiveError(error.message || String(error));
			return;
		}
		if (!this.controlReady || (this.currentCall && !["ended", "failed"].includes(this.currentCall.state))) {
			await this.discardPreparedMicrophone();
			return;
		}
		this.$note.text(__("Starting call…"));
		this.voiceWorker.postMessage({
			type: "dial",
			number,
		});
	}

	async answer() {
		if (this.currentCall?.direction !== "incoming" || this.currentCall.state !== "ringing") return;
		if (["observer", "unavailable"].includes(this.currentCall.handset?.status)) return;
		const callId = this.currentCall.call_id;
		this.$note.text(__("Preparing microphone before answering…"));
		try {
			await this.prepareCallAudio();
		} catch (error) {
			this.receiveError(error.message || String(error));
			return;
		}
		if (
			this.currentCall?.call_id !== callId || this.currentCall.direction !== "incoming"
			|| this.currentCall.state !== "ringing" || ["observer", "unavailable"].includes(this.currentCall.handset?.status)
		) {
			await this.discardPreparedMicrophone();
			return;
		}
		this.stopRinging();
		this.voiceWorker.postMessage({ type: "answer", callId });
	}

	reject() {
		if (this.currentCall?.direction !== "incoming" || this.currentCall.state !== "ringing") return;
		if (["observer", "unavailable"].includes(this.currentCall.handset?.status)) return;
		this.stopRinging();
		this.voiceWorker.postMessage({ type: "reject", callId: this.currentCall.call_id });
	}

	hangup() {
		if (!this.currentCall?.call_id || this.currentCall.handset?.status !== "owner") return;
		this.stopRingback();
		this.voiceWorker.postMessage({ type: "hangup", callId: this.currentCall.call_id });
		if (this.currentCall.state !== "ended" && this.currentCall.state !== "failed") {
			this.currentCall.state = "disconnecting";
			this.refresh();
		}
	}

	hold() {
		if (this.currentCall?.state !== "connected" || this.currentCall.handset?.status !== "owner") return;
		this.voiceWorker.postMessage({ type: "hold", callId: this.currentCall.call_id });
		this.$note.text(__("Putting call on hold…"));
	}

	async resumeCall() {
		if (this.currentCall?.state !== "held" || this.currentCall.handset?.status !== "owner") return;
		if (!this.mediaRunning) await this.primeAudio();
		this.voiceWorker.postMessage({ type: "resume", callId: this.currentCall.call_id });
		this.$note.text(__("Resuming call…"));
	}

	sendDtmf(digit) {
		if (this.currentCall?.state !== "connected" || this.currentCall.handset?.status !== "owner" || !/^[0-9*#]$/.test(digit)) return;
		this.voiceWorker.postMessage({ type: "dtmf", callId: this.currentCall.call_id, digit });
	}

	renderWaitingCall() {
		const waiting = this.waitingCall;
		this.$waitingCall.toggleClass("tp-visible", Boolean(waiting));
		if (!waiting) return;
		this.$waitingParty.text(`${__("Incoming")}: ${this.callDisplayLabel(waiting)}`);
		const currentOwned = !this.currentCall || ["ended", "failed"].includes(this.currentCall.state) || this.currentCall.handset?.status === "owner";
		const waitingAvailable = !["observer", "unavailable"].includes(waiting.handset?.status);
		this.$root.find('[data-action="answer-waiting"]').prop("disabled", !this.controlReady || !currentOwned || !waitingAvailable);
	}

	toggleKeypad() {
		const visible = this.root?.classList.toggle("tp-softphone-keypad-visible");
		this.keypadManuallyShown = this.historyHasActiveCall() ? Boolean(visible) : false;
		this.refresh();
	}

	ensureKeypadVisible() {
		this.root?.classList.add("tp-softphone-keypad-visible");
	}

	clearDialInput() {
		this.$number.val("").trigger("input");
	}

	pressDigit(digit) {
		if (!/^[0-9*#]$/.test(digit)) return;
		void this.playKeypadTone(digit);
		const collectingTransferTarget = Boolean(this.blindTransfer || this.attendedTransfer?.state === "preparing");
		if (!collectingTransferTarget && this.currentCall?.state === "connected") {
			this.sendDtmf(digit);
			return;
		}
		const current = String(this.$number.val() || "");
		this.$number.val(`${current}${digit}`).trigger("input");
	}

	backspaceNumber() {
		const current = String(this.$number.val() || "");
		if (!current) return;
		this.$number.val(current.slice(0, -1)).trigger("input");
	}

	updateClearButtonVisibility() {
		const visible = Boolean(String(this.$number.val() || ""));
		this.$root.find('[data-action="backspace"]').css("display", visible ? "inline-flex" : "none");
	}

	loadLayoutPrefs() {
		try {
			const raw = localStorage.getItem(TELEPHONY_VOICE_LAYOUT_KEY);
			const parsed = raw ? JSON.parse(raw) : {};
			return parsed && typeof parsed === "object" ? parsed : {};
		} catch (_) {
			return {};
		}
	}

	saveLayoutPrefs() {
		try { localStorage.setItem(TELEPHONY_VOICE_LAYOUT_KEY, JSON.stringify(this.layoutPrefs || {})); } catch (_) {}
	}

	applyLayoutPrefs() {
		if (!this.root) return;
		const pos = this.layoutPrefs?.position || {};
		if (Number.isFinite(pos.left) && Number.isFinite(pos.top)) {
			this.root.style.left = `${pos.left}px`;
			this.root.style.top = `${pos.top}px`;
			this.root.style.right = "auto";
			this.root.style.bottom = "auto";
		} else {
			this.root.style.right = "24px";
			this.root.style.bottom = "24px";
			this.root.style.left = "";
			this.root.style.top = "";
		}
		this.ensureAbsolutePosition();
		const savedPanelOpen = this.layoutPrefs?.panelOpen === true;
		this.toggle(savedPanelOpen, { persist: false, focus: false });
		this.clampToViewport();
	}

	ensureAbsolutePosition() {
		if (!this.root || (this.root.style.left && this.root.style.top)) return;
		const rect = this.root.getBoundingClientRect();
		const left = rect.left;
		const top = rect.top;
		this.root.style.left = `${left}px`;
		this.root.style.top = `${top}px`;
		this.root.style.right = "auto";
		this.root.style.bottom = "auto";
		this.layoutPrefs.position = { left, top };
		this.saveLayoutPrefs();
	}

	clampToViewport() {
		if (!this.root) return;
		if (this.$panel?.hasClass?.("tp-open") && this.panelBodyPosition) {
			this.positionOpenPanelBody();
			return;
		}
		const width = this.root.offsetWidth || 0;
		const height = this.root.offsetHeight || 0;
		if (!width || !height) return;
		const marginX = window.innerWidth >= width + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		const marginY = window.innerHeight >= height + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		const maxX = Math.max(window.innerWidth - width - marginX, 0);
		const maxY = Math.max(window.innerHeight - height - marginY, 0);
		let left = parseFloat(this.root.style.left || "0");
		let top = parseFloat(this.root.style.top || "0");
		if (!Number.isFinite(left) || !Number.isFinite(top)) {
			const rect = this.root.getBoundingClientRect();
			left = rect.left;
			top = rect.top;
		}
		left = Math.min(Math.max(left, marginX), maxX);
		top = Math.min(Math.max(top, marginY), maxY);
		this.root.style.left = `${left}px`;
		this.root.style.top = `${top}px`;
		this.root.style.right = "auto";
		this.root.style.bottom = "auto";
		this.layoutPrefs.position = { left, top };
		this.saveLayoutPrefs();
		this.positionPanelAroundToggle();
	}

	clampPanelBodyPosition(left, top, width, height, dockSide = null) {
		const viewportWidth = Math.max(window.innerWidth || 0, 0);
		const viewportHeight = Math.max(window.innerHeight || 0, 0);
		const toggleWidth = this.root?.offsetWidth || 0;
		const toggleHeight = this.root?.offsetHeight || 0;
		const marginX = viewportWidth >= width + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		const marginY = viewportHeight >= height + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		let minLeft = marginX;
		let maxLeft = Math.max(viewportWidth - width - marginX, minLeft);
		let minTop = marginY;
		let maxTop = Math.max(viewportHeight - height - marginY, minTop);

		// Keep a real outside lane for the docked toggle. The panel yields enough
		// space at the viewport edge so the toggle never has to overlap the panel.
		if (dockSide === "left") minLeft += toggleWidth + TELEPHONY_TOGGLE_DOCK_GAP;
		if (dockSide === "right") maxLeft -= toggleWidth + TELEPHONY_TOGGLE_DOCK_GAP;
		if (dockSide === "top") minTop += toggleHeight + TELEPHONY_TOGGLE_DOCK_GAP;
		if (dockSide === "bottom") maxTop -= toggleHeight + TELEPHONY_TOGGLE_DOCK_GAP;

		if (maxLeft < minLeft || maxTop < minTop) return null;
		return {
			left: Math.min(Math.max(left, minLeft), maxLeft),
			top: Math.min(Math.max(top, minTop), maxTop),
		};
	}

	dockToggleAroundPanel(panelLeft, panelTop, panelWidth, panelHeight, targetCenter, preferredSide = null) {
		if (!this.root) return null;
		const width = this.root.offsetWidth || 0;
		const height = this.root.offsetHeight || 0;
		if (!width || !height) return null;
		const halfW = width / 2;
		const halfH = height / 2;
		const viewportWidth = Math.max(window.innerWidth || 0, 0);
		const viewportHeight = Math.max(window.innerHeight || 0, 0);
		const minCenterX = TELEPHONY_WIDGET_VIEWPORT_MARGIN + halfW;
		const maxCenterX = Math.max(viewportWidth - TELEPHONY_WIDGET_VIEWPORT_MARGIN - halfW, minCenterX);
		const minCenterY = TELEPHONY_WIDGET_VIEWPORT_MARGIN + halfH;
		const maxCenterY = Math.max(viewportHeight - TELEPHONY_WIDGET_VIEWPORT_MARGIN - halfH, minCenterY);
		const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
		const sides = ["top", "right", "bottom", "left"];
		const candidates = [];

		for (const side of sides) {
			const body = this.clampPanelBodyPosition(panelLeft, panelTop, panelWidth, panelHeight, side);
			if (!body) continue;
			const panelRect = { left: body.left, top: body.top, right: body.left + panelWidth, bottom: body.top + panelHeight };
			let x;
			let y;
			if (side === "top" || side === "bottom") {
				x = clamp(targetCenter.x, Math.max(panelRect.left + halfW, minCenterX), Math.min(panelRect.right - halfW, maxCenterX));
				y = side === "top"
					? panelRect.top - TELEPHONY_TOGGLE_DOCK_GAP - halfH
					: panelRect.bottom + TELEPHONY_TOGGLE_DOCK_GAP + halfH;
			} else {
				x = side === "left"
					? panelRect.left - TELEPHONY_TOGGLE_DOCK_GAP - halfW
					: panelRect.right + TELEPHONY_TOGGLE_DOCK_GAP + halfW;
				y = clamp(targetCenter.y, Math.max(panelRect.top + halfH, minCenterY), Math.min(panelRect.bottom - halfH, maxCenterY));
			}
			if (x < minCenterX || x > maxCenterX || y < minCenterY || y > maxCenterY) continue;
			candidates.push({
				side, body, x, y,
				distance: Math.hypot(x - targetCenter.x, y - targetCenter.y),
			});
		}
		if (!candidates.length) return null;
		candidates.sort((a, b) => a.distance - b.distance);
		let chosen = candidates[0];
		const preferred = candidates.find((candidate) => candidate.side === preferredSide);
		if (preferred && preferred.distance <= chosen.distance + TELEPHONY_TOGGLE_DOCK_HYSTERESIS) chosen = preferred;
		return {
			left: chosen.x - halfW,
			top: chosen.y - halfH,
			side: chosen.side,
			body: chosen.body,
		};
	}

	applyOpenPanelGeometry(panelLeft, panelTop, targetCenter, preferredSide = this.panelDockSide) {
		if (!this.root || !this.panel) return;
		const panelWidth = this.panel.offsetWidth || parseFloat(this.panel.style.width || "0");
		const panelHeight = this.panel.offsetHeight || parseFloat(this.panel.style.height || "0");
		if (!panelWidth || !panelHeight) return;
		const dock = this.dockToggleAroundPanel(panelLeft, panelTop, panelWidth, panelHeight, targetCenter, preferredSide);
		if (!dock) return;
		const body = dock.body;
		this.root.style.left = `${dock.left}px`;
		this.root.style.top = `${dock.top}px`;
		this.root.style.right = "auto";
		this.root.style.bottom = "auto";
		Object.assign(this.panel.style, {
			left: `${body.left - dock.left}px`,
			top: `${body.top - dock.top}px`,
			right: "auto",
			bottom: "auto",
		});
		this.panelBodyPosition = body;
		this.panelDockSide = dock.side;
		this.panel.dataset.dockSide = dock.side;
		this.layoutPrefs.position = { left: dock.left, top: dock.top };
	}

	positionOpenPanelBody(targetCenter = null) {
		if (!this.root || !this.panel || !this.panelBodyPosition) return;
		const rootRect = this.root.getBoundingClientRect();
		const center = targetCenter || { x: rootRect.left + rootRect.width / 2, y: rootRect.top + rootRect.height / 2 };
		this.applyOpenPanelGeometry(this.panelBodyPosition.left, this.panelBodyPosition.top, center, this.panelDockSide);
	}

	choosePanelAxis({ current, first, second, firstFits, secondFits, firstOverflow, secondOverflow }) {
		if (current === first && (firstOverflow <= TELEPHONY_PANEL_FLIP_HYSTERESIS || !secondFits)) return first;
		if (current === second && (secondOverflow <= TELEPHONY_PANEL_FLIP_HYSTERESIS || !firstFits)) return second;
		if (firstFits) return first;
		if (secondFits) return second;
		return firstOverflow <= secondOverflow ? first : second;
	}

	positionPanelAroundToggle() {
		if (!this.root || !this.panel) return;
		if (this.$panel?.hasClass?.("tp-open") && this.panelBodyPosition) {
			this.positionOpenPanelBody();
			return;
		}
		const anchor = this.$toggle?.[0] || this.button || this.root;
		if (!anchor?.getBoundingClientRect) return;
		const anchorRect = anchor.getBoundingClientRect();
		const panelWidth = this.panel.offsetWidth || parseFloat(this.panel.style.width || "0");
		const panelHeight = this.panel.offsetHeight || parseFloat(this.panel.style.height || "0");
		if (!panelWidth || !panelHeight) return;

		const viewportWidth = Math.max(window.innerWidth || 0, 0);
		const viewportHeight = Math.max(window.innerHeight || 0, 0);
		const marginX = viewportWidth >= panelWidth + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		const marginY = viewportHeight >= panelHeight + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		const maxLeft = Math.max(viewportWidth - panelWidth - marginX, 0);
		const maxTop = Math.max(viewportHeight - panelHeight - marginY, 0);

		const opensLeft = anchorRect.right - panelWidth;
		const opensRight = anchorRect.left;
		const leftOverflow = Math.max(marginX - opensLeft, 0);
		const rightOverflow = Math.max(opensRight + panelWidth - (viewportWidth - marginX), 0);
		const horizontalPlacement = this.choosePanelAxis({
			current: this.panelPlacement?.horizontal, first: "left", second: "right",
			firstFits: leftOverflow === 0, secondFits: rightOverflow === 0, firstOverflow: leftOverflow, secondOverflow: rightOverflow,
		});
		let panelLeft = horizontalPlacement === "left" ? opensLeft : opensRight;

		const opensAbove = anchorRect.top - TELEPHONY_PANEL_ANCHOR_GAP - panelHeight;
		const opensBelow = anchorRect.bottom + TELEPHONY_PANEL_ANCHOR_GAP;
		const aboveOverflow = Math.max(marginY - opensAbove, 0);
		const belowOverflow = Math.max(opensBelow + panelHeight - (viewportHeight - marginY), 0);
		const verticalPlacement = this.choosePanelAxis({
			current: this.panelPlacement?.vertical, first: "above", second: "below",
			firstFits: aboveOverflow === 0, secondFits: belowOverflow === 0, firstOverflow: aboveOverflow, secondOverflow: belowOverflow,
		});
		let panelTop = verticalPlacement === "above" ? opensAbove : opensBelow;

		panelLeft = Math.min(Math.max(panelLeft, marginX), maxLeft);
		panelTop = Math.min(Math.max(panelTop, marginY), maxTop);
		const rootRect = this.root.getBoundingClientRect();
		Object.assign(this.panel.style, {
			left: `${panelLeft - rootRect.left}px`,
			top: `${panelTop - rootRect.top}px`,
			right: "auto",
			bottom: "auto",
		});
		this.panelPlacement = { horizontal: horizontalPlacement, vertical: verticalPlacement };
		const horizontalClamped = panelLeft !== (horizontalPlacement === "left" ? opensLeft : opensRight);
		const verticalClamped = panelTop !== (verticalPlacement === "above" ? opensAbove : opensBelow);
		this.panel.dataset.placement = `${verticalPlacement}${verticalClamped ? "-clamped" : ""}-${horizontalPlacement}${horizontalClamped ? "-clamped" : ""}`;
		if (this.$panel?.hasClass?.("tp-open")) {
			this.panelBodyPosition = { left: panelLeft, top: panelTop };
			this.panelDockSide = verticalPlacement === "above" ? "bottom" : verticalPlacement === "below" ? "top" : horizontalPlacement === "left" ? "right" : "left";
			this.panel.dataset.dockSide = this.panelDockSide;
		}
	}

	startDrag(event, source = "toggle") {
		if (!this.root || event.button === 2) return;
		this.ensureAbsolutePosition();
		this.dragging = true;
		this.dragMoved = false;
		this.dragStart = { x: event.clientX, y: event.clientY };
		const rect = this.root.getBoundingClientRect();
		this.dragOffset = { x: event.clientX - rect.left, y: event.clientY - rect.top };
		this.dragSource = source;
		if (this.$panel?.hasClass?.("tp-open") && this.panelBodyPosition && this.panel) {
			const panelRect = this.panel.getBoundingClientRect();
			this.panelDragState = {
				source, pointerX: event.clientX, pointerY: event.clientY,
				panelLeft: panelRect.left, panelTop: panelRect.top,
				toggleLeft: rect.left, toggleTop: rect.top, toggleWidth: rect.width, toggleHeight: rect.height,
			};
		}
		this.root.classList?.add("tp-softphone-dragging");
		document.addEventListener("pointermove", this.boundDragMove);
		document.addEventListener("pointerup", this.boundDragEnd);
		event.preventDefault();
	}

	applyDragPosition(clientX, clientY) {
		if (!this.dragging || !this.root || !this.dragOffset) return;
		if (this.panelDragState && this.$panel?.hasClass?.("tp-open")) {
			const state = this.panelDragState;
			const panelWidth = this.panel.offsetWidth || 0;
			const panelHeight = this.panel.offsetHeight || 0;
			const body = this.clampPanelBodyPosition(
				state.panelLeft + clientX - state.pointerX,
				state.panelTop + clientY - state.pointerY,
				panelWidth, panelHeight,
			);
			const movedX = body.left - state.panelLeft;
			const movedY = body.top - state.panelTop;
			let targetCenter;
			if (state.source === "panel") {
				targetCenter = {
					x: state.toggleLeft + movedX + state.toggleWidth / 2,
					y: state.toggleTop + movedY + state.toggleHeight / 2,
				};
			} else {
				targetCenter = {
					x: clientX - this.dragOffset.x + (this.root.offsetWidth || state.toggleWidth) / 2,
					y: clientY - this.dragOffset.y + (this.root.offsetHeight || state.toggleHeight) / 2,
				};
			}
			this.applyOpenPanelGeometry(body.left, body.top, targetCenter, this.panelDockSide);
			return;
		}
		const marginX = window.innerWidth >= this.root.offsetWidth + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		const marginY = window.innerHeight >= this.root.offsetHeight + TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2 ? TELEPHONY_WIDGET_VIEWPORT_MARGIN : 0;
		const maxX = Math.max(window.innerWidth - this.root.offsetWidth - marginX, 0);
		const maxY = Math.max(window.innerHeight - this.root.offsetHeight - marginY, 0);
		const left = Math.min(Math.max(clientX - this.dragOffset.x, marginX), maxX);
		const top = Math.min(Math.max(clientY - this.dragOffset.y, marginY), maxY);
		this.root.style.left = `${left}px`;
		this.root.style.top = `${top}px`;
		this.root.style.right = "auto";
		this.root.style.bottom = "auto";
		this.layoutPrefs.position = { left, top };
		this.positionPanelAroundToggle();
	}

	onDragMove(event) {
		if (!this.dragging || !this.root || !this.dragOffset) return;
		const dx = event.clientX - (this.dragStart?.x || event.clientX);
		const dy = event.clientY - (this.dragStart?.y || event.clientY);
		if (!this.dragMoved && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) this.dragMoved = true;
		this.dragPointer = { x: event.clientX, y: event.clientY };
		if (this.dragFrame !== null) return;
		const requestFrame = window.requestAnimationFrame?.bind(window) || ((callback) => setTimeout(callback, 16));
		this.dragFrame = requestFrame(() => {
			this.dragFrame = null;
			const pointer = this.dragPointer;
			this.dragPointer = null;
			if (pointer) this.applyDragPosition(pointer.x, pointer.y);
		});
	}

	endDrag() {
		if (!this.dragging) return;
		if (this.dragFrame !== null && window.cancelAnimationFrame) window.cancelAnimationFrame(this.dragFrame);
		this.dragFrame = null;
		const pointer = this.dragPointer;
		this.dragPointer = null;
		if (pointer) this.applyDragPosition(pointer.x, pointer.y);
		this.dragging = false;
		this.panelDragState = null;
		this.dragSource = null;
		this.root?.classList?.remove("tp-softphone-dragging");
		document.removeEventListener("pointermove", this.boundDragMove);
		document.removeEventListener("pointerup", this.boundDragEnd);
		this.saveLayoutPrefs();
	}

	initDrag() {
		const header = this.root?.querySelector(".tp-softphone-header");
		if (header) {
			header.style.cursor = "move";
			header.addEventListener("pointerdown", (event) => {
				if (event.target.closest("button")) return;
				this.startDrag(event, "panel");
			});
		}
		if (this.button) {
			this.button.style.cursor = "move";
			this.button.addEventListener("pointerdown", (event) => {
				if (event.target.closest(".tp-softphone-toggle-inline-btn")) return;
				this.startDrag(event, "toggle");
			});
		}
	}

	measurePanelContentHeight() {
		if (!this.panelContent) return 0;
		const inner = this.panelContent.querySelector?.(".tp-softphone-inner") || null;
		const previous = {
			height: this.panelContent.style.height,
			transform: this.panelContent.style.transform,
			origin: this.panelContent.style.transformOrigin,
			width: this.panelContent.style.width,
			innerHeight: inner?.style?.height || "",
		};
		this.panelContent.style.transform = "none";
		this.panelContent.style.transformOrigin = "center center";
		this.panelContent.style.width = `${TELEPHONY_SOFTPHONE_WIDTH}px`;
		this.panelContent.style.height = "auto";
		if (inner?.style) inner.style.height = "auto";
		const height = Math.ceil(this.panelContent.scrollHeight || 0);
		this.panelContent.style.height = previous.height;
		this.panelContent.style.transform = previous.transform;
		this.panelContent.style.transformOrigin = previous.origin;
		this.panelContent.style.width = previous.width;
		if (inner?.style) inner.style.height = previous.innerHeight;
		return height;
	}

	setPanelSizeFromViewport() {
		if (!this.panel || !this.panelContent) return;
		const fullContentView = this.view === "history" || this.view === "contacts" || this.view === "settings";
		const actionRowFallback = Math.max(0, Math.max(1, this.visibleActionRows) - 1) * TELEPHONY_SOFTPHONE_ACTION_ROW_HEIGHT;
		const measuredHeight = fullContentView ? 0 : this.measurePanelContentHeight();
		const fallbackHeight = TELEPHONY_SOFTPHONE_COMPACT_HEIGHT + actionRowFallback;
		const dialerHeight = measuredHeight > 0 ? measuredHeight : fallbackHeight;
		// Dialer/transfer states are content-driven. Blind/attended transfer can add
		// an input, keypad, and extra action rows, so clipping them to the fixed
		// History/Contacts canvas would hide controls. Oversized states are scaled
		// by the viewport calculation below instead of being cropped.
		const visibleHeight = fullContentView ? TELEPHONY_SOFTPHONE_HEIGHT : dialerHeight;
		const maxHeight = window.innerHeight * TELEPHONY_SOFTPHONE_VIEWPORT_RATIO || visibleHeight;
		const baseMargin = 0;
		const heightScale = maxHeight / (visibleHeight + baseMargin * 2);
		const availableWidth = Math.max((window.innerWidth || TELEPHONY_SOFTPHONE_WIDTH) - TELEPHONY_WIDGET_VIEWPORT_MARGIN * 2, 1);
		const widthScale = availableWidth / (TELEPHONY_SOFTPHONE_WIDTH + baseMargin * 2);
		let scale = Math.min(heightScale, widthScale);
		scale = Math.min(1, Math.max(TELEPHONY_SOFTPHONE_MIN_SCALE, scale));
		const margin = baseMargin * scale;
		const panelWidth = TELEPHONY_SOFTPHONE_WIDTH * scale + margin * 2;
		const panelHeight = visibleHeight * scale + margin * 2;
		Object.assign(this.panel.style, {
			width: `${panelWidth}px`, height: `${panelHeight}px`, maxWidth: `${panelWidth}px`, maxHeight: `${panelHeight}px`,
		});
		Object.assign(this.panelContent.style, {
			width: `${TELEPHONY_SOFTPHONE_WIDTH}px`, height: `${visibleHeight}px`, transformOrigin: "center center", transform: `scale(${scale})`,
		});
		this.positionPanelAroundToggle();
	}

	handleViewportResize() {
		this.setPanelSizeFromViewport();
		this.clampToViewport();
		this.positionPanelAroundToggle();
	}

	updateToggleLayoutForCall() {
		if (!this.button) return;
		const panelOpen = this.$panel.hasClass("tp-open");
		const handsetOwner = this.currentCall?.handset?.status === "owner";
		const active = Boolean(this.durationTimer && handsetOwner && !panelOpen);
		const wasActive = this.$toggle.hasClass("tp-softphone-toggle-active");
		this.$toggle.toggleClass("tp-softphone-toggle-active", active);
		if (active !== wasActive) this.clampToViewport();
	}

	toggleMute() {
		if (this.currentCall?.state !== "connected" || this.currentCall.handset?.status !== "owner" || !this.mediaRunning) return;
		this.muted = !this.muted;
		this.applyMuteState();
		this.refresh();
	}

	applyMuteState() {
		for (const track of this.localStream?.getAudioTracks?.() || []) track.enabled = !this.muted;
	}

	audioConstraints() {
		const audio = {
			channelCount: { ideal: 1 },
			echoCancellation: { ideal: this.audioProcessing },
			noiseSuppression: { ideal: this.audioProcessing },
			autoGainControl: { ideal: this.audioProcessing },
		};
		if (this.preferredMicId) audio.deviceId = { exact: this.preferredMicId };
		return { audio, video: false };
	}

	async refreshDevices() {
		if (!navigator.mediaDevices?.enumerateDevices) return;
		try {
			const devices = await navigator.mediaDevices.enumerateDevices();
			this.devices.inputs = devices.filter((device) => device.kind === "audioinput");
			this.devices.outputs = devices.filter((device) => device.kind === "audiooutput");
			if (this.preferredMicId && !this.devices.inputs.some((device) => device.deviceId === this.preferredMicId)) {
				this.preferredMicId = "";
				localStorage.removeItem(TELEPHONY_MIC_STORAGE_KEY);
			}
			if (this.preferredSpeakerId && !this.devices.outputs.some((device) => device.deviceId === this.preferredSpeakerId)) {
				this.preferredSpeakerId = "";
				localStorage.removeItem(TELEPHONY_SPEAKER_STORAGE_KEY);
			}
			const render = ($select, values, selected, fallback) => {
				$select.empty();
				$("<option>").val("").text(__("System default")).appendTo($select);
				values.forEach((device, index) => {
					$("<option>").val(device.deviceId).text(device.label || `${fallback} ${index + 1}`).appendTo($select);
				});
				$select.val(selected || "");
			};
			render(this.$microphone, this.devices.inputs, this.preferredMicId, __("Microphone"));
			render(this.$speaker, this.devices.outputs, this.preferredSpeakerId, __("Speaker"));
			const AudioContextClass = window.AudioContext || window.webkitAudioContext;
			const sinkSupported = typeof AudioContextClass?.prototype?.setSinkId === "function";
			this.$speaker.prop("disabled", !sinkSupported || !this.devices.outputs.length);
		} catch (_) {}
	}

	async toggleDevices() {
		this.toggleDiagnostics(true);
		await this.refreshDevices();
	}

	async changeMicrophone(deviceId) {
		this.preferredMicId = deviceId || "";
		if (this.preferredMicId) localStorage.setItem(TELEPHONY_MIC_STORAGE_KEY, this.preferredMicId);
		else localStorage.removeItem(TELEPHONY_MIC_STORAGE_KEY);
		await this.discardPreparedMicrophone();
		await this.restartMediaForDeviceChange();
	}

	async changeSpeaker(deviceId) {
		this.preferredSpeakerId = deviceId || "";
		if (this.preferredSpeakerId) localStorage.setItem(TELEPHONY_SPEAKER_STORAGE_KEY, this.preferredSpeakerId);
		else localStorage.removeItem(TELEPHONY_SPEAKER_STORAGE_KEY);
		await this.applySpeakerSelection();
	}

	async changeAudioProcessing(enabled) {
		this.audioProcessing = Boolean(enabled);
		localStorage.setItem(TELEPHONY_AUDIO_PROCESSING_KEY, this.audioProcessing ? "1" : "0");
		await this.discardPreparedMicrophone();
		await this.restartMediaForDeviceChange();
	}

	async applySpeakerSelection() {
		const context = this.audioContext || this.preparedAudioContext;
		if (!context || typeof context.setSinkId !== "function") return false;
		try {
			await context.setSinkId(this.preferredSpeakerId || "");
			return true;
		} catch (error) {
			this.$note.text(error.message || __("Could not select the audio output device."));
			return false;
		}
	}

	async restartMediaForDeviceChange() {
		if (this.currentCall?.state !== "connected" || !this.mediaRunning) return;
		const callId = this.currentCall.call_id;
		try {
			this.$note.text(__("Switching audio device…"));
			await this.stopMedia();
			await this.startMedia(callId);
		} catch (error) {
			this.receiveError(error.message || String(error));
		}
	}

	async runPreflight() {
		const AudioContextClass = window.AudioContext || window.webkitAudioContext;
		const checks = [];
		checks.push({ name: __("Secure browser context"), ok: Boolean(window.isSecureContext), detail: window.isSecureContext ? __("Ready") : __("HTTPS is required") });
		checks.push({ name: __("Microphone API"), ok: Boolean(navigator.mediaDevices?.getUserMedia), detail: navigator.mediaDevices?.getUserMedia ? __("Available") : __("Unavailable") });
		checks.push({ name: __("AudioWorklet"), ok: Boolean(AudioContextClass && window.AudioWorkletNode), detail: AudioContextClass && window.AudioWorkletNode ? __("Available") : __("Unavailable") });
		checks.push({ name: __("Voice worker"), ok: typeof Worker === "function", detail: typeof Worker === "function" ? __("Available") : __("Unavailable") });
		checks.push({ name: __("Frappe Socket.IO"), ok: this.controlReady, detail: this.controlReady ? __("Connected") : __("Connecting") });
		const accountStates = Object.values(this.registration || {});
		const registered = accountStates.some((value) => value === "registered");
		checks.push({ name: __("SIP registration"), ok: registered, detail: registered ? __("Registered") : __("Not registered") });
		try {
			const permission = await navigator.permissions?.query?.({ name: "microphone" });
			if (permission) checks.push({ name: __("Microphone permission"), ok: permission.state !== "denied", detail: permission.state });
		} catch (_) {}
		const sinkSupported = typeof AudioContextClass?.prototype?.setSinkId === "function";
		checks.push({ name: __("Speaker selection"), ok: sinkSupported, optional: true, detail: sinkSupported ? __("Supported") : __("Browser default only") });
		this.preflightChecks = checks;
		this.renderPreflight();
		return checks;
	}

	renderPreflight() {
		this.renderSettingsReadiness();
		if (this.view === "settings") this.renderDiagnostics();
	}

	renderSettingsReadiness() {
		const checks = this.preflightChecks || [];
		const readyCount = checks.filter((check) => check.ok).length;
		const limitedCount = checks.filter((check) => !check.ok && check.optional).length;
		const issueCount = checks.filter((check) => !check.ok && !check.optional).length;
		let summary = checks.length ? __("{0} ready", [readyCount]) : __("Checking…");
		if (issueCount) summary += ` · ${__("{0} need attention", [issueCount])}`;
		if (limitedCount) summary += ` · ${__("{0} limited", [limitedCount])}`;
		this.$readinessSummary?.text?.(summary);

		const renderRows = ($target, rows) => {
			if (!$target?.length) return;
			$target.empty();
			for (const check of rows) {
				const state = check.ok ? "ok" : (check.optional ? "neutral" : "bad");
				const $row = $("<div>", { class: `tp-softphone-readiness-row is-${state}` });
				$("<span>", { class: "tp-softphone-readiness-dot", "aria-hidden": "true" }).appendTo($row);
				const $copy = $("<div>", { class: "tp-softphone-readiness-copy" }).appendTo($row);
				$("<strong>").text(check.name || __("Check")).appendTo($copy);
				$("<span>").text(check.detail || "").appendTo($copy);
				$row.appendTo($target);
			}
		};

		const preferredNames = new Set([
			__("Frappe Socket.IO"),
			__("SIP registration"),
			__("Microphone permission"),
			__("Speaker selection"),
		]);
		let primary = checks.filter((check) => preferredNames.has(check.name));
		if (!primary.some((check) => check.name === __("Microphone permission"))) {
			const microphoneApi = checks.find((check) => check.name === __("Microphone API"));
			if (microphoneApi) primary.splice(Math.min(2, primary.length), 0, microphoneApi);
		}
		renderRows(this.$readinessPrimary, primary.slice(0, 4));
		renderRows(this.$readinessList, checks);
	}

	toggleDiagnostics(forceOpen = null) {
		const opening = forceOpen === null ? this.view !== "settings" : Boolean(forceOpen);
		if (opening) this.setSoftphoneView("settings");
		else if (this.view === "settings") this.setSoftphoneView("dialer");
	}

	renderDiagnostics() {
		if (!this.$diagnosticsJson?.length) return;
		const media = this.mediaStats || {};
		const playback = this.playbackStats || {};
		const server = this.serverMediaStatus || {};
		const sip = server.sip || {};
		const bridge = server.bridge || {};
		const track = this.localStream?.getAudioTracks?.()[0];
		const settings = track?.getSettings?.() || {};
		const snapshot = {
			transport: {
				socketio: this.controlReady ? "connected" : "disconnected",
				rtt_ms: this.transportRttMs,
				buffered_bytes: media.buffered_bytes ?? 0,
				media_paused: Boolean(media.media_paused),
			},
			sip: {
				registration: this.registration?.registration || null,
				codec: sip.codec || null,
				rtp_streams: sip.rtp_streams ?? null,
				input_buffer_bytes: sip.input_buffer_bytes ?? null,
				output_buffer_bytes: sip.output_buffer_bytes ?? null,
			},
			browser_media: {
				sent: media.sent ?? 0,
				received: media.received ?? 0,
				dropped: media.dropped ?? 0,
				capture_p95_ms: media.capture_p95_ms ?? null,
				capture_max_ms: media.capture_max_ms ?? null,
				receive_p95_ms: media.receive_p95_ms ?? null,
				receive_max_ms: media.receive_max_ms ?? null,
				jitter_ms: media.jitter_ms ?? null,
				jitter_target_ms: media.jitter_target_ms ?? null,
				sequence_gaps: media.sequence_gaps ?? 0,
				protocol_errors: media.protocol_errors ?? 0,
				media_clock_hz: media.media_clock_hz ?? null,
			},
			playback: {
				queued_ms: playback.queuedMs ?? 0,
				target_delay_ms: playback.targetDelayMs ?? 0,
				underruns: playback.underruns ?? 0,
				dropped_frames: playback.droppedFrames ?? 0,
				playout_ratio: playback.playoutRatio ?? 1,
				correction_mode: playback.correctionMode || "normal",
				preemptive_expands: playback.preemptiveExpands ?? 0,
				accelerations: playback.accelerations ?? 0,
				plc_events: playback.plcEvents ?? 0,
				plc_ms: playback.plcMs ?? 0,
				media_gap_events: playback.mediaGapEvents ?? 0,
				media_gap_ms: playback.mediaGapMs ?? 0,
				gap_silence_ms: playback.gapSilenceMs ?? 0,
				pending_gap_ms: playback.pendingGapMs ?? 0,
				provisional_outage_ms: playback.provisionalOutageMs ?? 0,
				credited_gap_ms: playback.creditedGapMs ?? 0,
				output_sample_rate: playback.outputSampleRate || this.audioContext?.sampleRate || null,
			},
			bridge: {
				browser_queue: bridge.browser_queue_length ?? null,
				browser_queue_max: bridge.browser_queue_max ?? null,
				sip_frames_read: bridge.sip_frames_read ?? null,
				sip_frames_written: bridge.sip_frames_written ?? null,
				sip_frames_dropped: bridge.sip_frames_dropped ?? null,
				outbound_media_dropped: server.outbound_media_dropped ?? 0,
			},
			microphone: {
				label: track?.label || null,
				sample_rate: settings.sampleRate || null,
				echo_cancellation: settings.echoCancellation ?? null,
				noise_suppression: settings.noiseSuppression ?? null,
				auto_gain_control: settings.autoGainControl ?? null,
			},
			media_startup: this.lastMediaStartupTiming ? {
				connected_to_start_ms: this.lastMediaStartupTiming.connected_to_start_ms ?? null,
				stop_media_ms: this.lastMediaStartupTiming.stop_media_ms ?? null,
				prime_audio_ms: this.lastMediaStartupTiming.prime_audio_ms ?? null,
				microphone_prepared: this.lastMediaStartupTiming.microphone_prepared ?? null,
				prewarm_get_user_media_ms: this.lastMediaStartupTiming.prewarm_get_user_media_ms ?? null,
				get_user_media_ms: this.lastMediaStartupTiming.get_user_media_ms ?? null,
				worklets_ms: this.lastMediaStartupTiming.worklets_ms ?? null,
				graph_ms: this.lastMediaStartupTiming.graph_ms ?? null,
				realtime_attach_ms: this.lastMediaStartupTiming.realtime_attach_ms ?? null,
				microphone_attach_ms: this.lastMediaStartupTiming.microphone_attach_ms ?? null,
				total_ms: this.lastMediaStartupTiming.total_ms ?? null,
			} : null,
			preflight: this.preflightChecks.map((check) => ({
				name: check.name,
				ok: Boolean(check.ok),
				optional: Boolean(check.optional),
				detail: check.detail || "",
			})),
		};
		this.renderQuality();
		const qualityGlance = String(this.$quality?.text?.() || "").trim();
		const transportGlance = snapshot.transport.socketio === "connected" ? __("Connected") : __("Disconnected");
		const sipGlance = snapshot.sip.registration === "registered" ? __("Registered") : (snapshot.sip.registration || __("Unknown"));
		this.$diagnosticsGlance?.text?.(qualityGlance || `${transportGlance} · ${sipGlance}`);
		if (this.$diagnosticsSummary?.length) {
			this.$diagnosticsSummary.empty();
			const rows = [
				[__("Transport"), snapshot.transport.socketio === "connected" ? __("Connected") : __("Disconnected"), snapshot.transport.socketio === "connected"],
				[__("SIP"), snapshot.sip.registration || __("Unknown"), snapshot.sip.registration === "registered"],
				[__("Codec"), snapshot.sip.codec || "—", Boolean(snapshot.sip.codec)],
				[__("RTT"), snapshot.transport.rtt_ms == null ? "—" : `${Math.round(Number(snapshot.transport.rtt_ms) || 0)} ms`, snapshot.transport.rtt_ms == null || Number(snapshot.transport.rtt_ms) < 300],
				[__("Media"), this.mediaRunning ? __("Running") : (this.currentCall?.state === "held" ? __("Held") : __("Idle")), this.mediaRunning || !this.currentCall],
			];
			for (const [label, value, healthy] of rows) {
				const $row = $("<div>", { class: `tp-softphone-diagnostic-row${healthy ? " is-ok" : ""}` });
				$("<span>").text(label).appendTo($row);
				$("<strong>").text(value).appendTo($row);
				$row.appendTo(this.$diagnosticsSummary);
			}
		}
		this.$diagnosticsJson.text(JSON.stringify(snapshot, null, 2));
	}

	renderQuality() {
		if (!this.$quality?.length) return;
		const state = this.currentCall?.state;
		if (!["connected", "held"].includes(state)) {
			this.$quality.attr("class", "tp-softphone-quality-inline").text("");
			return;
		}
		const sip = this.serverMediaStatus?.sip || {};
		const bridge = this.serverMediaStatus?.bridge || {};
		const media = this.mediaStats || {};
		const playback = this.playbackStats || {};
		const codec = sip.codec ? ` · ${sip.codec}` : "";
		let level = "good";
		let label = __("Good");
		if (state === "held" || !this.mediaRunning) {
			level = "warn";
			label = state === "held" ? __("Held") : __("Reconnecting");
		} else {
			const bad = Number(playback.underruns || 0) > 2 || Number(media.dropped || 0) > 5 || Number(bridge.sip_frames_dropped || 0) > 2 || Number(media.jitter_ms || 0) > 35 || Number(this.transportRttMs || 0) > 300;
			const warn = Number(playback.underruns || 0) > 0 || Number(media.dropped || 0) > 0 || Number(bridge.sip_frames_dropped || 0) > 0 || Number(media.jitter_ms || 0) > 18 || Number(media.receive_p95_ms || 0) > 35 || Number(this.transportRttMs || 0) > 180;
			if (bad) { level = "bad"; label = __("Poor"); }
			else if (warn) { level = "warn"; label = __("Fair"); }
		}
		this.$quality.attr("class", `tp-softphone-quality-inline ${level}`).text(`${label}${codec}`);
	}

	isTransferTargetValid(target) {
		const value = String(target || "").trim();
		if (!value || /[\r\n]/.test(value)) return false;
		if (/^[0-9+*#]{1,64}$/.test(value)) return true;
		if (/^sips?:[^\s<>]{1,120}$/i.test(value)) return true;
		return /^[^@\s<>]+@[^@\s<>]+$/.test(value) && value.length <= 120;
	}

	startBlindTransfer() {
		if (this.blindTransfer) {
			this.completeBlindTransfer();
			return;
		}
		const call = this.currentCall;
		if (
			!call?.call_id
			|| !["connected", "held"].includes(call.state)
			|| call.handset?.status !== "owner"
			|| !this.capabilities.transfer
			|| this.transferPending
			|| this.attendedTransfer
		) return;

		const alreadyHeld = call.state === "held";
		this.blindTransfer = {
			callId: call.call_id,
			autoHeld: !alreadyHeld,
			waitingForHold: !alreadyHeld,
		};
		this.clearDialInput();
		this.ensureKeypadVisible();
		if (alreadyHeld) {
			this.$note.text(__("Enter number to transfer to, then press Transfer again."));
		} else {
			this.voiceWorker?.postMessage({ type: "hold", callId: call.call_id });
			this.$note.text(__("Putting the caller on hold before transfer…"));
		}
		this.refresh();
	}

	completeBlindTransfer() {
		const transfer = this.blindTransfer;
		const call = this.currentCall;
		if (
			!transfer
			|| transfer.waitingForHold
			|| this.transferPending
			|| !call?.call_id
			|| call.call_id !== transfer.callId
			|| call.state !== "held"
		) return;
		const target = String(this.$number.val() || "").trim();
		if (!this.isTransferTargetValid(target)) {
			frappe.show_alert({ message: __("Enter a valid extension, phone number, or SIP URI."), indicator: "orange" });
			return;
		}
		transfer.target = target;
		this.transferPending = true;
		this.voiceWorker?.postMessage({ type: "transfer", callId: transfer.callId, target });
		this.$note.text(__("Transferring call…"));
		this.refresh();
	}

	async cancelBlindTransfer() {
		const transfer = this.blindTransfer;
		if (!transfer || this.transferPending) return;
		this.blindTransfer = null;
		const call = this.currentCall?.call_id === transfer.callId ? this.currentCall : null;
		if (transfer.autoHeld && call?.state === "held" && call.handset?.status === "owner") {
			if (!this.mediaRunning) await this.primeAudio();
			this.voiceWorker?.postMessage({ type: "resume", callId: call.call_id });
			this.$note.text(__("Returning to the call…"));
		} else {
			this.$note.text(__("Transfer cancelled."));
		}
		this.refresh();
	}

	attendedTransferAction() {
		if (this.attendedTransfer?.state === "consulting") {
			this.completeAttendedTransfer();
			return;
		}
		if (this.attendedTransfer) {
			void this.cancelAttendedTransfer();
			return;
		}
		const call = this.currentCall;
		if (
			!call?.call_id
			|| !["connected", "held"].includes(call.state)
			|| call.handset?.status !== "owner"
			|| !this.capabilities.attended_transfer
			|| this.transferPending
			|| this.blindTransfer
		) return;

		const alreadyHeld = call.state === "held";
		this.attendedTransfer = {
			state: "preparing",
			originalCallId: call.call_id,
			consultCallId: null,
			waitingForHold: !alreadyHeld,
			pendingDial: false,
			wasHeld: alreadyHeld,
		};
		this.clearDialInput();
		this.ensureKeypadVisible();
		if (alreadyHeld) {
			this.$note.text(__("Enter number then press Call to consult."));
		} else {
			this.voiceWorker?.postMessage({ type: "hold", callId: call.call_id });
			this.$note.text(__("Putting the caller on hold before the consultation call…"));
		}
		this.refresh();
	}

	async startAttendedConsultCall() {
		const transfer = this.attendedTransfer;
		const original = this.currentCall;
		if (
			transfer?.state !== "preparing"
			|| transfer.waitingForHold
			|| transfer.pendingDial
			|| !original?.call_id
			|| original.call_id !== transfer.originalCallId
			|| original.state !== "held"
			|| original.handset?.status !== "owner"
		) return;

		const target = String(this.$number.val() || "").trim();
		if (!/^[0-9+*#]{1,64}$/.test(target)) {
			frappe.show_alert({ message: __("Enter a valid extension or phone number for the consultation call."), indicator: "orange" });
			return;
		}

		transfer.target = target;
		transfer.pendingDial = true;
		this.parkedCall = original;
		this.currentCall = null;
		this.stopDuration();
		await this.stopMedia();
		try {
			await this.primeAudio();
			this.voiceWorker?.postMessage({
				type: "dial",
				number: target,
			});
			this.$note.text(__("Calling the transfer target for consultation…"));
		} catch (error) {
			transfer.pendingDial = false;
			this.currentCall = original;
			this.parkedCall = null;
			throw error;
		} finally {
			this.refresh();
		}
	}

	completeAttendedTransfer() {
		const transfer = this.attendedTransfer;
		if (transfer?.state !== "consulting" || this.transferPending) return;
		const calls = [this.currentCall, this.parkedCall].filter(Boolean);
		const original = calls.find((call) => call.call_id === transfer.originalCallId) || null;
		const consult = calls.find((call) => call.call_id === transfer.consultCallId) || null;
		if (
			!original
			|| !consult
			|| !["connected", "held"].includes(original.state)
			|| !["connected", "held"].includes(consult.state)
			|| original.handset?.status !== "owner"
			|| consult.handset?.status !== "owner"
		) {
			frappe.show_alert({ message: __("Attended transfer requires both calls to remain connected."), indicator: "orange" });
			return;
		}
		this.transferPending = true;
		this.voiceWorker?.postMessage({
			type: "attended_transfer",
			callId: transfer.originalCallId,
			consultCallId: transfer.consultCallId,
		});
		this.$note.text(__("Completing attended transfer…"));
		this.refresh();
	}

	async cancelAttendedTransfer({ skipConsultHangup = false } = {}) {
		const transfer = this.attendedTransfer;
		if (!transfer || this.transferPending) return;
		const calls = [this.currentCall, this.parkedCall].filter(Boolean);
		const original = calls.find((call) => call.call_id === transfer.originalCallId) || null;
		const consult = transfer.consultCallId
			? calls.find((call) => call.call_id === transfer.consultCallId) || null
			: null;

		if (!skipConsultHangup && consult?.call_id && !["ended", "failed", "disconnecting"].includes(consult.state)) {
			this.voiceWorker?.postMessage({ type: "hangup", callId: consult.call_id });
		}
		this.attendedTransfer = null;
		this.transferPending = false;
		if (!original) {
			this.currentCall = null;
			this.parkedCall = null;
			this.refresh();
			return;
		}

		if (this.currentCall?.call_id !== original.call_id) {
			await this.stopMedia();
			this.currentCall = original;
		}
		this.parkedCall = null;
		if (!transfer.wasHeld && original.state === "held" && original.handset?.status === "owner") {
			await this.primeAudio();
			this.voiceWorker?.postMessage({ type: "resume", callId: original.call_id });
			this.$note.text(__("Returning to the original call…"));
		} else if (original.state === "connected" && original.handset?.status === "owner") {
			await this.primeAudio();
			void this.startMedia(original.call_id).catch((error) => this.receiveError(error.message || String(error)));
			this.$note.text(__("Attended transfer cancelled."));
		} else {
			this.$note.text(__("Attended transfer cancelled. The original call remains on hold."));
		}
		this.refresh();
	}

	async cancelAnyTransfer() {
		if (this.blindTransfer) {
			await this.cancelBlindTransfer();
			return;
		}
		if (this.attendedTransfer) await this.cancelAttendedTransfer();
	}

	receiveTransferResult(data) {
		const result = data?.result || {};
		this.transferPending = false;
		const attended = result.mode === "attended";
		if (result.completed) {
			if (attended) {
				const transfer = this.attendedTransfer;
				if (transfer?.originalCallId) this.transferredCallIds.add(transfer.originalCallId);
				if (transfer?.consultCallId) this.transferredCallIds.add(transfer.consultCallId);
				this.attendedTransfer = null;
			} else {
				const transferCallId = this.blindTransfer?.callId || data?.callId || null;
				if (transferCallId) this.transferredCallIds.add(transferCallId);
				this.blindTransfer = null;
			}
			this.$note.text(attended ? __("Attended transfer completed.") : __("Transfer completed."));
			frappe.show_alert({ message: this.$note.text(), indicator: "green" });
			this.refresh();
			return;
		}

		const status = result.status_code
			? `${result.status_code}${result.phrase ? ` ${result.phrase}` : ""}`
			: (result.phrase || __("Transfer failed"));
		this.$note.text(`${__("Transfer failed")}: ${status}`);
		frappe.show_alert({ message: this.$note.text(), indicator: "red" });
		if (!attended && this.blindTransfer) {
			const transfer = this.blindTransfer;
			this.blindTransfer = null;
			if (transfer.autoHeld && this.currentCall?.call_id === transfer.callId && this.currentCall.state === "held") {
				void this.primeAudio().then(() => {
					if (this.currentCall?.call_id === transfer.callId && this.currentCall.state === "held") {
						this.voiceWorker?.postMessage({ type: "resume", callId: transfer.callId });
					}
				});
			}
		}
		this.refresh();
	}

	async recoverAttendedTransferAfterLegEnded(endedCallId, remainingCall) {
		const transfer = this.attendedTransfer;
		if (!transfer || ![transfer.originalCallId, transfer.consultCallId].includes(endedCallId)) return false;
		if (this.transferPending) {
			this.$note.text(__("Completing attended transfer…"));
			return true;
		}

		const originalEnded = endedCallId === transfer.originalCallId;
		this.attendedTransfer = null;
		this.transferPending = false;
		if (!remainingCall || ["ended", "failed"].includes(remainingCall.state)) {
			this.$note.text(originalEnded ? __("The original caller disconnected.") : __("Consultation call ended."));
			return true;
		}

		if (this.currentCall?.call_id !== remainingCall.call_id) {
			await this.stopMedia();
			this.currentCall = remainingCall;
		}
		this.parkedCall = null;
		this.startDuration();

		if (remainingCall.state === "held" && remainingCall.handset?.status === "owner") {
			await this.primeAudio();
			this.voiceWorker?.postMessage({ type: "resume", callId: remainingCall.call_id });
			this.$note.text(originalEnded
				? __("The original caller disconnected. Returning to the consultation call…")
				: __("Consultation call ended. Returning to the original caller…"));
		} else if (remainingCall.state === "connected" && remainingCall.handset?.status === "owner") {
			await this.primeAudio();
			void this.startMedia(remainingCall.call_id).catch((error) => this.receiveError(error.message || String(error)));
			this.$note.text(originalEnded
				? __("The original caller disconnected. The consultation call is still connected.")
				: __("Consultation call ended. The original call is still connected."));
		}
		this.refresh();
		return true;
	}

	async answerWaiting() {
		const target = this.waitingCall;
		if (!target?.call_id || !this.voiceWorker || this.pendingCallSwitch || ["observer", "unavailable"].includes(target.handset?.status)) return;
		const current = this.currentCall;
		if (current && current.call_id !== target.call_id && !["ended", "failed"].includes(current.state)) {
			if (current.handset?.status !== "owner") return;
			if (current.state === "connected") {
				this.pendingCallSwitch = {
					kind: "answer_waiting",
					fromCallId: current.call_id,
					targetCallId: target.call_id,
				};
				this.voiceWorker.postMessage({ type: "hold", callId: current.call_id });
				this.$note.text(__("Putting the current call on hold before answering…"));
				this.refresh();
				return;
			}
			if (current.state !== "held") return;
		}
		await this._activateWaitingCall(target, current);
	}

	async _activateWaitingCall(target, previous) {
		if (!target?.call_id || this.waitingCall?.call_id !== target.call_id) return;
		if (previous?.call_id && previous.call_id !== target.call_id) {
			this.parkedCall = previous;
			await this.stopMedia();
			this.stopDuration();
		}
		this.currentCall = target;
		this.waitingCall = null;
		this.stopRinging();
		await this.primeAudio();
		this.voiceWorker?.postMessage({ type: "answer", callId: target.call_id });
		this.$note.text(__("Answering waiting call…"));
		this.refresh();
	}

	declineWaiting() {
		if (!this.waitingCall?.call_id || !this.voiceWorker) return;
		const callId = this.waitingCall.call_id;
		this.voiceWorker.postMessage({ type: "reject", callId });
		this.waitingCall = null;
		this.callSequences.delete(callId);
		if (this.currentCall?.state !== "ringing") this.stopRinging();
		this.refresh();
	}

	async swapCalls() {
		const target = this.parkedCall;
		const current = this.currentCall;
		if (!target?.call_id || !current?.call_id || !this.voiceWorker || this.pendingCallSwitch) return;
		if (target.handset?.status !== "owner" || current.handset?.status !== "owner") return;
		if (target.state !== "held") return;
		if (current.state === "connected") {
			this.pendingCallSwitch = {
				kind: "swap",
				fromCallId: current.call_id,
				targetCallId: target.call_id,
			};
			this.voiceWorker.postMessage({ type: "hold", callId: current.call_id });
			this.$note.text(__("Putting the current call on hold before switching…"));
			this.refresh();
			return;
		}
		if (current.state !== "held") return;
		await this._activateParkedCall(target, current);
	}

	async _activateParkedCall(target, previous) {
		if (!target?.call_id || this.parkedCall?.call_id !== target.call_id || target.state !== "held") return;
		await this.stopMedia();
		this.stopDuration();
		this.currentCall = target;
		this.parkedCall = previous;
		await this.primeAudio();
		this.voiceWorker?.postMessage({ type: "resume", callId: target.call_id });
		this.$note.text(__("Switching calls…"));
		this.refresh();
	}

	async completePendingCallSwitch(heldCallId) {
		const pending = this.pendingCallSwitch;
		if (!pending || pending.fromCallId !== heldCallId) return;
		const previous = this.currentCall?.call_id === heldCallId ? this.currentCall : null;
		this.pendingCallSwitch = null;
		if (!previous || previous.state !== "held") return;
		if (pending.kind === "answer_waiting") {
			const target = this.waitingCall?.call_id === pending.targetCallId ? this.waitingCall : null;
			if (target) await this._activateWaitingCall(target, previous);
			return;
		}
		if (pending.kind === "swap") {
			const target = this.parkedCall?.call_id === pending.targetCallId ? this.parkedCall : null;
			if (target) await this._activateParkedCall(target, previous);
		}
	}

	async resumeAudio() {
		if (this.currentCall?.state !== "connected" || this.currentCall.handset?.status !== "owner") return;
		try {
			await this.primeAudio();
			await this.startMedia(this.currentCall.call_id);
		} catch (error) {
			this.receiveError(error.message || String(error));
		}
	}

	async primeAudio({ requireRunning = true } = {}) {
		const AudioContextClass = window.AudioContext || window.webkitAudioContext;
		if (!AudioContextClass) throw new Error(__("Audio is unavailable in this browser."));
		if (!this.preparedAudioContext || this.preparedAudioContext.state === "closed") {
			this.preparedAudioContext = new AudioContextClass({ latencyHint: "interactive", sampleRate: 16000 });
		}
		if (this.preparedAudioContext.state !== "running") {
			try { await this.resumeAudioContext(this.preparedAudioContext, requireRunning ? 800 : 150); } catch (error) {
				if (requireRunning) throw error;
			}
		}
		await this.applySpeakerSelection();
		return this.preparedAudioContext;
	}

	_isUsableMicrophoneStream(stream) {
		const track = stream?.getAudioTracks?.()[0];
		return Boolean(track && track.readyState === "live");
	}

	async prepareMicrophone() {
		if (!navigator.mediaDevices?.getUserMedia) throw new Error(__("Microphone access is unavailable."));
		if (this._isUsableMicrophoneStream(this.preparedLocalStream)) return this.preparedLocalStream;
		if (this.preparedMicrophonePromise) return this.preparedMicrophonePromise;
		const startedAt = performance.now();
		const generation = this.preparedMicrophoneGeneration;
		let task;
		task = navigator.mediaDevices.getUserMedia(this.audioConstraints()).then((stream) => {
			if (generation !== this.preparedMicrophoneGeneration) {
				stream?.getTracks?.().forEach((track) => track.stop());
				throw new Error(__("Microphone preparation was cancelled."));
			}
			this.lastMicrophonePreparationMs = Math.max(0, performance.now() - startedAt);
			if (!this._isUsableMicrophoneStream(stream)) {
				stream?.getTracks?.().forEach((track) => track.stop());
				throw new Error(__("The selected microphone did not become active."));
			}
			this.preparedLocalStream?.getTracks?.().forEach((track) => track.stop());
			this.preparedLocalStream = stream;
			return stream;
		}).finally(() => {
			if (this.preparedMicrophonePromise === task) this.preparedMicrophonePromise = null;
		});
		this.preparedMicrophonePromise = task;
		return task;
	}

	async prepareCallAudio() {
		// Start microphone acquisition synchronously inside the click/tap handler.
		// On mobile Safari the first capture can take seconds; SIP must not become
		// connected until the local handset is actually ready to send speech.
		const microphone = this.prepareMicrophone();
		const audio = this.primeAudio();
		try {
			const [audioContext, localStream] = await Promise.all([audio, microphone]);
			return { audioContext, localStream };
		} catch (error) {
			await this.discardPreparedMicrophone();
			throw error;
		}
	}

	takePreparedMicrophone() {
		const stream = this.preparedLocalStream;
		this.preparedLocalStream = null;
		if (this._isUsableMicrophoneStream(stream)) return stream;
		stream?.getTracks?.().forEach((track) => track.stop());
		return null;
	}

	async discardPreparedMicrophone() {
		this.preparedMicrophoneGeneration += 1;
		this.preparedMicrophonePromise = null;
		const stream = this.preparedLocalStream;
		this.preparedLocalStream = null;
		stream?.getTracks?.().forEach((track) => track.stop());
	}

	async resumeAudioContext(context, timeoutMs = 800) {
		if (!context || context.state === "closed") throw new Error(__("Browser audio context is unavailable."));
		if (context.state === "running") return context;
		try {
			await Promise.race([
				context.resume(),
				new Promise((resolve) => setTimeout(resolve, timeoutMs)),
			]);
		} catch (_) {}
		if (context.state !== "running") {
			throw new Error(__("Browser audio requires a user gesture after refresh."));
		}
		return context;
	}

	waitForMediaAttach(callId, timeoutMs = 3000) {
		if (this.mediaAttachWaiter) {
			this.rejectMediaAttach(this.mediaAttachWaiter.callId, new Error(__("A newer media connection replaced the previous attempt.")));
		}
		return new Promise((resolve, reject) => {
			const timer = setTimeout(() => {
				if (this.mediaAttachWaiter?.callId !== callId) return;
				this.mediaAttachWaiter = null;
				reject(new Error(__("Telephony media connection timed out.")));
			}, timeoutMs);
			this.mediaAttachWaiter = { callId, resolve, reject, timer };
		});
	}

	resolveMediaAttach(callId) {
		const waiter = this.mediaAttachWaiter;
		if (!waiter || waiter.callId !== callId) return;
		this.mediaAttachWaiter = null;
		clearTimeout(waiter.timer);
		waiter.resolve();
	}

	rejectMediaAttach(callId, error) {
		const waiter = this.mediaAttachWaiter;
		if (!waiter || waiter.callId !== callId) return;
		this.mediaAttachWaiter = null;
		clearTimeout(waiter.timer);
		waiter.reject(error);
	}

	async startMedia(callId) {
		if ((this.mediaRunning && this.mediaCallId === callId) || this.mediaPendingCallId === callId) {
			if (this.mediaSetupPromise) await this.mediaSetupPromise;
			return;
		}
		if (this.mediaSetupPromise) {
			try { await this.mediaSetupPromise; } catch (_) {}
			if ((this.mediaRunning && this.mediaCallId === callId) || this.mediaPendingCallId === callId) return;
		}
		const task = this._startMediaOnce(callId);
		this.mediaSetupPromise = task;
		try {
			await task;
		} finally {
			if (this.mediaSetupPromise === task) this.mediaSetupPromise = null;
		}
	}

	async _startMediaOnce(callId) {
		if (!this.controlReady || !this.voiceWorker) throw new Error(__("Telephony voice WebSocket is not connected."));
		if (!navigator.mediaDevices?.getUserMedia) throw new Error(__("Microphone access is unavailable."));
		const connectedAt = this.mediaConnectedAt.get(callId) || performance.now();
		const timing = { callId, connectedAt, connected_to_start_ms: Math.max(0, performance.now() - connectedAt) };
		let stageStartedAt = performance.now();
		await this.stopMedia();
		timing.stop_media_ms = Math.max(0, performance.now() - stageStartedAt);
		const generation = this.mediaLifecycleGeneration;
		try {
			stageStartedAt = performance.now();
			const audioContext = await this.primeAudio({ requireRunning: false });
			timing.prime_audio_ms = Math.max(0, performance.now() - stageStartedAt);
			if (generation !== this.mediaLifecycleGeneration || audioContext.state === "closed") return;
			this.preparedAudioContext = null;
			this.audioContext = audioContext;
			await this.applySpeakerSelection();
			if (generation !== this.mediaLifecycleGeneration) return;

			const version = frappe.boot.developer_mode ? Date.now() : window._version_number;
			stageStartedAt = performance.now();
			await audioContext.audioWorklet.addModule(`/assets/telephony/js/telephony_capture_processor.js?v=${version}`);
			await audioContext.audioWorklet.addModule(`/assets/telephony/js/telephony_playback_processor.js?v=${version}`);
			timing.worklets_ms = Math.max(0, performance.now() - stageStartedAt);
			if (generation !== this.mediaLifecycleGeneration) return;
			stageStartedAt = performance.now();
			const canonicalSampleRate = 16000;
			const canonicalFrameSamples = 320;

			this.playbackStats = { underruns: 0, droppedFrames: 0, queuedMs: 0, targetDelayMs: 60, outputSampleRate: audioContext.sampleRate };
			this.playbackNode = new AudioWorkletNode(audioContext, "telephony-playback-processor", {
				numberOfInputs: 0,
				numberOfOutputs: 1,
				outputChannelCount: [1],
				processorOptions: { sourceSampleRate: canonicalSampleRate, targetDelayMs: 60, maxDelayMs: 200 },
			});
			this.playbackNode.port.onmessage = ({ data }) => {
				if (data?.type === "underrun") {
					this.playbackStats.underruns = data.count || this.playbackStats.underruns + 1;
					return;
				}
				if (data?.type === "playback-stats") this.playbackStats = { ...this.playbackStats, ...data };
			};
			this.playbackNode.connect(audioContext.destination);
			this.captureNode = new AudioWorkletNode(audioContext, "telephony-capture-processor", {
				numberOfInputs: 1,
				numberOfOutputs: 1,
				outputChannelCount: [1],
				processorOptions: { frameSamples: canonicalFrameSamples, targetSampleRate: canonicalSampleRate },
			});
			this.captureMute = audioContext.createGain();
			this.captureMute.gain.value = 0;
			this.captureNode.connect(this.captureMute).connect(audioContext.destination);
			if (
				generation !== this.mediaLifecycleGeneration || !this.voiceWorker || !this.controlReady
				|| this.currentCall?.call_id !== callId || this.currentCall?.handset?.status !== "owner"
			) {
				await this.stopMedia();
				return;
			}

			const captureChannel = new MessageChannel();
			const playbackChannel = new MessageChannel();
			this.captureNode.port.postMessage({ type: "connect", port: captureChannel.port1 }, [captureChannel.port1]);
			this.playbackNode.port.postMessage({ type: "connect", port: playbackChannel.port1 }, [playbackChannel.port1]);
			timing.graph_ms = Math.max(0, performance.now() - stageStartedAt);
			this.mediaPendingCallId = callId;
			const mediaAttached = this.waitForMediaAttach(callId);
			timing.mediaStartPostedAt = performance.now();
			this.mediaStartupTiming = timing;
			this.voiceWorker.postMessage({
				type: "media_start",
				capturePort: captureChannel.port2,
				playbackPort: playbackChannel.port2,
				callId,
			}, [captureChannel.port2, playbackChannel.port2]);
			this.$note.text(__("Connecting secure Frappe realtime audio…"));

			let localStream = this.takePreparedMicrophone();
			let microphoneTask;
			if (localStream) {
				timing.microphone_prepared = 1;
				timing.prewarm_get_user_media_ms = this.lastMicrophonePreparationMs ?? 0;
				timing.get_user_media_ms = 0;
				microphoneTask = Promise.resolve(localStream);
			} else {
				timing.microphone_prepared = 0;
				stageStartedAt = performance.now();
				microphoneTask = navigator.mediaDevices.getUserMedia(this.audioConstraints()).then((stream) => {
					timing.get_user_media_ms = Math.max(0, performance.now() - stageStartedAt);
					return stream;
				});
			}

			const results = await Promise.all([mediaAttached, microphoneTask]);
			localStream = results[1];
			if (generation !== this.mediaLifecycleGeneration || !this._isUsableMicrophoneStream(localStream)) {
				localStream?.getTracks?.().forEach((track) => track.stop());
				if (generation === this.mediaLifecycleGeneration) throw new Error(__("The selected microphone did not become active."));
				return;
			}
			stageStartedAt = performance.now();
			this.localStream = localStream;
			this.sourceNode = audioContext.createMediaStreamSource(localStream);
			this.sourceNode.connect(this.captureNode);
			timing.microphone_attach_ms = Math.max(0, performance.now() - stageStartedAt);
			if (audioContext.state !== "running") await this.resumeAudioContext(audioContext, 800);
			this.applyMuteState();
			void this.refreshDevices();

			timing.total_ms = Math.max(0, performance.now() - connectedAt);
			this.lastMediaStartupTiming = { ...timing };
			this.voiceWorker?.postMessage({
				type: "media_client_timing",
				callId,
				timing: {
					connected_to_start_ms: timing.connected_to_start_ms,
					stop_media_ms: timing.stop_media_ms,
					prime_audio_ms: timing.prime_audio_ms,
					microphone_prepared: timing.microphone_prepared,
					prewarm_get_user_media_ms: timing.prewarm_get_user_media_ms,
					get_user_media_ms: timing.get_user_media_ms,
					worklets_ms: timing.worklets_ms,
					graph_ms: timing.graph_ms,
					realtime_attach_ms: timing.realtime_attach_ms,
					microphone_attach_ms: timing.microphone_attach_ms,
					total_ms: timing.total_ms,
				},
			});
			if (this.mediaStartupTiming?.callId === callId) this.mediaStartupTiming = null;
		} catch (error) {
			if (generation !== this.mediaLifecycleGeneration) return;
			await this.stopMedia();
			throw error;
		}
	}

	async stopMedia({ notify = true } = {}) {
		this.mediaLifecycleGeneration += 1;
		if (this.mediaAttachWaiter) {
			this.rejectMediaAttach(this.mediaAttachWaiter.callId, new Error(__("Telephony media connection was cancelled.")));
		}
		this.mediaRunning = false;
		this.mediaCallId = null;
		this.mediaPendingCallId = null;
		if (notify) this.voiceWorker?.postMessage({ type: "media_stop" });
		for (const node of [this.sourceNode, this.captureNode, this.captureMute, this.playbackNode]) {
			try { node?.disconnect(); } catch (_) {}
		}
		this.localStream?.getTracks().forEach((track) => track.stop());
		if (this.audioContext && this.audioContext.state !== "closed") {
			try { await this.audioContext.close(); } catch (_) {}
		}
		this.sourceNode = this.captureNode = this.captureMute = this.playbackNode = null;
		this.localStream = this.audioContext = null;
		this.refresh();
	}

	async discardPreparedAudio() {
		const context = this.preparedAudioContext;
		this.preparedAudioContext = null;
		if (context && context.state !== "closed") {
			try { await context.close(); } catch (_) {}
		}
	}

	finishCall(state) {
		const endedCall = this.currentCall;
		const callId = endedCall?.call_id;
		const parked = this.parkedCall;
		if (!callId) return;
		this.stopRinging();
		this.stopRingback();
		this.stopDuration();
		void this.stopMedia();
		void this.discardPreparedAudio();
		void this.discardPreparedMicrophone();
		this.muted = false;
		this.mediaResumeRequired = false;
		this.autoRestoredCallIds.delete(callId);
		this.mediaConnectedAt.delete(callId);
		if (this.mediaStartupTiming?.callId === callId) this.mediaStartupTiming = null;

		if (this.blindTransfer?.callId === callId && !this.transferPending) this.blindTransfer = null;
		if (this.transferredCallIds.has(callId)) {
			this.transferredCallIds.delete(callId);
			this.callSequences.delete(callId);
			this.currentCall = null;
			if (parked?.call_id && !this.transferredCallIds.has(parked.call_id)) {
				this.currentCall = parked;
				this.parkedCall = null;
				this.startDuration();
				this.$note.text(__("Transfer completed. The other call remains on hold."));
			} else {
				this.$note.text(__("Transferred call released by the PBX."));
			}
			this.refresh();
			return;
		}

		const attended = this.attendedTransfer;
		if (attended && [attended.originalCallId, attended.consultCallId].includes(callId)) {
			this.currentCall = null;
			this.callSequences.delete(callId);
			if (this.transferPending) {
				this.$note.text(__("Completing attended transfer…"));
				this.refresh();
				return;
			}
			this.parkedCall = null;
			void this.recoverAttendedTransferAfterLegEnded(callId, parked);
			return;
		}

		if (state.state === "failed") {
			this.$note.text(state.failure_reason ? `${__("Call failed")}: ${state.failure_reason}` : __("Call failed."));
		} else {
			this.$note.text(__("Call ended."));
		}
		if (parked?.call_id) {
			this.currentCall = parked;
			this.parkedCall = null;
			this.callSequences.delete(callId);
			this.startDuration();
			this.$note.text(__("The other call remains on hold."));
			this.refresh();
			return;
		}
		setTimeout(() => {
			if (this.currentCall?.call_id !== callId || !["ended", "failed"].includes(this.currentCall.state)) return;
			this.currentCall = null;
			this.callSequences.delete(callId);
			this.serverMediaStatus = null;
			this.mediaStats = null;
			this.refresh();
		}, 1400);
	}

	uiAudioAsset(name) {
		return `/assets/telephony/softphone_media/${name}`;
	}

	async loadUiAudioBuffer(name, context) {
		if (!this.uiAudioBuffers.has(name)) {
			const pending = fetch(this.uiAudioAsset(name), { credentials: "same-origin" })
				.then((response) => {
					if (!response.ok) throw new Error(`Unable to load Telephony audio asset: ${name}`);
					return response.arrayBuffer();
				})
				.then((data) => context.decodeAudioData(data));
			this.uiAudioBuffers.set(name, pending);
		}
		try {
			return await this.uiAudioBuffers.get(name);
		} catch (error) {
			this.uiAudioBuffers.delete(name);
			throw error;
		}
	}

	async playKeypadTone(digit) {
		if (!/^[0-9*#]$/.test(digit)) return;
		const context = this.audioContext || await this.primeAudio({ requireRunning: false });
		if (!context || context.state === "closed") return;
		if (context.state !== "running") {
			try { await this.resumeAudioContext(context, 150); } catch (_) { return; }
		}
		const asset = digit === "*" ? "star.wav" : digit === "#" ? "hash.wav" : `${digit}.wav`;
		let buffer;
		try { buffer = await this.loadUiAudioBuffer(asset, context); } catch (_) { return; }
		try { this.keypadToneSource?.stop(); } catch (_) {}
		try { this.keypadToneSource?.disconnect(); } catch (_) {}
		try { this.keypadToneGain?.disconnect(); } catch (_) {}
		const source = context.createBufferSource();
		const gain = context.createGain();
		gain.gain.value = 0.18;
		source.buffer = buffer;
		source.connect(gain);
		gain.connect(context.destination);
		this.keypadToneSource = source;
		this.keypadToneGain = gain;
		source.onended = () => {
			if (this.keypadToneSource === source) this.keypadToneSource = null;
			if (this.keypadToneGain === gain) this.keypadToneGain = null;
			try { source.disconnect(); } catch (_) {}
			try { gain.disconnect(); } catch (_) {}
		};
		source.start();
	}

	startRingback() {
		if (this.ringbackTimer) return;
		const pulse = () => {
			const context = this.preparedAudioContext;
			if (!context || context.state === "closed" || this.currentCall?.direction !== "outgoing" || this.currentCall?.state !== "ringing") return;
			const gain = context.createGain();
			gain.gain.value = 0.035;
			gain.connect(context.destination);
			const oscillators = [440, 480].map((frequency) => {
				const oscillator = context.createOscillator();
				oscillator.frequency.value = frequency;
				oscillator.connect(gain);
				oscillator.start();
				this.ringbackNodes.add(oscillator);
				return oscillator;
			});
			setTimeout(() => {
				for (const oscillator of oscillators) {
					try { oscillator.stop(); oscillator.disconnect(); } catch (_) {}
					this.ringbackNodes.delete(oscillator);
				}
				try { gain.disconnect(); } catch (_) {}
			}, 900);
		};
		pulse();
		this.ringbackTimer = setInterval(pulse, 4000);
	}

	stopRingback() {
		if (this.ringbackTimer) clearInterval(this.ringbackTimer);
		this.ringbackTimer = null;
		for (const oscillator of this.ringbackNodes) {
			try { oscillator.stop(); oscillator.disconnect(); } catch (_) {}
		}
		this.ringbackNodes.clear();
	}

	startRinging() {
		this.stopRinging();
		const callId = this.currentCall?.call_id;
		if (!callId || this.currentCall?.direction !== "incoming" || this.currentCall?.state !== "ringing") return;
		const generation = ++this.ringtoneGeneration;
		const pending = this.startRingtoneAudio(callId, generation);
		this.ringtoneLoading = pending;
		void pending.finally(() => {
			if (this.ringtoneLoading === pending) this.ringtoneLoading = null;
		});
	}

	async startRingtoneAudio(callId, generation) {
		const context = this.audioContext || await this.primeAudio({ requireRunning: false });
		if (!context || context.state === "closed") return;
		let buffer;
		try { buffer = await this.loadUiAudioBuffer("ring.mp3", context); } catch (_) { return; }
		if (
			generation !== this.ringtoneGeneration || this.currentCall?.call_id !== callId
			|| this.currentCall?.direction !== "incoming" || this.currentCall?.state !== "ringing"
			|| this.ringtoneSource
		) return;
		if (context.state !== "running") {
			try { await this.resumeAudioContext(context, 150); } catch (_) { return; }
		}
		if (generation !== this.ringtoneGeneration || this.currentCall?.state !== "ringing") return;
		const source = context.createBufferSource();
		const gain = context.createGain();
		gain.gain.value = 0.28;
		source.buffer = buffer;
		source.loop = true;
		source.connect(gain);
		gain.connect(context.destination);
		this.ringtoneSource = source;
		this.ringtoneGain = gain;
		source.start();
	}

	stopRinging() {
		this.ringtoneGeneration += 1;
		this.ringtoneLoading = null;
		const source = this.ringtoneSource;
		const gain = this.ringtoneGain;
		this.ringtoneSource = null;
		this.ringtoneGain = null;
		try { source?.stop(); } catch (_) {}
		try { source?.disconnect(); } catch (_) {}
		try { gain?.disconnect(); } catch (_) {}
		this.$toggle?.removeClass("ringing");
	}

	syncDurationFromServer(call, source) {
		if (!call || !source) return;
		const rawDuration = source.connected_duration_seconds;
		if (rawDuration !== null && rawDuration !== undefined && rawDuration !== "") {
			const seconds = Number(rawDuration);
			if (Number.isFinite(seconds) && seconds >= 0) {
				call.connectedAt = Date.now() - Math.round(seconds * 1000);
				return;
			}
		}
		const serverConnectedAt = Date.parse(String(source.connected_at || ""));
		if (Number.isFinite(serverConnectedAt)) call.connectedAt = serverConnectedAt;
	}

	startDuration() {
		if (!this.currentCall) return;
		if (!this.currentCall.connectedAt) this.currentCall.connectedAt = Date.now();
		if (this.durationTimer) return;
		const update = () => {
			const connectedAt = this.currentCall?.connectedAt;
			if (!connectedAt) { this.$duration.text(""); this.$toggleTimer.text(""); this.$historyActiveTimer?.text?.(""); return; }
			const seconds = Math.max(0, Math.floor((Date.now() - connectedAt) / 1000));
			const hours = Math.floor(seconds / 3600);
			const minutes = Math.floor((seconds % 3600) / 60);
			const secs = seconds % 60;
			const label = hours > 0
				? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
				: `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
			this.$duration.text(label).css("visibility", "visible");
			this.$toggleTimer.text(label);
			this.$historyActiveTimer?.text?.(label);
			this.updateAttendedTransferTimers();
		};
		update();
		this.durationTimer = setInterval(update, 1000);
		this.updateToggleLayoutForCall();
	}

	stopDuration() {
		if (this.durationTimer) clearInterval(this.durationTimer);
		this.durationTimer = null;
		this.$duration.text("").css("visibility", "hidden");
		this.$toggleTimer.text("");
		this.$historyActiveTimer?.text?.("");
		this.updateToggleLayoutForCall();
	}

	prefill(number) {
		if (this.currentCall && !["ended", "failed"].includes(this.currentCall.state)) return false;
		this.setSoftphoneView("dialer");
		this.$number.val(String(number || "").trim()).trigger("input");
		this.toggle(true);
		return true;
	}
}


async function initializeTelephonyVoice() {
	if (window.telephonyVoiceSoftphone || frappe.session?.user === "Guest" || !frappe.boot?.sitename) return;
	try {
		const response = await frappe.call({ method: "telephony.sip.api.fetch_my_sip_config" });
		const config = response.message || {};
		if (!config.enabled) return;
		const identity = config.identity || {};
		const label = String(identity.extension || identity.display_name || identity.user || __("SIP"));
		const softphone = new TelephonyVoiceSoftphone({ manager: false }, [{ name: "native-sip", number: label }]);
		window.telephonyVoiceSoftphone = softphone;
		window.telephony = window.telephony || {};
		window.telephony.voice = softphone;
		window.telephony.call = (number) => softphone.prefill(number);
	} catch (error) {
		console.warn("Telephony softphone could not initialize", error);
	}
}

function scheduleTelephonyVoiceInitialization(attempt = 0) {
	if (window.telephonyVoiceSoftphone) return;
	if (!window.frappe?.boot?.sitename || !window.frappe?.session?.user) {
		if (attempt < 100) setTimeout(() => scheduleTelephonyVoiceInitialization(attempt + 1), 100);
		return;
	}
	void initializeTelephonyVoice();
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", () => scheduleTelephonyVoiceInitialization(), { once: true });
} else {
	scheduleTelephonyVoiceInitialization();
}
