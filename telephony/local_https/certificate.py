from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from telephony.local_https.config import local_https_directory, normalise_local_https_host


@dataclass(frozen=True)
class LocalHttpsCertificate:
    certificate_path: Path
    private_key_path: Path
    fingerprint_sha256: str
    expires_at: datetime
    generated: bool


def _certificate_paths(*, site: str, bench_path: Path) -> tuple[Path, Path]:
    directory = local_https_directory(site=site, bench_path=bench_path)
    return directory / "cert.pem", directory / "key.pem"


def _san_value(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


def _load_existing(certificate_path: Path, private_key_path: Path, host: str) -> LocalHttpsCertificate | None:
    try:
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, TypeError, x509.ExtensionNotFound):
        return None
    try:
        host_address = ipaddress.ip_address(host)
    except ValueError:
        matches_host = host in san.get_values_for_type(x509.DNSName)
    else:
        matches_host = host_address in san.get_values_for_type(x509.IPAddress)
    if not matches_host:
        return None
    if certificate.not_valid_after_utc <= datetime.now(UTC) + timedelta(days=7):
        return None
    try:
        key_public = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cert_public = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except (AttributeError, TypeError, ValueError):
        return None
    if key_public != cert_public:
        return None
    return LocalHttpsCertificate(
        certificate_path=certificate_path,
        private_key_path=private_key_path,
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
        expires_at=certificate.not_valid_after_utc,
        generated=False,
    )


def ensure_self_signed_certificate(*, site: str, bench_path: Path, host: str) -> LocalHttpsCertificate:
    host = normalise_local_https_host(host)
    certificate_path, private_key_path = _certificate_paths(site=site, bench_path=bench_path)
    existing = _load_existing(certificate_path, private_key_path, host)
    if existing is not None:
        return existing

    directory = certificate_path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    common_name = host[:64]
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Frappe Telephony Local Testing"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([_san_value(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )
    key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    _atomic_private_write(private_key_path, key_bytes, mode=0o600)
    _atomic_private_write(certificate_path, cert_bytes, mode=0o644)
    return LocalHttpsCertificate(
        certificate_path=certificate_path,
        private_key_path=private_key_path,
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
        expires_at=certificate.not_valid_after_utc,
        generated=True,
    )


def _atomic_private_write(path: Path, content: bytes, *, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        previous_umask = os.umask(0o077)
        try:
            with temporary.open("wb") as stream:
                stream.write(content)
        finally:
            os.umask(previous_umask)
        os.chmod(temporary, mode)
        temporary.replace(path)
        os.chmod(path, mode)
    finally:
        temporary.unlink(missing_ok=True)
