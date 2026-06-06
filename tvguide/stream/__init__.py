"""LAN streaming: watch the current channel from any browser on the network."""
from .server import StreamServer, lan_ip

__all__ = ["StreamServer", "lan_ip"]
