from machine import Pin


KEY_GPIO = 34
key = Pin(KEY_GPIO, Pin.IN)
key_level = key.value()
assert key_level in (0, 1)
print("ESP_MCP_REG_GPIO34_KEY_READ:PASS level={}".format(key_level))
