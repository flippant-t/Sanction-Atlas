"""Render site/og.png (1200x630) from the built site for link previews. Optional."""
import asyncio, os, subprocess, sys, time
from playwright.async_api import async_playwright
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
async def main():
    srv = subprocess.Popen([sys.executable, "-m", "http.server", "8123"], cwd=SITE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page(viewport={"width": 1500, "height": 630})
            await pg.goto("http://localhost:8123/#lines=link")
            await pg.wait_for_selector("#loading", state="hidden", timeout=120000)
            await pg.wait_for_timeout(1500)
            await pg.screenshot(path=os.path.join(SITE, "og.png"), clip={"x": 300, "y": 0, "width": 1200, "height": 630})
            await b.close()
    finally:
        srv.terminate()
asyncio.run(main())
