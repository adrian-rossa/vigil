from __future__ import annotations

import asyncio
from pathlib import Path

FRAME_HEADER_BYTES = 4
# qemu NET_BUFSIZE, the largest frame a stream netdev sends
MAX_FRAME_BYTES = 4096 + 65536
WRITE_BUFFER_LIMIT_BYTES = 1024 * 1024
SOCKET_MODE = 0o600


class Hub:
    def __init__(self, write_buffer_limit: int = WRITE_BUFFER_LIMIT_BYTES) -> None:
        self.write_buffer_limit = write_buffer_limit
        self.clients: set[asyncio.StreamWriter] = set()
        self.dropped_frames = 0

    async def relay(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.clients.add(writer)
        try:
            while True:
                header = await reader.readexactly(FRAME_HEADER_BYTES)
                length = int.from_bytes(header, "big")
                if length > MAX_FRAME_BYTES:
                    return
                frame = await reader.readexactly(length)
                self.forward(writer, header + frame)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    def forward(self, source: asyncio.StreamWriter, packet: bytes) -> None:
        for peer in self.clients:
            if peer is source:
                continue
            if peer.transport.get_write_buffer_size() > self.write_buffer_limit:
                self.dropped_frames += 1
                continue
            peer.write(packet)


async def serve(socket_path: Path) -> None:
    socket_path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(Hub().relay, path=str(socket_path))
    socket_path.chmod(SOCKET_MODE)
    async with server:
        await server.serve_forever()


def main(socket_path: Path) -> None:
    asyncio.run(serve(socket_path))
