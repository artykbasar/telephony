import unittest
from unittest.mock import patch

from telephony.push import (
    get_call_notification_bootstrap,
    get_call_notification_status,
    send_incoming_call_notification,
)
from telephony.voice.sip import SipIncomingCall


class TelephonyPushTest(unittest.TestCase):
    def test_incoming_call_uses_standard_frappe_push_project_and_deep_link(self):
        call = SipIncomingCall(
            call_id="call id/123",
            caller_id="07725511905",
            called_number="2001",
            caller_name="Artyk Basarov",
            ivr_route="Landlord",
        )
        with patch("telephony.push.frappe") as frappe, patch("frappe.push_notification.PushNotification") as push_class, patch("telephony.push._resolve_contact_for_user", return_value={}):
            frappe.utils.get_url.return_value = "https://safezone.example"
            push = push_class.return_value
            push.is_enabled.return_value = True
            push.send_notification_to_user.return_value = True

            self.assertTrue(send_incoming_call_notification("agent@example.test", call))

            push_class.assert_called_once_with("telephony")
            args, kwargs = push.send_notification_to_user.call_args
            self.assertEqual(args[0], "agent@example.test")
            self.assertEqual(args[1], "Artyk Basarov")
            self.assertIn("Incoming call", args[2])
            self.assertIn("07725511905", args[2])
            self.assertIn("Landlord", args[2])
            self.assertEqual(kwargs["link"], "https://safezone.example/app?telephony_call=call%20id%2F123")
            self.assertEqual(kwargs["data"]["_frappe_push_data_only"], "1")
            self.assertEqual(kwargs["data"]["type"], "telephony_incoming_call")
            self.assertEqual(kwargs["data"]["call_id"], "call id/123")
            self.assertEqual(kwargs["data"]["caller_number"], "07725511905")
            self.assertEqual(kwargs["data"]["caller_name"], "Artyk Basarov")
            self.assertEqual(kwargs["data"]["title"], "Artyk Basarov")
            self.assertEqual(kwargs["data"]["body"], args[2])
            self.assertEqual(kwargs["data"]["ivr_route"], "Landlord")
            self.assertEqual(kwargs["icon"], "/assets/telephony/softphone_media/contact_avatar.png")
            self.assertNotIn("notification_image", kwargs["data"])

    def test_contact_match_enriches_notification_and_uses_public_photo(self):
        call = SipIncomingCall("call-2", "07700900123", "2003", "PBX Caller", "General Enquiry")
        contact = {
            "contact": "CONTACT-1",
            "contact_name": "John Smith",
            "company_name": "Example Ltd",
            "designation": "Property Manager",
            "department": "Operations",
            "image": "/files/john-smith.jpg",
        }
        with patch("telephony.push.frappe") as frappe, patch("frappe.push_notification.PushNotification") as push_class, patch("telephony.push._resolve_contact_for_user", return_value=contact):
            frappe.utils.get_url.return_value = "https://client.example"
            push = push_class.return_value
            push.is_enabled.return_value = True
            push.send_notification_to_user.return_value = True

            self.assertTrue(send_incoming_call_notification("agent@example.test", call))

            args, kwargs = push.send_notification_to_user.call_args
            self.assertEqual(args[1], "John Smith")
            self.assertIn("Incoming call", args[2])
            self.assertIn("07700900123", args[2])
            self.assertIn("Example Ltd", args[2])
            self.assertNotIn("Property Manager", args[2])
            self.assertNotIn("Operations", args[2])
            self.assertIn("General Enquiry", args[2])
            self.assertEqual(kwargs["icon"], "/files/john-smith.jpg")
            self.assertEqual(kwargs["data"]["contact"], "CONTACT-1")
            self.assertEqual(kwargs["data"]["caller_name"], "John Smith")
            self.assertEqual(kwargs["data"]["title"], "John Smith")
            self.assertEqual(kwargs["data"]["body"], args[2])
            self.assertEqual(kwargs["data"]["designation"], "Property Manager")
            self.assertEqual(kwargs["data"]["department"], "Operations")
            self.assertEqual(kwargs["data"]["sip_caller_name"], "PBX Caller")
            self.assertEqual(kwargs["data"]["notification_image"], "/files/john-smith.jpg")

    def test_private_contact_photo_uses_fallback_avatar(self):
        call = SipIncomingCall("call-3", "07700900124", "2003", "PBX Caller", None)
        contact = {"contact": "CONTACT-2", "contact_name": "Jane Doe", "image": "/private/files/jane.jpg"}
        with patch("telephony.push.frappe"), patch("frappe.push_notification.PushNotification") as push_class, patch("telephony.push._resolve_contact_for_user", return_value=contact):
            push = push_class.return_value
            push.is_enabled.return_value = True
            push.send_notification_to_user.return_value = True

            self.assertTrue(send_incoming_call_notification("agent@example.test", call))

            _args, kwargs = push.send_notification_to_user.call_args
            self.assertEqual(kwargs["icon"], "/assets/telephony/softphone_media/contact_avatar.png")
            self.assertEqual(kwargs["data"]["contact_image"], "")
            self.assertNotIn("notification_image", kwargs["data"])

    def test_disabled_relay_skips_incoming_call_push(self):
        call = SipIncomingCall("call-1", "44111", "2001", "Caller", None)
        with patch("telephony.push.frappe"), patch("frappe.push_notification.PushNotification") as push_class:
            push_class.return_value.is_enabled.return_value = False
            self.assertFalse(send_incoming_call_notification("agent@example.test", call))
            push_class.return_value.send_notification_to_user.assert_not_called()

    def test_bootstrap_is_fetched_server_side_from_configured_relay(self):
        with patch("telephony.push.frappe") as frappe, patch("telephony.push.make_get_request") as make_get, patch("frappe.push_notification.PushNotification") as push_class:
            frappe.session.user = "agent@example.test"
            frappe.db.exists.return_value = "agent@example.test"
            frappe.conf.get.return_value = "http://relay.localhost:8002"
            push_class.return_value.is_enabled.return_value = True
            make_get.return_value = {
                "config": {"projectId": "push-test"},
                "vapid_public_key": "public-vapid-key",
                "project_name": "telephony",
            }

            result = get_call_notification_bootstrap()

            make_get.assert_called_once_with(
                "http://relay.localhost:8002/api/method/notification_relay.api.get_config",
                params={"project_name": "telephony"},
            )
            self.assertEqual(result["config"]["projectId"], "push-test")
            self.assertEqual(result["vapid_public_key"], "public-vapid-key")
            self.assertEqual(result["project_name"], "telephony")

    def test_status_reports_standard_frappe_relay_readiness_for_sip_agent(self):
        with patch("telephony.push.frappe") as frappe, patch("frappe.push_notification.PushNotification") as push_class:
            frappe.session.user = "agent@example.test"
            frappe.db.exists.return_value = "agent@example.test"
            frappe.conf.get.return_value = "https://push.example.test"
            push_class.return_value.is_enabled.return_value = True

            result = get_call_notification_status()

            self.assertEqual(result["project_name"], "telephony")
            self.assertTrue(result["supported"])
            self.assertTrue(result["relay_enabled"])
            self.assertTrue(result["relay_url_configured"])


if __name__ == "__main__":
    unittest.main()
