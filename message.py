import ctypes
import enum

class Command(enum.IntEnum):
    Allocation          = 1
    AllocationAck       = 2
    CreatePermission    = 3
    CreatePermissionAck = 4
    Refresh             = 5
    RefreshAck          = 6
    ConnectionAttamp    = 7
    ConnectionAttampAck = 8
    ConnectionBind      = 9
    ConnectionBindAck   = 10


class StructureBase:
    @classmethod
    def read(cls, buf):
        b = bytes(buf[:cls.size()])
        p = ctypes.cast(b, ctypes.POINTER(cls))
        instance = cls()
        ctypes.memmove(ctypes.byref(instance), p, cls.size())
        return instance
        # return copy.copy(p.contents)

    @classmethod
    def size(cls):
        return ctypes.sizeof(cls)

    def write(self):
        return bytes(self)


class Head(ctypes.Structure, StructureBase):
    SYNC_BYTE = 0xA5

    _fields_ = [
        ('sync', ctypes.c_byte),
        ('version', ctypes.c_byte),
        ('flag', ctypes.c_byte),
        ('reserved', ctypes.c_byte),
        ('command', ctypes.c_uint16),
        ('sequence', ctypes.c_uint16),
        ('payload_len', ctypes.c_uint32),
        ('reserved2', ctypes.c_uint32),
    ]

    def __init__(self, flag=0, command=0, sequence=0, payload_len=0):
        self.sync = self.SYNC_BYTE
        self.flag = flag
        self.command = command
        self.sequence = sequence
        self.payload_len = payload_len

    def __repr__(self):
        return '<Head(cmd:%d seq:%d payload_len:%d)>' % (self.command, self.sequence, self.payload_len)

