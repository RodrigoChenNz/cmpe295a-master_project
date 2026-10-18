import time

class I2cLcd:
    def __init__(self, i2c, addr, cols=16):
        self.i2c = i2c
        self.addr = addr
        self.cols = cols
        self.bl = 0x08  # backlight on
        time.sleep_ms(50)
        self._nibble(0x30, 0); time.sleep_ms(5)
        self._nibble(0x30, 0); time.sleep_ms(1)
        self._nibble(0x30, 0); time.sleep_ms(1)
        self._nibble(0x20, 0)           # switch to 4-bit mode
        self._send(0x28, 0)             # 2 lines, 5x8 font
        self._send(0x0C, 0)             # display on, cursor off
        self._send(0x06, 0)             # cursor moves right
        self.clear()

    def _nibble(self, hi, rs):
        d = (hi & 0xF0) | rs | self.bl
        self.i2c.writeto(self.addr, bytes([d | 0x04]))   # E high
        time.sleep_us(2)
        self.i2c.writeto(self.addr, bytes([d & 0xFB]))   # E low
        time.sleep_us(50)

    def _send(self, value, rs):
        self._nibble(value & 0xF0, rs)
        self._nibble((value << 4) & 0xF0, rs)

    def clear(self):
        self._send(0x01, 0)
        time.sleep_ms(2)

    def write_line(self, row, text):
        text = (str(text) + " " * self.cols)[:self.cols]
        self._send(0x80 | (0x40 if row else 0x00), 0)
        for ch in text:
            o = ord(ch)
            self._send(o if o < 128 else 63, 1)
