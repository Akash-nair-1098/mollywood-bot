from aiohttp import web

async def handle(request):
    return web.Response(text="✅ Bot is alive!")

async def keep_alive():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("🌐 Keep-alive server running on port 8080")
