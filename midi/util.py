#Adaptation/derivative of @vishnubob (Giles Hall) python MIDI system
#For BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard Greenberg
#Offered according to GNU Public License Version 3
#See LICENSE in project directory.
#


import six
def midi_byte2int(b):
    if six.PY3:
        return b
    else:
        return ord(b)

def midi_pack_bytes(bb):
    if six.PY3:
        return bytes(bb)
    else:
        return bytearray(bb)

def read_varlen(data):
    NEXTBYTE = 1
    value = 0
    while NEXTBYTE:
        chr = midi_byte2int(six.next(data))
        # is the hi-bit set?
        if not (chr & 0x80):
            # no next BYTE
            NEXTBYTE = 0
        # mask out the 8th bit
        chr = chr & 0x7f
        # shift last value up 7 bits
        value = value << 7
        # add new value
        value += chr
    return value

def write_varlen(value):
    chr1 = (value & 0x7F)
    value >>= 7
    if value:
        chr2 = (value & 0x7F) | 0x80
        value >>= 7
        if value:
            chr3 = (value & 0x7F) | 0x80
            value >>= 7
            if value:
                chr4 = (value & 0x7F) | 0x80
                res = midi_pack_bytes([chr4, chr3, chr2, chr1])
            else:
                res = midi_pack_bytes([chr3, chr2, chr1])
        else:
            res = midi_pack_bytes([chr2 , chr1])
    else:
        res = midi_pack_bytes([chr1])
    return res

