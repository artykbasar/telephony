from unittest.mock import ANY, patch
import unittest

from telephony.contacts import resolve_contact_numbers


class _Row(dict):
    __getattr__ = dict.get


class ContactResolutionTest(unittest.TestCase):
    @patch("telephony.contacts.frappe")
    def test_resolves_primary_and_secondary_numbers_after_normalization(self, frappe):
        frappe.get_list.return_value = [
            _Row(name="CONTACT-1", full_name="John Smith", first_name="John", last_name="Smith",
                 mobile_no="+44 7700 900123", phone=None, company_name=None, image=None),
        ]
        frappe.get_all.return_value = [_Row(parent="CONTACT-1", phone="020 7946 0958")]
        result = resolve_contact_numbers(["07700 900123", "+44 20 7946 0958"], default_region="GB")
        self.assertEqual(result["07700 900123"]["contact_name"], "John Smith")
        self.assertEqual(result["+44 20 7946 0958"]["contact"], "CONTACT-1")

    @patch("telephony.contacts.frappe")
    def test_duplicate_number_is_ambiguous_instead_of_guessing(self, frappe):
        frappe.get_list.return_value = [
            _Row(name="CONTACT-1", full_name="One", first_name="One", last_name=None, mobile_no="07700 900123", phone=None, company_name=None, image=None),
            _Row(name="CONTACT-2", full_name="Two", first_name="Two", last_name=None, mobile_no="+447700900123", phone=None, company_name=None, image=None),
        ]
        frappe.get_all.return_value = []
        result = resolve_contact_numbers(["00447700900123"], default_region="GB")
        self.assertIsNone(result["00447700900123"])


