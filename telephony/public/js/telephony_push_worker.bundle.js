import { initializeApp } from "firebase/app";
import { getMessaging, onBackgroundMessage } from "firebase/messaging/sw";

const rawConfig = new URL(self.location.href).searchParams.get("config");

function notificationUrl(data = {}) {
	return data.click_action || "/app";
}

function notificationAssetUrl(value) {
	const asset = String(value || "").trim();
	if (!asset) return "";
	try {
		return new URL(asset, self.location.origin).href;
	} catch (_) {
		return "";
	}
}

async function openNotificationTarget(url) {
	const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
	for (const client of windows) {
		if (new URL(client.url).origin !== self.location.origin) continue;
		if ("navigate" in client) await client.navigate(url);
		return client.focus();
	}
	return self.clients.openWindow(url);
}

if (rawConfig) {
	const messaging = getMessaging(initializeApp(JSON.parse(rawConfig)));
	onBackgroundMessage(messaging, (payload) => {
		const data = payload?.data || {};
		const options = {
			body: data.body || payload?.notification?.body || "",
			tag: data.call_id ? `telephony-call-${data.call_id}` : "telephony-incoming-call",
			renotify: true,
			requireInteraction: true,
			data: { url: notificationUrl(data), call_id: data.call_id || "" },
		};
		const icon = notificationAssetUrl(data.notification_icon);
		const image = notificationAssetUrl(data.notification_image);
		if (icon) options.icon = icon;
		if (image) options.image = image;
		return self.registration.showNotification(data.title || payload?.notification?.title || "Incoming Call", options);
	});
}

self.addEventListener("notificationclick", (event) => {
	event.stopImmediatePropagation();
	event.notification.close();
	const target = event.notification?.data?.url || "/app";
	event.waitUntil(openNotificationTarget(target));
});
