# keep_alive.py
from aiohttp import web
import asyncio

async def handle(request):
    return web.Response(text="Bot is alive!")

async def run_webserver():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("🌐 Keep alive webserver started on port 8080")

def keep_alive():
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(run_webserver())
    else:
        loop.run_until_complete(run_webserver())
