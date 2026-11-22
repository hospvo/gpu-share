import argparse
import json
from pathlib import Path
from aiohttp import web
from agent import Agent
from hub import create


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--console", action="store_true")
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8-sig"))
    icon = None
    if "nodes" in cfg:
        app = create(cfg)
    else:
        agent = Agent(cfg, args.config.resolve().parent)
        if not args.console:
            from PIL import Image, ImageDraw
            import pystray
            image = Image.new("RGB", (64, 64), "#183a49")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((9, 16, 54, 48), radius=5, outline="white", width=3)
            draw.ellipse((25, 22, 44, 41), outline="#65dcaa", width=4)
            def toggle(icon, item):
                if agent.pause.exists():
                    agent.pause.unlink(missing_ok=True)
                else:
                    agent.pause.touch()
                icon.update_menu()
            icon = pystray.Icon("gpu-share", image, "GPU Share", pystray.Menu(
                pystray.MenuItem("Pozastavit sdílení", toggle, checked=lambda _: agent.pause.exists()),
                pystray.MenuItem(lambda _: agent.user or "Žádný aktivní uživatel", None, enabled=False)))
            icon.run_detached()
            agent.notify = lambda message: icon.notify(message, "GPU Share • požadavek na inferenci")
        app = agent.app()
    try:
        web.run_app(app, host=cfg["bind"], port=cfg["port"], access_log=None)
    finally:
        if icon:
            icon.stop()


if __name__ == "__main__":
    main()

