"""Stable display names for the two nodes and their Telegram contacts."""

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class Node:
    label: str
    mention: str
    aliases: frozenset[str]


BILI = Node("bili", "@bilionid", frozenset({"bili", "bilionid", "bilionid-factories", "main", "primary"}))
SUSLIK = Node("suslik", "@suslik24na7", frozenset({"susl", "suslik", "suslikvpn", "suslik24na7", "backup", "standby"}))


def node_for_role(is_primary: bool) -> Node:
    return BILI if is_primary else SUSLIK


def find_node(settings: Settings, *values: str | None) -> Node | None:
    for value in values:
        if not value:
            continue
        token = value.strip().casefold()
        name, _, contact = token.partition(" @")
        for node in (BILI, SUSLIK):
            if name.lstrip("@") in node.aliases or f"@{contact}" == node.mention:
                return node
        for local, name, address in (
            (True, settings.NODE_NAME, settings.TAILSCALE_IP),
            (False, settings.PEER_NODE_NAME, settings.PEER_TAILSCALE_IP),
        ):
            if token == name.casefold() or (token == address.casefold() and token not in {"127.0.0.1", "::1"}):
                return node_for_role(settings.is_primary == local)
    return None
