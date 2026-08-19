from types import SimpleNamespace
import unittest

from telephony.runtime.accounts import build_runtime_account


class _Agent(SimpleNamespace):
    def get_password(self, fieldname, raise_exception=False):
        self.requested_password_field = fieldname
        return "secret"


class RuntimeAccountTest(unittest.TestCase):
    def test_global_network_settings_and_agent_identity_build_native_account(self):
        settings = SimpleNamespace(
            sip_server="192.0.2.10", sip_port=5061, transport="tcp",
            local_bind_address="0.0.0.0", advertised_address="198.51.100.20",
            proxy="proxy.example.test", proxy_port=5080, tls_server_name=None,
        )
        agent = _Agent(
            name="alice@example.test", user="alice@example.test", sip_username="2001",
            sip_extension="2001", sip_display_name="Alice",
        )
        account = build_runtime_account(settings, agent)
        self.assertEqual(account.user, "alice@example.test")
        self.assertEqual(account.config.server, "192.0.2.10")
        self.assertEqual(account.config.port, 5061)
        self.assertEqual(account.config.transport, "TCP")
        self.assertEqual(account.config.local_ip, "0.0.0.0")
        self.assertEqual(account.config.advertised_ip, "198.51.100.20")
        self.assertEqual(account.config.username, "2001")
        self.assertEqual(account.config.password, "secret")
        self.assertEqual(agent.requested_password_field, "sip_password")

    def test_agent_can_override_native_sip_server_settings(self):
        settings = SimpleNamespace(
            sip_server="global.example.test", sip_port=5060, transport="UDP",
            local_bind_address="0.0.0.0", advertised_address="198.51.100.10",
            proxy="global-proxy.example.test", proxy_port=5080, tls_server_name=None,
        )
        agent = _Agent(
            name="alice@example.test", user="alice@example.test", sip_username="2001",
            sip_extension="2001", sip_display_name="Alice", override_sip_settings=1,
            sip_override_server="agent-pbx.example.test", sip_override_port=5061,
            sip_override_transport="tls", sip_override_local_bind_address="127.0.0.1",
            sip_override_advertised_address="203.0.113.20", sip_override_tls_server_name="sip.example.test",
            sip_override_proxy="agent-proxy.example.test", sip_override_proxy_port=5091,
        )
        config = build_runtime_account(settings, agent).config
        self.assertEqual(config.server, "agent-pbx.example.test")
        self.assertEqual(config.port, 5061)
        self.assertEqual(config.transport, "TLS")
        self.assertEqual(config.local_ip, "127.0.0.1")
        self.assertEqual(config.advertised_ip, "203.0.113.20")
        self.assertEqual(config.tls_server_name, "sip.example.test")
        self.assertEqual(config.proxy, "agent-proxy.example.test")
        self.assertEqual(config.proxy_port, 5091)

    def test_agent_override_disabled_ignores_override_values(self):
        settings = SimpleNamespace(
            sip_server="global.example.test", sip_port=5070, transport="TCP",
            local_bind_address="0.0.0.0", advertised_address=None,
            proxy=None, proxy_port=None, tls_server_name=None,
        )
        agent = _Agent(
            name="a", user="a", sip_username="1001", sip_extension=None, sip_display_name=None,
            override_sip_settings=0, sip_override_server="ignored.example.test", sip_override_port=6000,
        )
        config = build_runtime_account(settings, agent).config
        self.assertEqual(config.server, "global.example.test")
        self.assertEqual(config.port, 5070)
        self.assertEqual(config.transport, "TCP")

    def test_account_contract_contains_no_configured_local_sip_or_rtp_range(self):
        settings = SimpleNamespace(
            sip_server="pbx.example.test", sip_port=5060, transport="UDP",
            local_bind_address="0.0.0.0", advertised_address=None,
            proxy=None, proxy_port=None, tls_server_name=None,
        )
        agent = _Agent(name="a", user="a", sip_username="1001", sip_extension=None, sip_display_name=None)
        config = build_runtime_account(settings, agent).config
        self.assertFalse(hasattr(config, "local_port"))
        self.assertFalse(hasattr(config, "rtp_port_low"))
        self.assertFalse(hasattr(config, "rtp_port_high"))
        self.assertNotIn("secret", repr(config))


if __name__ == "__main__":
    unittest.main()
