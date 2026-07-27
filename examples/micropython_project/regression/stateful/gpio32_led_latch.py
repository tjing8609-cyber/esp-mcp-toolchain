from machine import Pin


LED_GPIO = 32
LED_ON = 0
LED_OFF = 1
led = Pin(LED_GPIO, Pin.OUT)
try:
    led.value(LED_ON)
    assert led.value() == LED_ON
finally:
    led.value(LED_OFF)
assert led.value() == LED_OFF
print("ESP_MCP_REG_GPIO32_LED_LATCH:PASS restored_off=1")
