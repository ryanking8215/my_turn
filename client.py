import asyncio
import json
import logging
import weakref
import sys
import time

import message as msg

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)-8s  %(message)s', level=logging.INFO)
logging.getLogger('asyncio').setLevel(logging.WARN)
log = logging.getLogger('client')

class BaseTcpClient:
    READ_SIZE = 4096 

    def __init__(self, host, port, *, loop=None):
        self.host = host
        self.port = port
        self.loop = loop
        self._is_closing = False
        self.r = None
        self.w = None
        self._read_task = None

    def start(self):
        asyncio.async(self.connect())

    @asyncio.coroutine
    def connect(self):
        self.r, self.w = yield from asyncio.open_connection(self.host, self.port, loop=self.loop)
        self.connected()
        self._read_task = asyncio.async(self._read_loop())

    def close(self):
        if self._is_closing:
            return
        self._is_closing = True

        if self.w:
            self.w.close()
        if self._read_task:
            self._read_task.cancel()

        self.disconnected()

    @asyncio.coroutine
    def _read_loop(self):
        while True:
            try:
                buf = yield from self.r.read(self.READ_SIZE)
                if len(buf) == 0:
                    break

                r = self._process(buf)
                if asyncio.iscoroutine(r):
                    yield from r
            except:
                break

        self.close()

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

    def send(self, buf):
        self.w.write(buf)

    @asyncio.coroutine
    def sendall(self, buf):
        self.w.write(buf)
        yield from self.w.drain()


class BaseDataClient(BaseTcpClient):
    MAX_SEQ = 2*16

    def __init__(self, connection_id, host, port, *, loop=None):
        super().__init__(host, port, loop = loop)
        self.connection_id = connection_id
        self._seq = 0
        self._is_binded = False
        self._buf = bytearray()

    def start(self):
        asyncio.async(self.connect(), loop=self.loop)

    def connected(self):
        log.info("data client connected")
        self.bind()

    def disconnected(self):
        pass

    @asyncio.coroutine
    def _process(self, buf):
        if self._is_binded:
            if len(self._buf)>0:
                r = self.process_binded_data(self._buf)
                if asyncio.iscoroutine(r):
                    yield from r
                del self._buf[:]
            else:
                r = self.process_binded_data(buf)
                if asyncio.iscoroutine(r):
                    yield from r
            return

        self._buf.extend(buf)
        buf = self._buf

        while True:
            if len(buf) < msg.Head.size():
                break
            if buf[0] != msg.Head.SYNC_BYTE:
                del buf[0]
                continue

            head = msg.Head.read(buf)
            if head.payload_len + msg.Head.size() < len(buf):
                break

            payload = buf[msg.Head.size():msg.Head.size()+head.payload_len]
            # process command
            try:
                cmd = msg.Command(head.command)
                cmd_str = 'process_%s' % cmd.name
                processor = getattr(self, cmd_str)
                if asyncio.iscoroutinefunction(processor):
                    yield from processor(head, payload)
                else:
                    processor(head, payload)
            except:
                pass

            del buf[:head.payload_len+msg.Head.size()]

    def bind(self):
        log.info("send bind request")
        head = msg.Head(command=msg.Command.ConnectionBind)
        req = {
            'connection_id' : self.connection_id
        }
        self.send_request(head, json.dumps(req).encode())

    def _send_msg(self, head, payload):
        head.payload_len = len(payload)
        buf = bytearray()
        buf.extend(head.write())
        buf.extend(payload)
        self.w.write(buf)

    def send_request(self, head, payload):
        head.sequence = self._gen_seq()
        self._send_msg(head, payload)

    def _gen_seq(self):
        self._seq = self._seq+1
        if self._seq >= self.MAX_SEQ:
            self._seq = 0
        return self._seq

    def process_ConnectionBindAck(self, head, payload):
        try:
            payload = json.loads(payload.decode())
            if payload['code'] == 200:
                self._is_binded = True

            log.info("bind ok")
            return
        except BaseException as e:
            log.error(e)

        log.warn("failed")
        # raise RuntimeError("bind error")

    def process_binded_data(self, buf):
        ''' 处理bind后的数据，即relay的数据
        '''
        raise NotImplemented


class LocalClient(BaseTcpClient):
    ''' 连接本地端口的客户端
    '''
    def __init__(self, port, *, loop=None):
        super().__init__('127.0.0.1', port, loop=loop)
        self._data_client = None

    def set_data_client(self, data_client):
        self._data_client = data_client

    def disconnected(self):
        log.info("local client disconnect")
        if self._data_client is None:
            return

        d = self._data_client()
        if d:
            d.close()

    def _process(self, buf):
        if self._data_client is None:
            return

        d = self._data_client()
        if d:
            d.send(buf)


class LocalDataClient(BaseDataClient):
    ''' 连接本地端口和数据通道的类
    '''
    LOCAL_PORT = 22

    @classmethod
    def set_local_port(cls, port):
        cls.LOCAL_PORT = port

    def __init__(self, connection_id, host, port, *, loop=None):
        super().__init__(connection_id, host, port, loop=loop)
        self._local_client = None

    @asyncio.coroutine
    def process_ConnectionBindAck(self, head, payload):
        super().process_ConnectionBindAck(head, payload)
        if self._is_binded:
            try:
                self._local_client = LocalClient(self.LOCAL_PORT, loop=self.loop)
                self._local_client.set_data_client(weakref.ref(self))
                yield from self._local_client.connect()
                log.info("local client connect")
            except BaseException as e:
                log.error(e)

    def process_binded_data(self, buf):
        if self._local_client:
            self._local_client.send(buf)

    def disconnected(self):
        log.warn("data client disconnect")
        super().disconnected()
        if self._local_client:
            self._local_client.close()


