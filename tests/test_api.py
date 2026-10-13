"""Exercise real HTTP responses and transport failure handling."""
import unittest

import aiohttp
from aiohttp import web

from custom_components.blebox_smartwindowbox.api import BleBoxApiError, BleBoxSmartWindowBoxApi


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.response = web.json_response({'window': {'motors': []}})
        async def respond(request):
            return self.response
        app = web.Application()
        app.router.add_get('/api/window/extended/state', respond)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.session = aiohttp.ClientSession()
        self.api = BleBoxSmartWindowBoxApi(self.session, f'127.0.0.1:{self.port}')

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def test_valid_json_without_content_type(self):
        self.response = web.Response(text='{"window": {"motors": []}}')
        self.assertEqual(await self.api.window_extended_state(), {'window': {'motors': []}})

    async def test_invalid_json_is_api_error(self):
        self.response = web.Response(text='not JSON')
        with self.assertRaises(BleBoxApiError):
            await self.api.window_extended_state()

    async def test_non_object_json_is_api_error(self):
        self.response = web.json_response([])
        with self.assertRaisesRegex(BleBoxApiError, 'expected a JSON object'):
            await self.api.window_extended_state()

    async def test_http_error_is_api_error(self):
        self.response = web.Response(status=503)
        with self.assertRaisesRegex(BleBoxApiError, 'HTTP 503'):
            await self.api.window_extended_state()

    async def test_disconnection_is_api_error(self):
        await self.runner.cleanup()
        with self.assertRaises(BleBoxApiError):
            await self.api.window_extended_state()
