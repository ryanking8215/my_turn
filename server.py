import asyncio
import json
import uuid
import os
import weakref
import random
import logging
import socket
import copy

from .base_server import *
from . import message as msg

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)-8s  %(message)s', level=logging.INFO)
logging.getLogger('asyncio').setLevel(logging.WARN)
log = logging.getLogger('server')

# 是否启用保活检测
USE_TURN_CLINT_LIVE_CHECK = False

class DataSession(BaseTcpSession):
    ''' 数据通道会话, 即内网机器服务的数据通道
    '''
    def connected(self):
        self._buf = bytearray()
        self.connection_id = None
        self._relay_session = None
        log.info("%r connected" % self)

    def disconnected(self):
        log.warn("%r disconnected" % self)
        try:
            self.server.allocation.del_session(self.connection_id)
            # del self.server.allocation.data_sessions[self.connection_id]
            # if self._relay_session:
            #     self._relay_session.close()
            #     self._relay_session = None
        except:
            pass

    def _process(self, buf):
        if self._relay_session:
            if len(self._buf)>0:
                self._relay_session.send(self._buf)
                del self._buf[:]
            self._relay_session.send(buf)
        else:
            # 接收并处理ConnectionBind协议
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

    def process_ConnectionBind(self, head, payload):
        log.info("%r process bind" % self)
        try:
            payload = json.loads(payload.decode())
            connection_id = payload.get('connection_id')
            self.connection_id = connection_id

            relay_session = self.server.allocation.relay_sessions.get(connection_id)
            data_session = self.server.allocation.data_sessions.get(connection_id)
            if connection_id and relay_session and data_session is None:
                relay_session.set_data_session(weakref.proxy(self))
                self.set_relay_session(weakref.proxy(relay_session))
                self.server.allocation.data_sessions[connection_id] = self
                rsp = {
                    'code': 200
                }
                log.info("bind ok")
            else:
                rsp = {
                    'code': 400
                }

            self.send_response(head, json.dumps(rsp).encode())
        except Exception as e:
            log.error(e)

    def set_relay_session(self, relay_session):
        self._relay_session = relay_session

    def _send(self, head, payload):
        head.payload_len = len(payload)
        buf = bytearray()
        buf.extend(head.write())
        buf.extend(payload)
        self.w.write(buf)

    def send_response(self, req_head, payload):
        head = copy.deepcopy(req_head)
        head.command = req_head.command+1

        self._send_msg(head, payload)

    def _send_msg(self, head, payload):
        head.payload_len = len(payload)
        buf = bytearray()
        buf.extend(head.write())
        buf.extend(payload)
        self.w.write(buf)

    def __repr__(self):
        return '<DataSession(id:{}, {})>'.format(self.connection_id, self.w.get_extra_info('peername'))

class DataServer(BaseTcpServer):
    session_class = DataSession

    def __init__(self, allocation, port=None, *, loop=None, sock=None):
        super().__init__(port, loop=loop, sock=sock)
        self.allocation = allocation



class RelaySession(BaseTcpSession):
    ''' 中继会话， 即(外部)用户客户端连接上来的会话
    '''
    def connected(self):
        self._buf = bytearray()
        self.connection_id = str(uuid.uuid4())
        self._data_session = None
        # log.info("relay session connected %s server:%r" % (self.connection_id, self.server))
        self.server.allocation.relay_connected(self)
        log.info('%r connected' % self)

    def disconnected(self):
        log.warn("%r disconnected" % self)
        try:
            self.server.allocation.del_session(self.connection_id)
            # del self.server.allocation.relay_sessions[self.connection_id]
            # if self._data_session:
            #     self._data_session.close()
            #     self._data_session = None
        except:
            pass

    def _process(self, buf):
        if self._data_session:
            if len(self._buf)>0:
                self._data_session.send(self._buf)
                del self._buf[:]
            self._data_session.send(buf)
        else:
            self._buf.extend(buf)

    def set_data_session(self, data_session):
        self._data_session = data_session

    def __repr__(self):
        return '<RelaySession(id:{}, l:{} r:{})>'.format(self.connection_id, self.w.get_extra_info('peername'), self.w.get_extra_info('sockname'))
        # return '<RelaySession(id:%s)>' % self.connection_id


class RelayServer(BaseTcpServer):
    session_class = RelaySession

    def __init__(self, allocation, port=None, *, loop=None, sock=None):
        super().__init__(port, loop=loop, sock=sock)
        self.allocation = allocation


