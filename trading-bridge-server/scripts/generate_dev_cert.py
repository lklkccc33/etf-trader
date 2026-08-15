"""VPC 내부 통신용 자체서명 TLS 인증서 생성 (로컬 개발/내부망 전용).

퍼블릭 신뢰 체인이 필요 없는 VPC 내부 서버간 통신 전제 — 운영서버 쪽에서
이 인증서(cert.pem)를 신뢰 목록에 등록해 pinning 하는 방식을 권장.
"""
import datetime as dt
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "certs")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "trading-bridge-server.internal")]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.timezone.utc))
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("trading-bridge-server.internal"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path = os.path.join(OUT_DIR, "key.pem")
    cert_path = os.path.join(OUT_DIR, "cert.pem")

    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"wrote {cert_path}")
    print(f"wrote {key_path}")


if __name__ == "__main__":
    main()
