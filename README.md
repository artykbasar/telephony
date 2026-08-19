### Telephony

Telephony for Frappe apps, with Exotel and Twilio integrations plus a native server-side SIP/RTP Desk softphone.

---

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app <telephony-repository>
bench --site <site-name> install-app telephony
bench --site <site-name> migrate
bench build --app telephony
```

---

### Configuration

#### Doctypes

##### TP Twilio Settings

This stores configuration settings for integrating with Twilio provider, including API credentials such as Account SID and Auth Token, Secret, options to enable the integration and record calls.

##### TP SIP Settings

This stores the native server-side SIP runtime settings used by the Desk softphone, including SIP server, port, transport, bind/advertised addresses, TLS server name, and optional SIP proxy.

##### TP Exotel Settings

This holds settings for Exotel provider, such as Account SID, Subdomain, Webhook Verify Token, API Key, API Token, and flags to enable the service and record outgoing calls.

##### TP Call Log

This records details of all telephony calls made through the app, including call ID, from/to numbers, status, duration, type (incoming/outgoing), timestamps, recording URL, and links field for linking calls to related documents.

##### TP Telephony Agent

This defines agents who can make and receive calls. It links Frappe Users to their telephony identities, including:

- Twilio / Exotel numbers or devices.
- Native SIP credentials used by the server-side Telephony runtime (SIP username/extension and password).

#### Webhooks and APIs

Webhooks and API configuration for Telephony app will be found in the respective app's documentation that uses this app.

---

### Twilio Setup

https://docs.frappe.io/helpdesk/twilio

### Exotel Setup

https://docs.frappe.io/helpdesk/exotel

---
### Desk Softphone (Native SIP)

See [`docs/desk-softphone-setup.md`](docs/desk-softphone-setup.md) for native SIP/PBX configuration and [`docs/production-deployment.md`](docs/production-deployment.md) for runtime-manager deployment, health checks, and the production smoke test.

---

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/telephony
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

---

### License

agpl-3.0
