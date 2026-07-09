#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define BOOT_GPIO GPIO_NUM_0
#define LED_GPIO GPIO_NUM_32
#define BUZZER_GPIO GPIO_NUM_25

#define LED_ON_LEVEL 0
#define LED_OFF_LEVEL 1

#define BUZZER_LEDC_MODE LEDC_LOW_SPEED_MODE
#define BUZZER_LEDC_TIMER LEDC_TIMER_0
#define BUZZER_LEDC_CHANNEL LEDC_CHANNEL_0
#define BUZZER_LEDC_DUTY_RES LEDC_TIMER_10_BIT
#define BUZZER_LEDC_FREQ_HZ 2000
#define BUZZER_LEDC_DUTY 512

static const char *TAG = "key_led_buzzer";

static void buzzer_set(bool on)
{
    uint32_t duty = on ? BUZZER_LEDC_DUTY : 0;
    ESP_ERROR_CHECK(ledc_set_duty(BUZZER_LEDC_MODE, BUZZER_LEDC_CHANNEL, duty));
    ESP_ERROR_CHECK(ledc_update_duty(BUZZER_LEDC_MODE, BUZZER_LEDC_CHANNEL));
}

static bool boot_is_pressed(void)
{
    return gpio_get_level(BOOT_GPIO) == 0;
}

static void play_three_pulses(void)
{
    ESP_LOGI(TAG, "BOOT pressed: play three LED/buzzer pulses");
    for (int i = 0; i < 3; ++i) {
        ESP_LOGI(TAG, "pulse %d on", i + 1);
        gpio_set_level(LED_GPIO, LED_ON_LEVEL);
        buzzer_set(true);
        vTaskDelay(pdMS_TO_TICKS(300));

        ESP_LOGI(TAG, "pulse %d off", i + 1);
        buzzer_set(false);
        gpio_set_level(LED_GPIO, LED_OFF_LEVEL);
        vTaskDelay(pdMS_TO_TICKS(300));
    }
    ESP_LOGI(TAG, "sequence done; waiting for release");
}

static void configure_gpio(void)
{
    gpio_config_t key_config = {
        .pin_bit_mask = 1ULL << BOOT_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&key_config));

    gpio_config_t led_config = {
        .pin_bit_mask = 1ULL << LED_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&led_config));
    gpio_set_level(LED_GPIO, LED_OFF_LEVEL);
}

static void configure_buzzer_pwm(void)
{
    ledc_timer_config_t timer_config = {
        .speed_mode = BUZZER_LEDC_MODE,
        .duty_resolution = BUZZER_LEDC_DUTY_RES,
        .timer_num = BUZZER_LEDC_TIMER,
        .freq_hz = BUZZER_LEDC_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer_config));

    ledc_channel_config_t channel_config = {
        .gpio_num = BUZZER_GPIO,
        .speed_mode = BUZZER_LEDC_MODE,
        .channel = BUZZER_LEDC_CHANNEL,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = BUZZER_LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&channel_config));
    buzzer_set(false);
}

void app_main(void)
{
    configure_gpio();
    configure_buzzer_pwm();

    ESP_LOGI(TAG, "ready: BOOT=GPIO0 active-low, LED=GPIO32 active-low, buzzer=GPIO25 PWM");

    while (true) {
        if (!boot_is_pressed()) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        vTaskDelay(pdMS_TO_TICKS(40));
        if (!boot_is_pressed()) {
            continue;
        }

        play_three_pulses();

        while (boot_is_pressed()) {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        vTaskDelay(pdMS_TO_TICKS(80));
        ESP_LOGI(TAG, "BOOT released; ready");
    }
}
