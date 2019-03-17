# -*- coding: utf-8 -*-

import mpd
import unittest
import mock
import asyncio

TEST_MPD_HOST, TEST_MPD_PORT = ('example.com', 10000)

class AsyncMockServer:
    def __init__(self):
        self._output = asyncio.Queue()
        self._expectations = []

    def get_streams(self):
        result = asyncio.Future()
        result.set_result((self, self))
        return result

    def readline(self):
        # directly passing around the awaitable
        return self._output.get()

    def write(self, data):
        try:
            next_write = self._expectations[0][0][0]
        except IndexError:
            self.error("Data written to mock even though none expected: %r" % data)
        if next_write == data:
            self._expectations[0][0].pop(0)
            self._feed()
        else:
            self.error("Mock got %r, expected %r" % (data, next_write))

    def error(self, message):
        raise AssertionError(message)

    def _feed(self):
        if len(self._expectations[0][0]) == 0:
            _, response_lines = self._expectations.pop(0)
            for l in response_lines:
                self._output.put_nowait(l)

    def expect_exchange(self, request_lines, response_lines):
        self._expectations.append((request_lines, response_lines))
        self._feed()

class TestAsyncioMPD(unittest.TestCase):
    def init_client(self, odd_hello=None):
        import mpd.asyncio

        self.loop = asyncio.get_event_loop()

        self.mockserver = AsyncMockServer()
        asyncio.open_connection = mock.MagicMock(return_value=self.mockserver.get_streams())

        if odd_hello is None:
            hello_lines = [b'OK MPD mocker\n']
        else:
            hello_lines = odd_hello

        self.mockserver.expect_exchange([], hello_lines)

        self.client = mpd.asyncio.MPDClient()
        self._await(self.client.connect(TEST_MPD_HOST, TEST_MPD_PORT, loop=self.loop))

        asyncio.open_connection.assert_called_with(TEST_MPD_HOST, TEST_MPD_PORT, loop=self.loop)

    def _await(self, future):
        return self.loop.run_until_complete(future)

    def test_oddhello(self):
        self.assertRaises(mpd.base.ProtocolError, self.init_client, odd_hello=[b'NOT OK\n'])

    @unittest.skip("This test would add 5 seconds of idling to the run")
    def test_noresponse(self):
        self.assertRaises(mpd.base.ConnectionError, self.init_client, odd_hello=[])

    def test_status(self):
        self.init_client()

        self.mockserver.expect_exchange([b"status\n"], [
            b"volume: 70\n",
            b"repeat: 0\n",
            b"random: 1\n",
            b"single: 0\n",
            b"consume: 0\n",
            b"playlist: 416\n",
            b"playlistlength: 7\n",
            b"mixrampdb: 0.000000\n",
            b"state: play\n",
            b"song: 4\n",
            b"songid: 19\n",
            b"time: 28:403\n",
            b"elapsed: 28.003\n",
            b"bitrate: 465\n",
            b"duration: 403.066\n",
            b"audio: 44100:16:2\n",
            b"OK\n",
            ])

        status = self._await(self.client.status())
        self.assertEqual(status, {
            'audio': '44100:16:2',
            'bitrate': '465',
            'consume': '0',
            'duration': '403.066',
            'elapsed': '28.003',
            'mixrampdb': '0.000000',
            'playlist': '416',
            'playlistlength': '7',
            'random': '1',
            'repeat': '0',
            'single': '0',
            'song': '4',
            'songid': '19',
            'state': 'play',
            'time': '28:403',
            'volume': '70',
            })

    def assertAllExceptionRetrieved(self, future):
        e = []
        def exception_handler(loop, ctx):
            if 'exception' in ctx:
                    e.append(ctx['exception'])
                    return
            loop.default_exception_handler(ctx)

        self.loop.set_exception_handler(exception_handler)
        self._await(future)
        self.loop.set_exception_handler(None)
        assert (not e), 'Task exception(s) never retrieved: %s' % str(e)

    def test_missed_exception_disconnect(self):
        """Does a client disconnect during idle is leaking exceptions?"""
        self.init_client()

        self.mockserver.expect_exchange([b"idle \"database\"\n"], [])
        self._await(asyncio.sleep(self.client.IMMEDIATE_COMMAND_TIMEOUT))
        self.client.disconnect()
        self.assertAllExceptionRetrieved(asyncio.sleep(0))

    def test_missed_exception_direct_commands(self):
        """Does a error during a direct command is leaking exceptions?"""
        self.init_client()

        async def await_playlistid():
            try:
                await self.client.playlistid(1)
            except:
                pass

        self.mockserver.expect_exchange([b"playlistid \"1\"\n"], [b"ACK [50@0] {} No such song\n"])
        self.mockserver.expect_exchange([b"idle \"database\"\n"], [])
        task = asyncio.ensure_future(await_playlistid())
        self.assertAllExceptionRetrieved(asyncio.sleep(.1))

    def test_mocker(self):
        """Does the mock server refuse unexpected writes?"""
        self.init_client()

        self.mockserver.expect_exchange([b"expecting odd things\n"], [b""])
        self.assertRaises(AssertionError, self._await, self.client.status())
