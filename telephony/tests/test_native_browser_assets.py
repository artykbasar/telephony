from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeBrowserAssetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = (ROOT / "public/js/telephony_voice.bundle.js").read_text()
        cls.worker = (ROOT / "public/js/telephony_voice_transport_worker.bundle.js").read_text()
        cls.push_worker = (ROOT / "public/js/telephony_push_worker.bundle.js").read_text()
        cls.realtime_handler = (ROOT.parent / "realtime/handlers.js").read_text()
        cls.media_dir = ROOT / "public/softphone_media"
        cls.push_avatar = cls.media_dir / "contact_avatar.png"

    def test_browser_uses_socketio_as_only_voice_transport(self):
        self.assertIn('import { io } from "socket.io-client"', self.worker)
        self.assertIn('transports: ["websocket"]', self.worker)
        self.assertIn('socket.emit(EVENT_MEDIA, frame)', self.worker)
        self.assertNotIn('socket.volatile.emit(EVENT_MEDIA', self.worker)
        self.assertNotIn('socket.volatile.emit(EVENT_MEDIA', self.realtime_handler)
        self.assertIn('socketBufferedBytes(socket) > MAX_BUFFERED_MEDIA_BYTES', self.realtime_handler)
        self.assertIn('frappe.realtime?.get_host?.', self.main)
        self.assertIn('"telephony_voice_transport_worker.bundle.js"', self.main)
        self.assertNotIn('telephony_voice_raw_transport_worker.bundle.js', self.main)
        self.assertNotIn('/telephony-voice', self.main)
        self.assertNotIn('TELEPHONY_VOICE_TRANSPORT_', self.main)
        self.assertNotIn('window.telephonyVoiceTransport', self.main)
        self.assertNotIn('new WebSocket', self.worker)
        self.assertFalse((ROOT / "public/js/telephony_voice_raw_transport_worker.bundle.js").exists())
        self.assertIn("telephony.voice.wss.auth.issue_ticket", self.realtime_handler)
        self.assertIn("ws+unix://", self.realtime_handler)
        self.assertIn('socket.on(EVENT_MEDIA', self.realtime_handler)
        self.assertIn('__("Frappe Socket.IO")', self.main)
        self.assertIn('socketio: this.controlReady ? "connected" : "disconnected"', self.main)
        self.assertNotIn('__("Telephony WSS")', self.main)
        self.assertNotIn('snapshot.transport.wss', self.main)
        self.assertNotIn('outbound_wss_dropped', self.main)

    def test_browser_never_contains_sip_registration_credentials_or_webrtc_routing(self):
        combined = (self.main + self.worker).lower()
        for forbidden in ("sip_password", "turn_servers", "stun_servers", "ice_transport_policy", "wss_uri", "jssip", "sip.js"):
            self.assertNotIn(forbidden, combined)

    def test_legacy_browser_sip_assets_are_not_shipped(self):
        for relative in (
            "public/js/sip_softphone.js",
            "public/js/softphone",
            "public/js/vendor/sip.js",
            "public/js/vendor/jssip.js",
            "public/js/vendor/jssip.min.js",
            "public/js/vendor/adapter.js",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_ui_audio_assets_are_intentionally_shipped(self):
        for name in (
            "0.wav", "1.wav", "2.wav", "3.wav", "4.wav", "5.wav", "6.wav",
            "7.wav", "8.wav", "9.wav", "hash.wav", "star.wav", "ring.mp3",
        ):
            self.assertTrue((self.media_dir / name).exists(), name)
        for name in (
            "silence.wav", "arrow-repeat.svg", "contact_avatar.svg",
            "resume_audio.svg", "transfer_back.svg",
        ):
            self.assertFalse((self.media_dir / name).exists(), name)
        self.assertTrue(self.push_avatar.exists())

    def test_keypad_audio_is_local_feedback_while_dtmf_remains_sip_control(self):
        self.assertIn("void this.playKeypadTone(digit);", self.main)
        self.assertIn('digit === "*" ? "star.wav" : digit === "#" ? "hash.wav"', self.main)
        self.assertIn('this.voiceWorker.postMessage({ type: "dtmf", callId: this.currentCall.call_id, digit });', self.main)
        self.assertIn("context.createBufferSource()", self.main)

    def test_ring_mp3_is_incoming_ringtone_only(self):
        ringtone = self.main[self.main.index("\tprepareUiAudioContext() {"):self.main.index("\n\tsyncDurationFromServer", self.main.index("\tprepareUiAudioContext() {"))]
        self.assertIn('this.loadUiAudioBuffer("ring.mp3", context)', ringtone)
        self.assertIn("source.loop = true", ringtone)
        self.assertIn('this.currentCall?.direction !== "incoming"', ringtone)
        self.assertNotIn("new Audio(", ringtone)

    def test_incoming_ringtone_uses_persistent_context_unlocked_by_trusted_gesture(self):
        self.assertIn("this.installUiAudioUnlock();", self.main)
        self.assertIn('["click", "keydown"]', self.main)
        self.assertIn("if (!event?.isTrusted) return;", self.main)
        self.assertIn("if (context.state !== \"running\") await context.resume();", self.main)
        self.assertIn("this.uiAudioReady = true", self.main)
        self.assertNotIn("audio.volume = 0", self.main)
        self.assertIn('[__("Ringtone"), snapshot.ui_audio.ready ? __("Ready") : __("Needs interaction")', self.main)
        self.assertIn('frappe.utils?.play_sound?.("alert")', self.main)

    def test_outgoing_ringback_keeps_original_synthesized_call_progress_tone(self):
        ringback = self.main[self.main.index("\tstartRingback() {"):self.main.index("\n\tprepareUiAudioContext()", self.main.index("\tstartRingback() {"))]
        self.assertIn("context.createOscillator()", ringback)
        self.assertIn("const oscillators = [440, 480]", ringback)
        self.assertIn("this.ringbackTimer = setInterval(pulse, 4000)", ringback)
        self.assertNotIn("ring.mp3", ringback)

    def test_dial_control_sends_only_number(self):
        self.assertIn('sendJson("call.dial", { number: data.number })', self.worker)
        self.assertNotIn("telephony_number", self.worker)
        self.assertNotIn("telecomNumber", self.main)

    def test_native_history_and_contacts_are_telephony_owned(self):
        self.assertIn("telephony.call_history.get_my_call_history", self.main)
        self.assertIn("telephony.contacts.get_contacts", self.main)
        self.assertIn('"TP Call Log"', self.main)
        self.assertNotIn("Telecom Call Log", self.main)

    def test_incoming_metadata_keeps_caller_name_and_ivr_route_separate(self):
        self.assertIn("call.caller_name || call.caller_id", self.main)
        self.assertIn('call?.ivr_route', self.main)
        self.assertIn('__("Caller Name")', self.main)
        self.assertIn('__("IVR Route")', self.main)

    def test_incoming_ivr_route_is_announced_in_call_state_strip(self):
        self.assertIn('call?.direction === "incoming" ? String(call?.ivr_route || "").trim() : ""', self.main)
        self.assertIn('`${baseCallStateLabel} · ${ivrAnnouncement}`', self.main)
        self.assertIn('this.$callStateLabel?.text?.(callStateLabel).attr?.("title", callStateLabel)', self.main)

    def test_contact_resolution_enriches_incoming_calls_and_history(self):
        self.assertIn("telephony.contacts.resolve_numbers", self.main)
        self.assertIn("match?.contact_name", self.main)
        self.assertIn("item.contact_name", self.main)
        self.assertIn("historyRemoteLabel", self.main)

    def test_contact_resolution_drives_active_call_identity_for_every_call_leg(self):
        self.assertIn("call.contact_name || call.party", self.main)
        self.assertIn("this.callDisplayLabel(this.currentCall", self.main)
        self.assertIn("void this.resolveCallContact(this.currentCall);", self.main)
        self.assertIn("void this.resolveCallContact(restored);", self.main)
        self.assertNotIn('if (restored.direction === "incoming") void this.resolveCallContact(restored);', self.main)

    def test_contact_rows_use_images_and_open_widget_details(self):
        self.assertIn('data-voice="contact-detail"', self.main)
        self.assertIn('data-contact-id', self.main)
        self.assertIn('this.renderContactAvatar($avatar, item)', self.main)
        self.assertIn('src: image', self.main)
        self.assertIn('this.openContactDetail', self.main)
        self.assertIn('frappe.set_route("Form", "Contact", this.contactsSelectedItem.name)', self.main)
        self.assertIn('data-contact-detail-number', self.main)
        self.assertIn('tp-softphone-contact-summary-card', self.main)
        self.assertIn('tp-softphone-contact-details-card', self.main)
        self.assertIn('tp-softphone-contact-phone-card', self.main)
        self.assertIn('tp-softphone-contact-record-card', self.main)
        self.assertIn('tp-softphone-contact-detail-link', self.main)
        self.assertIn('tp-softphone-contact-call-icon', self.main)
        self.assertIn('<use href="#icon-phone-call"></use>', self.main)
        self.assertNotIn('.text("☎").appendTo($row)', self.main)

    def test_contacts_lazy_load_twenty_per_page_on_scroll(self):
        self.assertIn('this.contactsCursor = null', self.main)
        self.assertIn('this.contactsHasMore = true', self.main)
        self.assertIn('this.$contactsList.on("scroll", () => this.maybeLoadMoreContacts())', self.main)
        self.assertIn('async loadContacts({ reset = false } = {})', self.main)
        self.assertIn('if (this.contactsCursor) args.cursor = this.contactsCursor', self.main)
        self.assertIn('this.contactsItems.push(...freshItems)', self.main)
        self.assertIn('this.appendContactRows(freshItems)', self.main)
        self.assertIn('this.contactsCursor = result.next_cursor || null', self.main)
        self.assertIn('maybeLoadMoreContacts() {', self.main)
        self.assertIn('remaining <= TELEPHONY_CONTACTS_PREFETCH_PX', self.main)
        self.assertIn('this.loadContacts({ reset: true })', self.main)

    def test_unsaved_recent_contact_creation_and_matching_stay_inside_widget(self):
        self.assertIn('__("Not saved as a contact")', self.main)
        self.assertIn('data-action": "history-create-contact"', self.main)
        self.assertIn('data-action": "history-add-existing-contact"', self.main)
        self.assertIn('data-history-contact-create-form', self.main)
        self.assertIn('data-history-contact-search', self.main)
        self.assertIn('method: "telephony.contacts.create_contact_from_call"', self.main)
        self.assertIn('method: "frappe.desk.search.search_link"', self.main)
        self.assertIn('method: "telephony.contacts.add_number_to_contact"', self.main)
        self.assertIn('this.applyHistoryContactToItem(item, contact, number)', self.main)
        self.assertNotIn('frappe.route_options = { mobile_no: number }', self.main)
        self.assertNotIn('frappe.new_doc("Contact")', self.main)
        self.assertNotIn('fieldtype: "Link", options: "Contact"', self.main)

    def test_expanded_recent_is_call_profile_with_contact_and_call_sections(self):
        self.assertIn('method: "telephony.contacts.get_contact_details"', self.main)
        self.assertIn('tp-softphone-history-summary', self.main)
        self.assertIn('tp-softphone-history-badges', self.main)
        self.assertIn('tp-softphone-history-ivr', self.main)
        self.assertIn('__("Talk time")', self.main)
        self.assertIn('__("Contact")', self.main)
        self.assertIn('data-history-contact-number', self.main)
        self.assertIn('data-action": "history-open-contact"', self.main)
        self.assertIn('__("View Contact") + " ›"', self.main)
        self.assertIn('__("Call Details")', self.main)
        self.assertIn('__("Open Call Log") + " ›"', self.main)
        self.assertIn('__("Call Back")', self.main)

    def test_recents_use_contact_photos_and_contact_buttons_dial_immediately(self):
        self.assertIn('item.contact_image || item.contact', self.main)
        self.assertIn('tp-softphone-history-identity', self.main)
        self.assertIn('image: item.contact_image || ""', self.main)
        self.assertIn('tp-softphone-history-direction-badge', self.main)
        self.assertIn('void this.callFromHistory(String($(event.currentTarget).data("contact-number") || ""));', self.main)
        self.assertIn('void this.callFromHistory(String($(event.currentTarget).data("contact-detail-number") || ""));', self.main)
        self.assertNotIn('this.prefillFromHistory(String($(event.currentTarget).data("contact-number") || ""));', self.main)
        self.assertNotIn('this.prefillFromHistory(String($(event.currentTarget).data("contact-detail-number") || ""));', self.main)

    def test_active_call_and_call_details_use_contact_photos(self):
        self.assertIn('this.renderContactAvatar(this.$partyAvatar', self.main)
        self.assertIn('call.contact_image', self.main)
        self.assertIn('tp-softphone-history-detail-identity', self.main)
        self.assertIn('tp-softphone-history-detail-avatar', self.main)
        self.assertIn('image: item.contact_image || ""', self.main)
        self.assertIn('tp-softphone-history-detail-direction-badge', self.main)

    def test_call_actions_prepare_microphone_before_sip_connects(self):
        answer = self.main.split("\tasync answer() {", 1)[1].split("\n\t}", 1)[0]
        self.assertIn("await this.prepareCallAudio();", answer)
        self.assertIn('this.voiceWorker.postMessage({ type: "answer", callId });', answer)
        self.assertLess(answer.index("await this.prepareCallAudio();"), answer.index('this.voiceWorker.postMessage({ type: "answer", callId });'))
        dial = self.main.split("\tasync dial() {", 1)[1].split("\n\t}", 1)[0]
        self.assertIn("await this.prepareCallAudio();", dial)
        self.assertLess(dial.index("await this.prepareCallAudio();"), dial.index('type: "dial"'))
        self.assertIn("async prepareMicrophone()", self.main)
        self.assertIn("navigator.mediaDevices.getUserMedia(this.audioConstraints())", self.main)

    def test_socketio_media_lifecycle_is_the_only_browser_media_path(self):
        start_media = self.main.split("\tasync startMedia(callId) {", 1)[1].split("\n\tasync _startMediaOnce", 1)[0]
        self.assertIn("const task = this._startMediaOnce(callId);", start_media)
        self.assertNotIn("_startKnownGoodDirectMediaOnce", self.main)
        self.assertNotIn("TELEPHONY_VOICE_TRANSPORT_DIRECT", self.main)
        dial = self.main.split("\tasync dial() {", 1)[1].split("\n\tasync answer()", 1)[0]
        self.assertIn("await this.prepareCallAudio();", dial)
        self.assertNotIn("await this.discardPreparedMicrophone();\n\t\t\tawait this.primeAudio();", dial)

    def test_media_bridge_attaches_before_fallback_microphone_wait(self):
        self.assertIn("this.mediaConnectedAt.set(state.call_id, performance.now())", self.main)
        media = self.main.split("\tasync _startMediaOnce(callId) {", 1)[1].split("\n\tasync stopMedia", 1)[0]
        self.assertIn('type: "media_start"', media)
        self.assertIn("let localStream = this.takePreparedMicrophone();", media)
        self.assertIn("Promise.all([mediaAttached, microphoneTask])", media)
        self.assertLess(media.index('type: "media_start"'), media.index("navigator.mediaDevices.getUserMedia(this.audioConstraints())"))
        self.assertIn("timing.prewarm_get_user_media_ms", media)
        self.assertIn("timing.microphone_attach_ms", media)
        self.assertIn('type: "media_client_timing"', media)
        self.assertIn('sendJson("media.client_timing"', self.worker)

    def test_refresh_restore_keeps_media_bridge_while_audio_context_is_suspended(self):
        ready = self.main.split('if (data.type === "media_ready") {', 1)[1].split('if (data.type === "stats")', 1)[0]
        self.assertNotIn('this.audioContext.state !== "running"', ready)
        self.assertNotIn('void this.stopMedia()', ready)
        self.assertIn('this.resolveMediaAttach(readyCallId)', ready)
        restored = self.main.split("\tasync autoResumeRestoredHandset() {", 1)[1].split("\n\treceiveState", 1)[0]
        self.assertIn("await this.primeAudio({ requireRunning: false });", restored)
        self.assertIn("await this.startMedia(call.call_id);", restored)
        media = self.main.split("\tasync _startMediaOnce(callId) {", 1)[1].split("\n\tasync stopMedia", 1)[0]
        self.assertLess(media.index("Promise.all([mediaAttached, microphoneTask])"), media.index("await this.resumeAudioContext(audioContext, 800)"))

    def test_audio_worklets_are_telephony_assets(self):
        self.assertIn("telephony_capture_processor.js", self.main)
        self.assertIn("telephony_playback_processor.js", self.main)
        self.assertIn('"telephony-capture-processor"', self.main)
        self.assertIn('"telephony-playback-processor"', self.main)

    def test_call_push_uses_standard_frappe_relay_and_telephony_worker(self):
        self.assertIn('const TELEPHONY_PUSH_PROJECT_NAME = "telephony"', self.main)
        self.assertIn('method: "telephony.push.get_call_notification_status"', self.main)
        self.assertIn('method: "telephony.push.get_call_notification_bootstrap"', self.main)
        self.assertNotIn('notification_relay.api.get_config?project_name=', self.main)
        self.assertNotIn('frappe.boot?.push_relay_server_url', self.main)
        self.assertIn('frappe.assets?.bundled_asset?.("telephony_push_worker.bundle.js")', self.main)
        self.assertIn('/api/method/frappe.push_notification.subscribe?', self.main)
        self.assertIn('/api/method/frappe.push_notification.unsubscribe?', self.main)
        self.assertIn('data-action="toggle-call-notifications"', self.main)
        self.assertIn('__("Call notifications")', self.main)
        self.assertIn('Notification.requestPermission()', self.main)
        self.assertIn('getToken(this.pushMessaging', self.main)
        self.assertNotIn('raven.api.notification', self.main)
        self.assertNotIn('Raven Push Token', self.main)
        self.assertIn('from "firebase/messaging/sw"', self.push_worker)
        self.assertIn('onBackgroundMessage(messaging', self.push_worker)
        self.assertIn('notificationAssetUrl(data.notification_icon)', self.push_worker)
        self.assertIn('notificationAssetUrl(data.notification_image)', self.push_worker)
        self.assertIn('if (image) options.image = image', self.push_worker)
        self.assertIn('payload?.notification?.title || "Incoming Call"', self.push_worker)
        self.assertIn('payload?.notification?.body || ""', self.push_worker)
        self.assertNotIn('Incoming Telephony Call', self.push_worker)
        self.assertIn('telephonyNotificationAssetUrl(data.notification_image)', self.main)
        self.assertIn('if (image) options.image = image', self.main)
        self.assertIn('payload?.notification?.title || __("Incoming Call")', self.main)
        self.assertIn('payload?.notification?.body || ""', self.main)
        self.assertTrue(self.push_avatar.exists())
        self.assertIn('self.addEventListener("notificationclick"', self.push_worker)
        self.assertIn('data.click_action || "/app"', self.push_worker)

    def test_call_push_deep_link_reopens_server_authoritative_call(self):
        self.assertIn('const TELEPHONY_PUSH_DEEP_LINK_KEY = "telephony_call"', self.main)
        self.assertIn('this.deepLinkedCallId = this.readDeepLinkedCallId()', self.main)
        self.assertIn('this.maybeOpenDeepLinkedCall(incoming)', self.main)
        self.assertIn('this.maybeOpenDeepLinkedCall(restored)', self.main)
        self.assertIn('this.toggle(true, { persist: true, focus: false })', self.main)


if __name__ == "__main__":
    unittest.main()
