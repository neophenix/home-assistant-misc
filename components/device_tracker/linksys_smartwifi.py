"""
Support for Linksys Access Points.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/device_tracker.linksys_ap/
"""
import base64
import logging
import threading
import asyncio
import aiohttp
import async_timeout
from collections import namedtuple
from datetime import timedelta

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.device_tracker import DOMAIN, PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL)
from homeassistant.util import Throttle
from homeassistant.helpers.aiohttp_client import async_create_clientsession

MIN_TIME_BETWEEN_SCANS = timedelta(seconds=5)

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Required(CONF_USERNAME): cv.string,
    vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
})


def get_scanner(hass, config):
    """Validate the configuration and return a Linksys Smart Wifi scanner."""
    try:
        return LinksysSmartWifiDeviceScanner(hass, config[DOMAIN])
    except ConnectionError:
        return None

Device = namedtuple('Device', ['mac', 'name', 'ip'])


class LinksysSmartWifiDeviceScanner(object):
    """This class queries a Linksys Access Point."""

    def __init__(self, hass, config):
        """Initialize the scanner."""
        self.host = config[CONF_HOST]
        self.username = config[CONF_USERNAME]
        self.password = config[CONF_PASSWORD]
        self.verify_ssl = config[CONF_VERIFY_SSL]

        self.websession = async_create_clientsession(hass, self.verify_ssl)

        self.last_results = []

        # Check if the access point is accessible
        self.success_init = self._update_info()

    @asyncio.coroutine
    def async_scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        info = self._update_info()
        if info is not None:
            yield from info

        return [device.mac for device in self.last_results]

    @asyncio.coroutine
    def async_get_device_name(self, mac):
        """Return the name (if known) of the device or None."""
        filter_named = [device.name for device in self.last_results
                        if device.mac == mac]

        if filter_named:
            return filter_named[0]
        else:
            return None

    @Throttle(MIN_TIME_BETWEEN_SCANS)
    def _update_info(self):
        _LOGGER.info("Connecting to Linksys Access Point Smart Wifi")

        self.last_results = []

        try:
            with async_timeout.timeout(10, loop=self.hass.loop):
                url = 'https://{}/JNAP/'.format(self.host)
                """Note the Auth here doesn't actually do anything and the call should succeed without it, awesome right? Keeping it in if Linksys fixes that"""
                headers = {
                    'X-JNAP-Action': 'http://linksys.com/jnap/core/Transaction',
                    'X-JNAP-Authorization': 'Basic ' + base64.b64encode(bytes(self.username + ':' + self.password, 'utf8')).decode('utf8')
                }
                payload = ('['
                    '{"action":"http://linksys.com/jnap/devicelist/GetDevices","request":{"sinceRevision":1653327}},'
                    '{"action":"http://linksys.com/jnap/networkconnections/GetNetworkConnections","request":{}}'
                ']')

                response = yield from self.websession.post(url, headers=headers, data=payload)

                if response.status != 200:
                    _LOGGER.warning("Error %d on %s", response.status, url)
                    return

                clients = yield from response.json()
        except asyncio.TimeoutError as e:
            _LOGGER.error("Timeout when connecting to Linksys Access Point")
            return False
        except aiohttp.ClientError as e:
            _LOGGER.error("Exception when connecting to Linksys Access Point: {0}".format(e))
            return False

        devices = {}
        for device in clients['responses'][0]['output']['devices']:
            device.setdefault('friendlyName', 'Unknown')
            for conn in device['connections']:
                devices[conn['macAddress']] = {}
                if 'ipAddress' in conn:
                    devices[conn['macAddress']]['name'] = device['friendlyName']
                    devices[conn['macAddress']]['ip'] = conn['ipAddress']

        for conn in clients['responses'][1]['output']['connections']:
            if 'wireless' in conn:
                if conn['macAddress'] in devices:
                    self.last_results.append(Device(conn['macAddress'].upper(), devices[conn['macAddress']]['name'], devices[conn['macAddress']]['ip']))
                else:
                    self.last_results.append(Device(conn['macAddress'].upper(), 'Unknown', 'Unknown'))

        return True
