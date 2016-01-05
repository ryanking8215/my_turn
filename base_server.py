import asyncio
import weakref

class BaseTcpSession:
    READ_SIZE = 1024

    def __init__(self, r, w, server, *, loop=None):
        self.r = r
        self.w = w
        self.server = server
        self.loop = loop
        self._is_closing = False

    def close(self):
        if self._is_closing:
            return

        self._is_closing = True
        self.w.close()

    @asyncio.coroutine
    def run(self):
        while True:
            try:
                b = yield from self.r.read(self.READ_SIZE)
                if len(b) == 0:
                    break

                r = self._process(b)
                if asyncio.iscoroutine(r):
                    yield from r
            except:
                break

        self.disconnected()
        self.close()

    def send(self, buf):
        self.w.write(buf)

    @asyncio.coroutine
    def sendall(self, buf):
        self.w.write(buf)
        yield from self.w.drain()

    def _process(self, buf):
        ''' 子类实现
        '''
        raise NotImplemented

    def connected(self):
        ''' 子类实现
        '''

    def disconnected(self):
        ''' 子类实现
        '''


class BaseTcpServer:
    session_class = BaseTcpSession

    def __init__(self, port=None, *, loop=None, **kw):
        self._server_coro = asyncio.start_server(self.client_connected_cb, port=port, loop=loop, **kw)
        self.loop = loop
        self._server = None

    def start(self):
        asyncio.async(self.run())
        
    @asyncio.coroutine
    def run(self):
        self._server = yield from self._server_coro
        print("%s run" % self)

    def client_connected_cb(self, reader, writer):
        session = self.session_class(reader, writer, weakref.proxy(self), loop=self.loop)
        session.connected()
        asyncio.async(session.run())

    def close(self):
        if self._server:
            self._server.close()

    def getsockname(self):
        if self._server:
            return self._server.sockets[0].getsockname()
        return None

    @property
    def port(self):
        r = self.getsockname()
        if r is not None:
            return r[1]
        return None

    def __repr__(self):
        return '<{} {}>'.format(self.__class__.__name__, self.getsockname())  