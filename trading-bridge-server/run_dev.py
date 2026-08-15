import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    cert_path = os.path.join("certs", "cert.pem")
    key_path = os.path.join("certs", "key.pem")
    ssl_kwargs = {}
    if os.path.exists(cert_path) and os.path.exists(key_path):
        ssl_kwargs = {"ssl_certfile": cert_path, "ssl_keyfile": key_path}
        print(f"TLS enabled with {cert_path}")

    # --reload은 이 환경(OneDrive 동기화 경로)에서 WatchFiles가 워커를
    # 재기동하지 못하는 경우가 있어 꺼둠. 코드 수정 후에는 수동으로 재시작할 것.
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=8000, reload=False, **ssl_kwargs
    )
