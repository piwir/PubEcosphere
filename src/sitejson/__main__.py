"""`python -m sitejson` 入口：sync / --selftest。"""

from .sync import main

if __name__ == "__main__":
    raise SystemExit(main())
