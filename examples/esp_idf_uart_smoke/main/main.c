#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "uart_only_smoke";

void app_main(void)
{
    unsigned long heartbeat = 0;

    ESP_LOGI(TAG, "ESP_MCP_UART_ONLY_READY");
    while (true) {
        ESP_LOGI(TAG, "ESP_MCP_UART_ONLY_HEARTBEAT=%lu", heartbeat++);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