class ContactDetailTest(unittest.TestCase):
    @patch("telephony.contacts.frappe")
    def test_exact_contact_details_respect_contact_permissions_and_include_children(self, frappe):
        from telephony.contacts import get_contact_details

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True
        frappe.get_list.return_value = [
            _Row(
                name="CONTACT-1", full_name="John Smith", first_name="John", middle_name=None,
                last_name="Smith", email_id="john@example.com", user=None, status="Passive",
                salutation=None, gender=None, phone=None, mobile_no="07700 900123", department="Sales",
                designation="Property Manager", address="ADDR-1", google_contacts=None,
                google_contacts_id=None, company_name="Safe Zone Partners", image="/files/john.jpg",
            )
        ]

        def get_all(doctype, **kwargs):
            if doctype == "Contact Phone":
                return [_Row(parent="CONTACT-1", phone="2002")]
            if doctype == "Contact Email":
                return [_Row(parent="CONTACT-1", email_id="leasing@example.com")]
            if doctype == "Dynamic Link":
                return [_Row(parent="CONTACT-1", link_doctype="Customer", link_name="CUST-1", link_title="Safe Zone Partners")]
            return []

        frappe.get_all.side_effect = get_all
        result = get_contact_details("CONTACT-1")
        self.assertEqual(result["item"]["name"], "CONTACT-1")
        self.assertEqual(result["item"]["number"], "07700 900123")
        self.assertEqual(result["item"]["additional_phones"], ["2002"])
        self.assertEqual(result["item"]["additional_emails"], ["leasing@example.com"])
        self.assertEqual(result["item"]["links"][0]["link_doctype"], "Customer")
        frappe.get_list.assert_called_once_with(
            "Contact", filters={"name": "CONTACT-1"}, fields=ANY, limit=1
        )

    @patch("telephony.contacts.frappe")
    def test_exact_contact_details_do_not_leak_unreadable_contact(self, frappe):
        from telephony.contacts import get_contact_details

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True
        frappe.get_list.return_value = []
        self.assertEqual(get_contact_details("CONTACT-PRIVATE"), {"item": None})
        frappe.get_all.assert_not_called()

    @patch("telephony.contacts.get_default_phone_region", return_value="GB")
    @patch("telephony.contacts.frappe")
    def test_create_contact_from_call_uses_normal_insert_and_returns_profile(self, frappe, _region):
        from telephony.contacts import create_contact_from_call

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True

        class ContactDoc:
            name = "NEW-CONTACT"

            def __init__(self, values):
                self.values = values
                self.phone_nos = []
                self.email_ids = []
                self.inserted = False

            def append(self, fieldname, values):
                getattr(self, fieldname).append(_Row(**values))

            def insert(self):
                self.inserted = True

        created = None

        def get_doc(values):
            nonlocal created
            created = ContactDoc(values)
            return created

        frappe.get_doc.side_effect = get_doc
        frappe.get_list.return_value = [
            _Row(
                name="NEW-CONTACT", full_name="Jane Doe", first_name="Jane", middle_name=None,
                last_name="Doe", email_id="jane@example.com", user=None, status="Passive",
                salutation=None, gender=None, phone=None, mobile_no="07700 900123", department="Sales",
                designation="Manager", address=None, google_contacts=None, google_contacts_id=None,
                company_name="Example Ltd", image=None,
            )
        ]

        def get_all(doctype, **kwargs):
            if doctype == "Contact Phone":
                return [_Row(parent="NEW-CONTACT", phone=row.phone) for row in created.phone_nos]
            if doctype == "Contact Email":
                return [_Row(parent="NEW-CONTACT", email_id=row.email_id) for row in created.email_ids]
            return []

        frappe.get_all.side_effect = get_all
        result = create_contact_from_call(
            first_name="Jane", last_name="Doe", phone="07700 900123", email="jane@example.com",
            company_name="Example Ltd", department="Sales", designation="Manager",
        )

        self.assertTrue(created.inserted)
        self.assertEqual(created.values["doctype"], "Contact")
        self.assertEqual(created.values["first_name"], "Jane")
        self.assertEqual(created.phone_nos[0].phone, "07700 900123")
        self.assertEqual(created.phone_nos[0].is_primary_mobile_no, 1)
        self.assertEqual(created.email_ids[0].email_id, "jane@example.com")
        self.assertEqual(created.email_ids[0].is_primary, 1)
        self.assertEqual(result["item"]["name"], "NEW-CONTACT")

    @patch("telephony.contacts.get_default_phone_region", return_value="GB")
    @patch("telephony.contacts.frappe")
    def test_add_number_to_contact_checks_write_permission_and_returns_contact(self, frappe, _region):
        from telephony.contacts import add_number_to_contact

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True

        class ContactDoc:
            mobile_no = None
            phone = None
            phone_nos = []

            def __init__(self):
                self.phone_nos = []
                self.permission_checked = False
                self.saved = False

            def check_permission(self, permtype):
                self.permission_checked = permtype == "write"

            def append(self, fieldname, values):
                self.phone_nos.append(_Row(**values))

            def save(self):
                self.saved = True

        doc = ContactDoc()
        frappe.get_doc.return_value = doc
        frappe.get_list.return_value = [
            _Row(
                name="CONTACT-1", full_name="John Smith", first_name="John", middle_name=None,
                last_name="Smith", email_id=None, user=None, status="Passive", salutation=None,
                gender=None, phone=None, mobile_no=None, department=None, designation=None,
                address=None, google_contacts=None, google_contacts_id=None, company_name=None, image=None,
            )
        ]

        def get_all(doctype, **kwargs):
            if doctype == "Contact Phone":
                return [_Row(parent="CONTACT-1", phone=row.phone) for row in doc.phone_nos]
            return []

        frappe.get_all.side_effect = get_all
        result = add_number_to_contact("CONTACT-1", "07700 900123")

        self.assertTrue(doc.permission_checked)
        self.assertTrue(doc.saved)
        self.assertEqual(doc.phone_nos[0].phone, "07700 900123")
        self.assertTrue(result["added"])
        self.assertEqual(result["item"]["name"], "CONTACT-1")
        self.assertIn("07700 900123", result["item"]["additional_phones"])

    @patch("telephony.contacts.get_default_phone_region", return_value="GB")
    @patch("telephony.contacts.frappe")
    def test_add_number_to_contact_does_not_duplicate_normalized_number(self, frappe, _region):
        from telephony.contacts import add_number_to_contact

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True

        class ContactDoc:
            mobile_no = "+44 7700 900123"
            phone = None
            phone_nos = []

            def __init__(self):
                self.phone_nos = []
                self.saved = False

            def check_permission(self, _permtype):
                return None

            def append(self, _fieldname, _values):
                raise AssertionError("duplicate number should not be appended")

            def save(self):
                self.saved = True

        doc = ContactDoc()
        frappe.get_doc.return_value = doc
        frappe.get_list.return_value = [
            _Row(
                name="CONTACT-1", full_name="John Smith", first_name="John", middle_name=None,
                last_name="Smith", email_id=None, user=None, status="Passive", salutation=None,
                gender=None, phone=None, mobile_no="+44 7700 900123", department=None, designation=None,
                address=None, google_contacts=None, google_contacts_id=None, company_name=None, image=None,
            )
        ]
        frappe.get_all.return_value = []

        result = add_number_to_contact("CONTACT-1", "07700 900123")
        self.assertFalse(result["added"])
        self.assertFalse(doc.saved)


