# Hack to update stm32f0 clock rate
#
# Copyright (C) 2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import mcu

RCC_CR_ADDR = 0x40021000
TRIM_STEP = 6 * 40000

class ClockTrim:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        mcu_name = config.get('mcu', 'mcu')
        self.static_trim = config.getint('trim', None, minval=0, maxval=31)
        self.mcu = mcu.get_printer_mcu(printer, mcu_name)
        self.debug_write_cmd = self.debug_read_cmd = None
        self.rcc_cr = self.freq = None
        # Register callbacks
        printer.register_event_handler("klippy:mcu_identify",
                                       self._mcu_identify)
        printer.register_event_handler("klippy:connect",
                                       self._connect)
    def _mcu_identify(self):
        mcu_type = self.mcu.get_constants().get("MCU", "")
        if not mcu_type.startswith('stm32f0'):
            raise self.printer.config_error("stm32f0_trim on non-stm32f0 mcu")
        cmd_queue = self.mcu.alloc_command_queue()
        self.debug_read_cmd = self.mcu.lookup_query_command(
            "debug_read order=%c addr=%u", "debug_result val=%u", cq=cmd_queue)
        self.debug_write_cmd = self.mcu.lookup_command(
            "debug_write order=%c addr=%u val=%u", cq=cmd_queue)
        params = self.debug_read_cmd.send([2, RCC_CR_ADDR])
        self.rcc_cr = params['val']
        self.freq = self.mcu.seconds_to_clock(1.)
        logging.info("stm32f0 trim rcc_cr=0x%x freq=%d", self.rcc_cr, self.freq)
        if self.static_trim is not None:
            self.rcc_cr = (self.rcc_cr & ~(0x1f <<3)) | (self.static_trim << 3)
            logging.info("Setting HSI trim of %d (0x%x)",
                         self.static_trim, self.rcc_cr)
            self.debug_write_cmd.send([2, RCC_CR_ADDR, self.rcc_cr])
    def _connect(self):
        if self.static_trim is not None:
            return
        reactor = self.printer.get_reactor()
        reactor.register_timer(self._calibrate, reactor.monotonic() + 60.)
    def _calibrate(self, eventtime):
        if self.printer.is_shutdown():
            return self.printer.get_reactor().NEVER
        clocksync = self.mcu._clocksync # XXX
        c1 = clocksync.get_clock(eventtime)
        c2 = clocksync.get_clock(eventtime + 1.)
        est_freq = c2 - c1
        if abs(est_freq - self.freq) < 1.25*TRIM_STEP:
            return eventtime + 10.
        trim = (self.rcc_cr >> 3) & 0x1f
        if est_freq < self.freq:
            trim += 1
        else:
            trim -= 1
        if trim > 0x1f or trim < 0:
            return eventtime + 10.
        self.rcc_cr = (self.rcc_cr & ~(0x1f <<3)) | (trim << 3)
        logging.info("Setting new HSI trim of %d (0x%x) for freq %d",
                     trim, self.rcc_cr, est_freq)
        self.debug_write_cmd.send([2, RCC_CR_ADDR, self.rcc_cr])
        return eventtime + 60.

def load_config_prefix(config):
    return ClockTrim(config)
