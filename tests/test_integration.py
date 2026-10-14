"""Verify actual Home Assistant device/entity registration against a local API."""
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry

DOMAIN = 'blebox_smartwindowbox'


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cover_and_rain_sensor_registered_under_one_device(self):
        async def device(request):
            return web.json_response({'device': {
                'deviceName': 'Test window', 'type': 'smartWindowBox',
                'apiLevel': '20180604', 'id': 'test-device', 'hv': '1.0', 'fv': '2.0',
            }})
        async def state(request):
            return web.json_response({'window': {
                'motors': [{'id': 0, 'name': 'Motor 1', 'state': 2, 'currentPos': {'position': 40}}],
                'sensors': [{'id': 0, 'type': 'rain', 'value': 1}],
            }})
        app = web.Application()
        app.router.add_get('/api/device/state', device)
        app.router.add_get('/api/window/extended/state', state)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            with tempfile.TemporaryDirectory() as config:
                components = Path(config) / 'custom_components'
                components.mkdir()
                source = Path(__file__).resolve().parents[1] / 'custom_components' / DOMAIN
                (components / DOMAIN).symlink_to(source)
                hass = HomeAssistant(config)
                loader.async_setup(hass)
                try:
                    self.assertIsNotNone(await bootstrap.async_from_config_dict({
                        'homeassistant': {'name': 'Device test', 'latitude': 52.0,
                                          'longitude': 5.0, 'elevation': 0,
                                          'time_zone': 'Europe/Amsterdam', 'unit_system': 'metric'},
                    }, hass))
                    await hass.async_start()
                    result = await hass.config_entries.flow.async_init(
                        DOMAIN, context={'source': 'user'}, data={'host': f'127.0.0.1:{port}'},
                    )
                    self.assertEqual(result['type'], 'create_entry', result)
                    entry = result['result']
                    await hass.async_block_till_done()
                    self.assertEqual(entry.state.value, 'loaded')
                    entities = entity_registry.async_entries_for_config_entry(
                        entity_registry.async_get(hass), entry.entry_id,
                    )
                    self.assertEqual(len(entities), 2)
                    self.assertEqual({e.unique_id for e in entities}, {
                        f'{entry.entry_id}_motor_0', f'{entry.entry_id}_rain_0',
                    })
                    devices = {e.device_id for e in entities}
                    self.assertEqual(len(devices), 1)
                    self.assertNotIn(None, devices)
                    device_id = devices.pop()
                    registered_device = device_registry.async_get(hass).async_get(device_id)
                    self.assertEqual(registered_device.identifiers, {(DOMAIN, 'test-device')})
                    self.assertEqual(registered_device.manufacturer, 'BleBox')
                    self.assertEqual(registered_device.model, 'smartWindowBox')
                    self.assertEqual(registered_device.sw_version, '2.0')
                    cover = next(e for e in entities if e.entity_id.startswith('cover.'))
                    self.assertEqual(hass.states.get(cover.entity_id).attributes['device_class'], 'window')
                    self.assertEqual(hass.states.get(cover.entity_id).attributes['current_position'], 60)
                    before = {e.unique_id: e.entity_id for e in entities}
                    self.assertTrue(await hass.config_entries.async_reload(entry.entry_id))
                    await hass.async_block_till_done()
                    after = entity_registry.async_entries_for_config_entry(entity_registry.async_get(hass), entry.entry_id)
                    self.assertEqual({e.unique_id: e.entity_id for e in after}, before)
                    self.assertEqual({e.device_id for e in after}, {device_id})
                finally:
                    await hass.async_stop()
        finally:
            await runner.cleanup()