class ContactViewSearchTest(unittest.TestCase):
    @staticmethod
    def _contact(**values):
        defaults = dict(
            name="CONTACT-1", full_name="John Smith", first_name="John", middle_name=None,
            last_name="Smith", email_id="john.primary@example.com", user=None, status="Passive",
            salutation=None, gender=None, phone=None, mobile_no="07700 900123", department="Sales",
            designation="Property Manager", address="ADDR-1", google_contacts=None,
            google_contacts_id=None, company_name="Safe Zone Partners", image=None,
        )
        defaults.update(values)
        return _Row(**defaults)

    @patch("telephony.contacts.frappe")
    def test_search_matches_secondary_email_and_returns_match_hint(self, frappe):
        from telephony.contacts import get_contacts

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True
        contact = self._contact()

        def get_list(doctype, **kwargs):
            if kwargs.get("fields") == ["name"]:
                return []
            return [contact]

        def get_all(doctype, **kwargs):
            filters = kwargs.get("filters") or {}
            if doctype == "Contact Email" and "or_filters" in kwargs:
                return [_Row(parent="CONTACT-1")]
            if doctype == "Contact Email" and "parent" in filters:
                return [_Row(parent="CONTACT-1", email_id="leasing.team@example.com")]
            return []

        frappe.get_list.side_effect = get_list
        frappe.get_all.side_effect = get_all
        result = get_contacts("leasing.team@example.com")
        self.assertEqual(result["items"][0]["name"], "CONTACT-1")
        self.assertEqual(result["items"][0]["match_hint"], {"label": "Email", "value": "leasing.team@example.com"})
        self.assertIn("additional_phones", result["items"][0])
        self.assertIn("additional_emails", result["items"][0])
        self.assertIn("links", result["items"][0])

    @patch("telephony.contacts.frappe")
    def test_search_matches_secondary_phone_with_format_insensitive_digits(self, frappe):
        from telephony.contacts import get_contacts

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True
        contact = self._contact(mobile_no=None, phone=None)

        def get_list(doctype, **kwargs):
            if kwargs.get("fields") == ["name"]:
                return []
            return [contact]

        def get_all(doctype, **kwargs):
            filters = kwargs.get("filters") or {}
            if doctype == "Contact Phone" and "or_filters" in kwargs:
                return [_Row(parent="CONTACT-1")]
            if doctype == "Contact Phone" and "parent" in filters:
                return [_Row(parent="CONTACT-1", phone="020 7946 0958")]
            return []

        frappe.get_list.side_effect = get_list
        frappe.get_all.side_effect = get_all
        result = get_contacts("02079460958")
        self.assertEqual(result["items"][0]["number"], "020 7946 0958")

    @patch("telephony.contacts.frappe")
    def test_multiple_terms_can_match_different_contact_fields(self, frappe):
        from telephony.contacts import get_contacts

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True
        contact = self._contact()

        def get_list(doctype, **kwargs):
            if kwargs.get("fields") == ["name"]:
                return [_Row(name="CONTACT-1")]
            return [contact]

        frappe.get_list.side_effect = get_list
        frappe.get_all.return_value = []
        result = get_contacts("John Sales")
        self.assertEqual(result["items"][0]["name"], "CONTACT-1")
        self.assertEqual(result["items"][0]["department"], "Sales")

    @patch("telephony.contacts.frappe")
    def test_contacts_paginate_twenty_at_a_time_without_skips(self, frappe):
        from telephony.contacts import get_contacts

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True
        contacts = [
            self._contact(
                name=f"CONTACT-{index:03d}",
                full_name=f"Person {index:03d}",
                first_name="Person",
                last_name=f"{index:03d}",
                mobile_no=f"07700{index:06d}",
            )
            for index in range(45)
        ]

        def get_list(doctype, **kwargs):
            start = int(kwargs.get("limit_start") or 0)
            length = int(kwargs.get("limit_page_length") or len(contacts))
            return contacts[start:start + length]

        frappe.get_list.side_effect = get_list
        frappe.get_all.return_value = []

        first = get_contacts()
        second = get_contacts(cursor=first["next_cursor"])
        third = get_contacts(cursor=second["next_cursor"])

        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(second["items"]), 20)
        self.assertEqual(len(third["items"]), 5)
        self.assertTrue(first["has_more"])
        self.assertTrue(second["has_more"])
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_cursor"])
        names = [item["name"] for page in (first, second, third) for item in page["items"]]
        self.assertEqual(names, [f"CONTACT-{index:03d}" for index in range(45)])

    @patch("telephony.contacts.frappe")
    def test_contact_search_results_paginate_twenty_at_a_time(self, frappe):
        from telephony.contacts import get_contacts

        frappe.session.user = "agent@example.test"
        frappe.db.exists.return_value = True
        contacts = [
            self._contact(
                name=f"SEARCH-{index:03d}",
                full_name=f"Search Person {index:03d}",
                first_name="Search",
                last_name=f"Person {index:03d}",
                mobile_no=f"07701{index:06d}",
            )
            for index in range(25)
        ]

        def get_list(doctype, **kwargs):
            if kwargs.get("fields") == ["name"]:
                return [_Row(name=item["name"]) for item in contacts]
            return contacts

        frappe.get_list.side_effect = get_list
        frappe.get_all.return_value = []

        first = get_contacts("Search")
        second = get_contacts("Search", cursor=first["next_cursor"])

        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(second["items"]), 5)
        self.assertEqual(first["next_cursor"], "s:20")
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        names = [item["name"] for page in (first, second) for item in page["items"]]
        self.assertEqual(names, [f"SEARCH-{index:03d}" for index in range(25)])


if __name__ == "__main__":
    unittest.main()
