#BSG MIDI VPO Tools system (VPOMIDITools)
#Copyright (C) 2016-2020 by Bernard S. Greenberg
#Offered according to GNU Public License Version 3
#See file LICENSE in project directory.
#

import operator

class RegEvent:
    def __init__(self, point):
        self.point = point
        (self.measure, self.beat) = point
        self.tick = 0

    def tickize(self, tickmaster):
        if self.measure == 0 and self.beat == 0:  # can occur in piece with no measure #1
            self.tick = 0
        else:
            self.tick = tickmaster(self.measure, self.beat)

class ExpressionEvent(RegEvent):
    def __init__(self, point, division, value):
        RegEvent.__init__(self, point)
        self.division = division
        self.value = value

    def get_channel(self):
        return self.division.get_expression_channel()

    def get_expression_value(self):
        return self.value

class StopEvent(RegEvent):
    COMMANDS = ("Remove","Add")
    def __init__(self, point, status, stop):
        RegEvent.__init__(self, point)
        self.stop = stop
        self.status = status

    def execute(self):
        self.stop.set_status(self.status)
        return self.stop.execute(self.status, self.tick) #returns list of midi events

    def listing_describe(self):
        cmd = self.COMMANDS[int(self.status)]
        return "0     %d    Reg Change: %s %s" % (self.tick, cmd, str(self.stop))

class RoutingEvent(RegEvent):
    def __init__(self, point, staff, division):
        RegEvent.__init__(self, point)
        self.staff = staff
        self.division = division

    def get_division(self):
        return self.division
    def get_staff(self):
        return self.staff

class RegEventStack(list):
    def __init__(self):
        pass

    def mature(self, tick):
        if len(self) == 0:
            return False
        return tick >= self[0].tick

    def clone(self, clazz = None):
        new_stack = RegEventStack()
        for e in self:
            if (clazz is None) or isinstance(e, clazz):
                new_stack.append(e)
        return new_stack

    def add_reg(self, point, cstatus, stop):
        assert isinstance(cstatus, bool), "add_reg arg is not bool: %s" % status
        stop.cstatus = cstatus
        self.append(StopEvent(point, cstatus, stop))

    def add_rte(self, point, staff, division):
        self.append(RoutingEvent(point, staff, division))

    def add_expression(self, point, division, value):
         assert isinstance(value, int), "add_expression value is not int: %s" % status
         self.append(ExpressionEvent(point, division, value))

    def tickize(self, fcn):
        for rev in self:
            rev.tickize(fcn)
        self[:] = sorted(self, key=operator.attrgetter("tick"))
