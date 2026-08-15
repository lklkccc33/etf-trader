import ipaddress
import os

from dotenv import load_dotenv

load_dotenv()


def _parse_trusted_networks(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    return [ipaddress.ip_network(cidr.strip(), strict=False) for cidr in raw.split(",") if cidr.strip()]


class Settings:
    KIS_REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
    KIS_VIRTUAL_DOMAIN = "https://openapivts.koreainvestment.com:29443"
    ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]

    OPERATIONS_API_KEY = os.environ["OPERATIONS_API_KEY"]
    OPERATIONS_API_SECRET = os.environ["OPERATIONS_API_SECRET"]

    REPORTS_DIR = os.environ.get("REPORTS_DIR", "./reports")
    LOG_DIR = os.environ.get("LOG_DIR", "./logs")

    # 콤마로 구분된 CIDR 목록. 비어있으면(로컬 개발) 필터링을 하지 않음 —
    # 운영 배포 시에는 반드시 운영서버가 속한 VPC 대역/IP로 설정할 것.
    TRUSTED_NETWORKS = _parse_trusted_networks(os.environ.get("TRUSTED_NETWORKS", ""))

    # KIS 실제 계약상 초당 호출 한도를 확인하지 못해 보수적인 기본값을 둠 —
    # 실제 연동 시 계정 등급에 맞춰 조정할 것.
    KIS_RATE_LIMIT_PER_SECOND = float(os.environ.get("KIS_RATE_LIMIT_PER_SECOND", "5"))


settings = Settings()
