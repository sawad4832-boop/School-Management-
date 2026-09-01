"""Zeigt beim Start, unter welchen Adressen das Dashboard erreichbar ist.

Auf Wunsch als QR-Code im Terminal, damit die Adresse am Handy nur noch
abgescannt werden muss (``pip install qrcode`` – optional).

Aufruf::

    python -m schulcloud.netinfo --port 5000 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import socket
from typing import Optional


def lan_ip() -> Optional[str]:
    """Ermittelt die IP-Adresse dieses Rechners im lokalen Netz."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Es wird nichts gesendet; das Ziel bestimmt nur die passende Route.
        sock.connect(("192.0.2.1", 53))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def qr_lines(url: str) -> list[str]:
    """QR-Code als Textzeilen; leere Liste, wenn ``qrcode`` fehlt."""
    try:
        import qrcode
    except ImportError:
        return []

    code = qrcode.QRCode(border=1)
    code.add_data(url)
    code.make(fit=True)
    matrix = code.get_matrix()

    # Zwei Zeilen der Matrix je Textzeile - so bleibt der Code quadratisch.
    blocks = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}
    lines = []
    for row in range(0, len(matrix), 2):
        upper = matrix[row]
        lower = matrix[row + 1] if row + 1 < len(matrix) else [False] * len(upper)
        lines.append("".join(blocks[(bool(u), bool(l))] for u, l in zip(upper, lower)))
    return lines


def report(port: int, host: str, show_qr: bool = True) -> str:
    lines = [f"→ Auf diesem Rechner:  http://127.0.0.1:{port}"]
    if host == "0.0.0.0":
        ip = lan_ip()
        if ip:
            url = f"http://{ip}:{port}"
            lines.append(f"→ Im gleichen WLAN:    {url}")
            if show_qr:
                code = qr_lines(url)
                if code:
                    lines.append("")
                    lines.append("  Mit der Handy-Kamera scannen:")
                    lines.extend("  " + line for line in code)
                else:
                    lines.append("  (QR-Code mit 'pip install qrcode' verfügbar)")
        else:
            lines.append("→ Netzwerkadresse konnte nicht ermittelt werden.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description="Zugriffsadressen anzeigen")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-qr", action="store_true")
    args = parser.parse_args(argv)
    print(report(args.port, args.host, show_qr=not args.no_qr))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