class Allocation:
    def __init__(self, turn_session):
        self.relay_server = None
        self.data_server  = None
        self.turn_session = turn_session

        # { connection_id: relay_session}
        self.relay_sessions = {}
        # { connection_id: data_session}
        self.data_sessions = {}

    @asyncio.coroutine
    def start_relay_server(self):
        port, sock = FindTcpPortByBind.find()
        self.relay_server = RelayServer(self, None, loop=self.turn_session.loop, sock=sock)
        yield from self.relay_server.run()
        # self.relay_server.start()
        # log.info("%r run" % self.relay_server)

    @asyncio.coroutine
    def start_data_server(self):
        port, sock = FindTcpPortByBind.find()
        self.data_server = DataServer(self, None, loop=self.turn_session.loop, sock=sock)
        yield from self.data_server.run()
        # self.relay_server.start()
        # log.info("%r run" % self.data_server)

    def relay_connected(self, relay_session):
        self.relay_sessions[relay_session.connection_id] = relay_session

        # send ConnectionAttamp
        log.info("send ConnectionAttamp")
        head = msg.Head(command=msg.Command.ConnectionAttamp)
        rsp = {
            'connection_id': relay_session.connection_id,
            'data_address': '%s:%d' % (self.turn_session.server.server_host, self.data_server.port)
        }
        self.turn_session.send_request(head, json.dumps(rsp).encode())

    def close(self):
        log.warn("%r close my sessions" % self)
        # close all sessions
        for s in self.relay_sessions.values():
            s.close()
        for s in self.data_sessions.values():
            s.close()

        log.warn("%r close my relay and data server" % self)
        if self.relay_server:
            self.relay_server.close()
        if self.data_server:
            self.data_server.close()


    def close_connection(self, connection_id, remove=True):
        v = self.relay_data_map.get(connection_id)
        if v:
            relay_session = v[0]
            data_session = v[1]
            if relay_session:
                relay_session.close()
            if data_session:
                data_session.close()

            if remove:
                del self.relay_data_map[connection_id]

    def del_session(self, connection_id):
        data_session = self.data_sessions.get(connection_id)
        if data_session:
            data_session.close()

        relay_session = self.relay_sessions.get(connection_id)
        if relay_session:
            relay_session.close()

    def __repr__(self):
        return '<Allocation(session:%r)>' % self.turn_session.__repr__()


class MyTurnSession(BaseTcpSession):
    ''' 转发服务连接上来的会话
    '''
    MAX_SEQ = 2**16

    def connected(self):
        log.info('%r connected' % self)
        self._allocation = None
        self._seq = 0
        self._buf = bytearray()
        self._live_watchdog = 0
        if USE_TURN_CLINT_LIVE_CHECK:
            self._live_check_task = asyncio.async(self._live_check())

    def disconnected(self):
        log.warn("%r disconnected" % self)
        if self._allocation:
            self._allocation.close()
        if hasattr(self, '_live_check_task'):
            self._live_check_task.cancel()

    @asyncio.coroutine
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
                processer = getattr(self, cmd_str)
                if asyncio.iscoroutinefunction(processer):
                    yield from processer(head, payload)
                else:
                    processer(head, payload)
            except:
                pass

            del buf[:head.payload_len+msg.Head.size()]

    @asyncio.coroutine
    def process_Allocation(self, head, payload):
        if self._allocation is not None:
            rsp = {
                'code': 400,
            }
        else:
            payload = json.loads(payload.decode())

            self._allocation = Allocation(self)
            # start relay and data server
            try:
                yield from self._allocation.start_relay_server()
                yield from self._allocation.start_data_server()
            except Exception as e:
                log.error(e)
                

            rsp = {
                'code': 200,
                'relay_address': '%s:%d' % (self.server.server_host, self._allocation.relay_server.port)
            }

        self.send_response(head, json.dumps(rsp).encode())

    def process_CreatePermission(self, head, payload):
        rsp = {
            'code': 200,
        }
        self.send_response(head, json.dumps(rsp).encode())

    def process_Refresh(self, head, paylod):
        rsp = {
            'code': 200,
        }
        self.send_response(head, json.dumps(rsp).encode())
        log.debug("%r feed" % self)
        self._live_watchdog = 0

    def send_response(self, req_head, payload):
        head = copy.deepcopy(req_head)
        head.command = req_head.command+1

        self._send_msg(head, payload)

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

    def _live_check(self):
        self._live_watchdog = 0
        while True:
            log.debug("%r check %d" % (self, self._live_watchdog))
            self._live_watchdog = self._live_watchdog+1
            yield from asyncio.sleep(1.0)
            if self._live_watchdog>=120:
                break

        log.warn("%r live check timeout" % self)
        self.close()

    def __repr__(self):
        return "<TurnSession({})>".format(self.w.get_extra_info('peername'))


class FindTcpPortByNetstat:
    MIN = 15000
    MAX = 25000

    @classmethod
    def find(cls):
        cmd = "netstat -ntl |grep -v Active| grep -v Proto|awk '{print $4}'|awk -F: '{print $NF}'"
        with os.popen(cmd) as f:
            procs = f.read()
            procs_arr = procs.split('\n')

        count = 0
        while count<(cls.MAX-cls.MIN):
            tt = random.randint(cls.MIN, cls.MAX)
            if tt not in procs_arr:
                return tt
            else:
                count=count+1
                continue

        return None


class FindTcpPortByBind:
    @classmethod
    def find(cls):
        s = socket.socket() # default is TCP
        s.bind(('0.0.0.0', 0))
        return s.getsockname()[1], s

        
class MyTurnServer(BaseTcpServer):
    session_class = MyTurnSession
    server_host = '192.168.128.134'

    def __init__(self, port, loop=None, server_host=None):
        super().__init__(port, loop=loop)
        if server_host is not None:
            self.server_host = server_host


if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="turn server host")
    args = parser.parse_args()
    log.info("host:%s" % args.host)

    loop = asyncio.get_event_loop()
    srv = MyTurnServer(5555, loop=loop, server_host=args.host)
    srv.start()
    loop.run_forever()