class MyTurnClient(BaseTcpClient):
    MAX_SEQ = 2**16
    data_client_class = LocalDataClient

    def set_cb(self, disconnected_cb):
        self._disconnected_cb = disconnected_cb

    def connected(self):
        self._buf = bytearray()
        self._seq = 0
        self._is_allocated = False

        # {connection_id: data_client}
        self._data_clients = {}

        self._refresh_ack_ev = asyncio.Event()
        self.allocate()

    def disconnected(self):
        log.warn("turn client disconnected")
        if hasattr(self, "_live_task"):
            self._live_task.cancel()

        if hasattr(self, "_disconnected_cb"):
            self._disconnected_cb(self)

    def _process(self, buf):
        self._buf.extend(buf)
        buf = self._buf

        while True:
            if len(buf) < msg.Head.size():
                break
            if buf[0] != msg.Head.SYNC_BYTE:
                del buf[0]
                continue

            head = msg.Head.read(buf)
            if head.payload_len + msg.Head.size() < len(buf):
                break

            payload = buf[msg.Head.size():msg.Head.size()+head.payload_len]
            # process command
            try:
                cmd = msg.Command(head.command)
                cmd_str = 'process_%s' % cmd.name
                getattr(self, cmd_str)(head, payload)
            except:
                pass

            del buf[:head.payload_len+msg.Head.size()]

    def allocate(self):
        head = msg.Head(command=msg.Command.Allocation)
        req = {
            'software': '0.0.1',
        }
        self._send_msg(head, json.dumps(req).encode())


    def process_AllocationAck(self, head, payload):
        payload = json.loads(payload.decode())
        if payload['code'] != 200:
            raise RuntimeError("allocation failed")

        relay_address = payload['relay_address']
        log.info("allocate ok: %s" % relay_address)
        self._is_allocated = True

        self._live_task = asyncio.async(self._live_loop())

    def process_ConnectionAttamp(self, head, payload):
        log.info("process ConnectionAttamp")
        payload = json.loads(payload.decode())
        log.debug(payload)
        connection_id = payload.get('connection_id')
        data_address = payload.get('data_address')
        if connection_id is None or data_address is None:
            raise RuntimeError("connection attamp failed")

        try:
            v = data_address.split(':')
            host = v[0]
            port = int(v[1])
            log.info("data server: %s %d" % (host, port))
            dc = self.data_client_class(connection_id, host, port, loop=self.loop)
            dc.start()
        except Exception as e:
            log.error(e)

        self._data_clients[connection_id] = dc

    def process_RefreshAck(self, head, payload):
        log.debug("process Refresh Ack")
        payload = json.loads(payload.decode())
        self._refresh_ack_ev.set()

    def _send_msg(self, head, payload):
        head.payload_len = len(payload)
        buf = bytearray()
        buf.extend(head.write())
        buf.extend(payload)
        self.w.write(buf)

    def send_request(self, head, payload):
        head.sequence = self._gen_seq()
        self._send_msg(head, payload)

    def _gen_seq(self):
        self._seq = self._seq+1
        if self._seq >= self.MAX_SEQ:
            self._seq = 0
        return self._seq

    @asyncio.coroutine
    def _live_loop(self):
        log.info("start refresh task")
        head = msg.Head(command=msg.Command.Refresh)
        req = {}
        err_times = 0
        while True:
            log.debug("turn client refresh")
            self.send_request(head, json.dumps(req).encode())
            try:
                yield from asyncio.wait_for(self._refresh_ack_ev.wait(), timeout=5.0)
                err_times = 0
            except Exception as e:
                err_times = err_times+1
                log.warn("turn client refresh error times:%d" % err_times)

            if err_times>=5:
                log.error("Turn client refresh failed too many times")
                break

            yield from asyncio.sleep(30.0)

        self.close()


class ForeverMyTurnClient:
    def __init__(self, host, port, *, loop=None):
        self.host = host
        self.port = port
        self.loop = loop
        self._client = None

    def start(self):
        asyncio.async(self.run())

    @asyncio.coroutine
    def run(self):
        if self._client:
            return

        self._client = MyTurnClient(args.host, args.port, loop=loop)
        self._client.set_cb(self.on_client_disconnected)
        while True:
            try:
                yield from asyncio.wait_for(self._client.connect(), 10.0)
                break
            except:
                log.error("turn client connect failed")
                yield from asyncio.sleep(5.0)


    def on_client_disconnected(self, client):
        self._client = None
        self.loop.call_later(5.0, self.start)
        

if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="turn server host")
    parser.add_argument("port", help="turn server port", type=int)
    parser.add_argument("lport", help="local server port", type=int)
    args = parser.parse_args()
    log.info("host:%s port:%d local_port:%d" % (args.host, args.port, args.lport))

    try:
        LocalDataClient.set_local_port(args.lport)
        loop = asyncio.get_event_loop()
        ftc = ForeverMyTurnClient(args.host, args.port, loop=loop)
        ftc.start()
        loop.run_forever()
    except Exception as e:
        log.error(">>>>>>>>>>>>> quit", e)
        time.sleep(5.0)
