"""Serve the API on a dual-stack (IPv4 + IPv6) socket.

asyncio marks IPv6 listeners as IPV6_V6ONLY, so ``uvicorn --host ::`` only accepts
IPv6 while ``--host 0.0.0.0`` only accepts IPv4. Railway routes public traffic over
IPv4 and private-network traffic over IPv6, so the API must accept both. This
launcher binds one IPv6 socket with V6ONLY disabled and passes it to uvicorn.
"""

import os
import socket

import uvicorn


def _dual_stack_socket(port: int) -> socket.socket:
    """Return a listening socket bound to [::]:port that also accepts IPv4."""
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(("::", port))
    sock.listen(2048)
    sock.set_inheritable(True)
    return sock


def main() -> None:
    """Start uvicorn on the dual-stack socket."""
    port = int(os.environ.get("PORT", "8000"))
    sock = _dual_stack_socket(port)
    uvicorn.run(
        "app.main:app",
        fd=sock.fileno(),
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
