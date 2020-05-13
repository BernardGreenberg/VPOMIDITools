#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import re

# "Barfu III" could be a three-rank mixture, or a type of coupler involving Manual III...

def key(stop):
    s = stop.main_name.replace(".", "")
    s = s.replace("'", "")
    comps = s.lower().split()
    if not comps:
        return (1, 0, s)
    # F can be Forte. R can be a division name.
    if len(comps) > 1 and comps[-1] in {"rgs", "rang", "rangs", "ranks", "rk", "f", "fach", "ranks", "st", "sterk", "file"}:
        return mixtur(s, comps[-2])
    if re.match(r"^[ivx\-]+$", comps[-1]):
        return mixtur(s, comps[-1])
    if re.match(r"\d*-?\d*/?\d+", s[-1]):
        return normal(s, comps[-1])
    return (10, 0, s)

def normal(s, footexp):
    m = re.match(r"^(\d+)-(\d+)/(\d+)$", footexp)
    if m:
        fm = list(map(float, m.groups()))
        return (3, -(fm[0] + fm[1]/fm[2]), s)
    m = re.match(r"^(\d+)/(\d+)$", footexp)
    if m:
        fm = list(map(float, m.groups()))
        return (3, -(fm[0]/fm[1]), s)
    m = re.match(r"^\d+$", footexp)
    if m:
        return (3, -float(footexp), s)
    return (4, 0, s)

# Very few 15-rank mixtures out there ...
ROME = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv"]
    

def deroman(s):
    return 1 + ROME.index(s)

def mixtur(s, rkexp):
    m = re.match("^([ivx]+)-([ivx]+)$", rkexp)
    if m:
        return (5, -max(map(deroman, m.groups())), s)
    m = re.match("^[ivx]+$", rkexp)
    if m:
        return (5, -deroman(rkexp), s)
    m = re.match(r"^(\d+)-(\d+)$", rkexp)
    if m:
        return (5, -max(map(float, m.groups())), s)
    m = re.match(r"\d+", rkexp)
    if m:
        return (5, -float(rkexp), s)
    return (6, 0, s)
