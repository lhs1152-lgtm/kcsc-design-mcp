# -*- coding: utf-8 -*-
"""진입점 — stdio 로 MCP 서버를 돈다."""
from .server import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
