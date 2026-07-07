LOW_RISK_TOOLS = {
    "esp_port_list",
    "esp_port_status",
    "esp_serial_capture",
    "esp_logs_latest",
    "esp_logs_get",
    "esp_logs_query",
    "esp_error_parse_log",
    "esp_error_parse_text",
    "hardwork_list",
    "hardwork_get",
    "hardwork_search",
    "memory_read",
    "memory_search",
}

MEDIUM_RISK_TOOLS = {
    "esp_port_select",
    "esp_project_build",
    "esp_file_upload",
    "esp_file_download",
    "esp_file_read",
    "esp_reset",
    "esp_exec_code",
    "esp_run_file",
    "hardwork_set",
    "memory_write",
    "memory_update",
}

HIGH_RISK_TOOLS = {
    "esp_file_delete",
    "esp_project_clean",
    "esp_flash_firmware",
    "esp_erase_flash",
    "memory_delete",
}


def risk_level(tool_name: str) -> str:
    if tool_name in HIGH_RISK_TOOLS:
        return "high"
    if tool_name in MEDIUM_RISK_TOOLS:
        return "medium"
    return "low"


def requires_confirmation(tool_name: str) -> bool:
    return risk_level(tool_name) == "high"

