frappe.ui.form.on("TP SIP Settings", {
	refresh(frm) {
		void telephony_render_local_https_status(frm);
	},

	enable_local_https(frm) {
		if (frm.doc.enable_local_https && !frm.doc.local_https_host) {
			void frm.set_value("local_https_host", window.location.hostname);
		}
		telephony_render_local_https_preview(frm);
	},

	local_https_host(frm) {
		telephony_render_local_https_preview(frm);
	},

	local_https_port(frm) {
		telephony_render_local_https_preview(frm);
	},

	after_save(frm) {
		void telephony_poll_local_https_status(frm);
	},
});

function telephony_local_https_url(frm) {
	const host = String(frm.doc.local_https_host || "").trim();
	const port = Number(frm.doc.local_https_port || 8443);
	if (!host) return "";
	const displayHost = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
	return `https://${displayHost}:${port}`;
}

function telephony_escape_html(value) {
	const div = document.createElement("div");
	div.textContent = String(value || "");
	return div.innerHTML;
}

function telephony_render_local_https_preview(frm) {
	const enabled = Boolean(frm.doc.enable_local_https);
	const url = telephony_local_https_url(frm);
	telephony_render_local_https_html(frm, {
		enabled,
		state: enabled ? "pending" : "disabled",
		message: enabled
			? __("Save TP SIP Settings to start or update the local HTTPS endpoint.")
			: __("Local HTTPS testing is disabled."),
		url,
		certificate: "pending",
	});
}

async function telephony_render_local_https_status(frm) {
	if (!frm.fields_dict.local_https_status_html) return null;
	try {
		const response = await frappe.call({
			method: "telephony.ftelephony.doctype.tp_sip_settings.tp_sip_settings.get_local_https_status",
		});
		const status = response.message || {};
		telephony_render_local_https_html(frm, status);
		return status;
	} catch (error) {
		const status = {
			enabled: Boolean(frm.doc.enable_local_https),
			state: "error",
			message: __("Unable to read local HTTPS status."),
			url: telephony_local_https_url(frm),
		};
		telephony_render_local_https_html(frm, status);
		return status;
	}
}

async function telephony_poll_local_https_status(frm) {
	for (let attempt = 0; attempt < 12; attempt += 1) {
		const status = await telephony_render_local_https_status(frm);
		if (["ready", "disabled", "error"].includes(String(status?.state || ""))) return;
		await new Promise((resolve) => window.setTimeout(resolve, 500));
	}
}

function telephony_render_local_https_html(frm, status) {
	const wrapper = frm.fields_dict.local_https_status_html?.$wrapper;
	if (!wrapper) return;
	const enabled = Boolean(status.enabled);
	const state = String(status.state || (enabled ? "starting" : "disabled"));
	const labels = {
		ready: __("Ready"),
		starting: __("Starting"),
		switching: __("Switching"),
		stopping: __("Stopping"),
		pending: __("Save required"),
		error: __("Error"),
		disabled: __("Disabled"),
	};
	const classes = {
		ready: "green", starting: "orange", switching: "orange", stopping: "orange",
		pending: "orange", error: "red", disabled: "gray",
	};
	const url = String(status.url || telephony_local_https_url(frm) || "");
	const activeUrl = String(status.active_url || "");
	const activeEndpoint = activeUrl && activeUrl !== url
		? `<div class="text-muted small mt-2">${__("Previous endpoint")}: <code>${telephony_escape_html(activeUrl)}</code></div>`
		: "";
	const secureNote = window.isSecureContext && window.location.protocol === "https:"
		? `<div class="text-muted small mt-2">${__("This Desk session is already using a secure HTTPS context. Local HTTPS testing is optional.")}</div>`
		: "";
	const warning = enabled
		? `<div class="text-muted small mt-2">${__("Testing only: the certificate is self-signed. Your browser will show a certificate warning; choose the browser option to proceed anyway.")}</div>`
		: "";
	const openButton = enabled && url
		? `<button type="button" class="btn btn-xs btn-default mt-2" data-telephony-open-local-https>${__("Open Testing Site")}</button>`
		: "";
	wrapper.html(`
		<div class="border rounded p-3" data-telephony-https-state="${telephony_escape_html(state)}">
			<div><span class="indicator-pill ${classes[state] || "gray"}">${labels[state] || state}</span></div>
			<div class="text-muted small mt-2">${telephony_escape_html(status.message || "")}</div>
			${url ? `<div class="mt-2"><code>${telephony_escape_html(url)}</code></div>` : ""}
			${activeEndpoint}
			${openButton}
			${warning}
			${secureNote}
		</div>
	`);
	wrapper.find("[data-telephony-open-local-https]").on("click", () => {
		window.open(url, "_blank", "noopener");
	});
}
