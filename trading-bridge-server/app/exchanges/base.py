from abc import ABC, abstractmethod


class ExchangeOrderError(Exception):
    """거래소가 요청을 거부했을 때(주문/잔고/체결조회 공통) 발생.

    거래소별 client는 자기 응답 포맷에서 code/message를 뽑아 이 예외로
    통일해서 던져야 함 — 호출부는 어떤 거래소인지 몰라도 되게.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ExchangeClient(ABC):
    """계좌 하나에 대한 거래소 연동 인터페이스.

    새 거래소를 추가하려면 이 클래스를 상속해 구현하고
    exchanges/registry.py의 매핑에 등록하면 됨 — 호출부(routers, reports,
    fills, scheduler)는 이 인터페이스로만 접근하므로 수정할 필요 없음.
    """

    @abstractmethod
    def place_order(
        self,
        *,
        exchange_code: str,
        ticker: str,
        side: str,
        order_type: str,
        volume: str,
        price: str,
    ) -> dict:
        """주문을 접수하고 {"order_no": str, "raw": dict}를 반환.
        실패(거래소가 거부)하면 ExchangeOrderError를 던짐."""
        raise NotImplementedError

    @abstractmethod
    def get_balance(self, exchange_code: str, currency_code: str) -> dict:
        """계좌 잔고를 거래소 원본 형태로 반환."""
        raise NotImplementedError

    @abstractmethod
    def get_order_execution(self, exchange_code: str, order_no: str, order_date: str) -> dict | None:
        """주문번호 기준 체결 내역. {"order_qty", "filled_qty", "filled_price"}
        또는 아직 조회되는 게 없으면 None."""
        raise NotImplementedError
