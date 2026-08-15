import ipaddress
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import settings

logger = logging.getLogger(__name__)


class TrustedNetworkMiddleware(BaseHTTPMiddleware):
    """동일 VPC의 지정 서버만 연결 가능하도록 하는 애플리케이션 레벨 방어선.

    Security Group으로 이미 VPC 경계를 막고 있어야 하며, 이 미들웨어는
    그 방어를 한 겹 더하는 용도임 (defense in depth). 앞단에 로드밸런서/
    프록시가 있으면 request.client.host가 프록시 IP가 되므로 이 구현은
    맞지 않음 — 그 경우 신뢰할 수 있는 X-Forwarded-For 파싱으로 바꿔야 함.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        networks = settings.TRUSTED_NETWORKS
        if networks is None:
            return await call_next(request)

        client_host = request.client.host if request.client else None
        if client_host is None or not self._is_trusted(client_host, networks):
            logger.warning("blocked request from untrusted ip=%s path=%s", client_host, request.url.path)
            return JSONResponse({"detail": "forbidden"}, status_code=403)

        return await call_next(request)

    @staticmethod
    def _is_trusted(ip: str, networks) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in network for network in networks)
