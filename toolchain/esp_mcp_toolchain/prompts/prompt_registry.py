from __future__ import annotations


def _workflow(
    *,
    goal: str,
    prechecks: str,
    tool_order: str,
    success_evidence: str,
    safety_boundary: str,
    failure_handling: str,
    final_report: str,
) -> str:
    return (
        f"目标\n{goal}\n\n"
        f"前置检查\n{prechecks}\n\n"
        f"工具顺序\n{tool_order}\n\n"
        f"成功证据\n{success_evidence}\n\n"
        f"安全边界\n{safety_boundary}\n\n"
        f"失败处理\n{failure_handling}\n\n"
        f"最终报告\n{final_report}"
    )


PROMPT_DEFINITIONS = {
    "file_transfer": {
        "description": "把 MicroPython 源文件安全推送到 ESP，并读回核验。",
        "text": _workflow(
            goal="将指定本地 MicroPython 文件传到板上目标路径，并证明内容可读。",
            prechecks="先调用 project_context_select/project_context_status；读取 hardwork 映射，使用 esp_port_list 与 esp_port_status 核对板卡和端口；确认板上运行 MicroPython，且 Conda 环境可用 mpremote。",
            tool_order="依次使用 esp_file_upload，再用 esp_file_read 或 esp_file_list 核验；最后用 esp_logs_latest/esp_logs_get 保存审计证据。",
            success_evidence="上传工具成功、字节数合理、读回路径与内容或校验值匹配；不能仅以工具被调用作为成功。",
            safety_boundary="上传会修改板上文件。只写用户指定路径，不覆盖未知文件；不得顺带删除、烧录、擦除或把 confirm=False 改成 True。",
            failure_handling="端口忙或消失时停止并重新枚举；mpremote/raw REPL 不可用时如实报告固件或依赖不匹配，不自动烧录 MicroPython。",
            final_report="报告源路径、目标路径、后端、字节数、读回证据、run_id、未验证事项和风险。",
        ),
    },
    "program_execution_control": {
        "description": "远程运行 MicroPython 程序，并用 Ctrl-C 请求停止。",
        "text": _workflow(
            goal="运行指定短代码或板上文件，并在用户要求时用 Ctrl-C 独立停止程序。",
            prechecks="先调用 project_context_select/project_context_status 选择项目上下文、读取 hardwork、核对端口和 MicroPython raw REPL；明确运行的是内联代码、本地文件还是远程文件。",
            tool_order="按目标调用 esp_exec_code 或 esp_run_file；需要停止时调用 esp_program_stop；随后用 esp_serial_capture 或日志工具核验结果。",
            success_evidence="运行需有 stdout/stderr 或业务输出；停止只有观察到 >>> 提示符才算 stop_confirmed=True；KeyboardInterrupt 单独出现不够。reset_command_sent=False 仅证明工具未发送复位命令。",
            safety_boundary="esp_program_stop 只能发送 Ctrl-C，不发送 Ctrl-D 或复位命令，也不删除文件；打开串口仍可能产生驱动或控制线效应，因此 physical_reset_excluded=False。代码执行不得扩展到未授权的持久化写入。",
            failure_handling="停止未确认时报告 program_stop_unconfirmed，不声称已停；raw REPL 不可达时检查端口/固件，不自动复位或烧录。",
            final_report="报告运行目标、输出、异常、停止是否确认、是否发送复位命令、物理复位是否可排除、run_id 和下一步。",
        ),
    },
    "microcontroller_reset": {
        "description": "按明确模式远程复位 ESP，并核验重新启动。",
        "text": _workflow(
            goal="根据用户明确选择执行 soft 或 hard reset，并收集复位后的串口启动证据。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文、读取 hardwork 串口映射、核对当前端口；在执行前说明 soft/hard 对运行状态的影响。",
            tool_order="调用 esp_reset(mode=soft|hard)，先检查 pre_action_output_observed/pre_action_text，再检查该调用在同一串口句柄内返回的 text 和动作状态；如需观察后续输出，可另开 esp_serial_capture 或 esp_serial_monitor_start/read/stop，但必须标注为独立串口会话。",
            success_evidence="ok=True 只证明主机侧软复位命令或硬复位脉冲流程完成；pre_action_text 记录动作前 250 ms 的有界输出，用于发现打开串口时的可能副作用。复位后的有界原始字节以 reset_output_raw_base64、reset_output_sha256、reset_output_bytes、reset_output_text、reset_output_capture_completed 和解码/捕获上限状态写入项目 SQLite completion 事件，供后续按 run_id 复核；capture_completed=False 时不能把默认的 0 字节解释为一次成功的空捕获。持久化不消除因果歧义，因此 output_causality_confirmed=False、reset_confirmed=False；独立重新打开串口得到的日志不能反向证明原复位，没有日志时不得虚构启动成功。",
            safety_boundary="只执行用户指定模式；打开串口仍可能产生驱动控制线效应，因此 physical_reset_excluded=False。不得借复位进入 bootloader、烧录、擦除或 full clean；端口忙时不强制抢占。",
            failure_handling="端口消失时重新 esp_port_list；若疑似供电瞬断，只报告 USB/串口证据，不能据此证明 LED、蜂鸣器等物理状态。",
            final_report="报告复位模式、端口、pre_action_text/字节数、reset_command_sent 或硬复位脉冲状态、reset_output_bytes/reset_output_sha256/reset_output_raw_base64 的持久化状态、reset_output_capture_completed、解码/捕获上限、reset_confirmed、output_causality_confirmed、physical_reset_excluded、动作后 text 摘要、证据边界和 run_id。",
        ),
    },
    "serial_monitor": {
        "description": "启动、读取并停止可审计的后台串口监控。",
        "text": _workflow(
            goal="持续采集 ESP 串口输出，通过游标增量读取，并在结束时可靠停止。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文、读取 hardwork 串口参数、核对端口未被其他进程占用；给 session_name 和波特率。",
            tool_order="esp_serial_monitor_start → esp_serial_monitor_status → 按 after_seq 调用 esp_serial_monitor_read → esp_serial_monitor_stop；短任务可用 esp_serial_capture。",
            success_evidence="状态为 RUNNING、记录非空且序号有序、无无法解释的 dropped/unpersisted/decode_error；结束后 worker_alive=False。",
            safety_boundary="监控是只读串口采集；不要重复启动同一端口，不把 UART 文本等同于肉眼观察的 LED/蜂鸣器现象。",
            failure_handling="DISCONNECTED/FAILED 时保留 raw 日志并停止推断；遇到异常数据量、解码错误或端口消失时按 USB/供电故障处理并报告。",
            final_report="报告 run_id、时间范围、字节数、记录序号、丢弃/解码/断连状态、日志路径和可信结论。",
        ),
    },
    "runtime_log_search": {
        "description": "按 run、工具、级别和时间检索 SQLite 运行日志。",
        "text": _workflow(
            goal="从项目级 SQLite runs/events 中定位目标执行记录并给出可复核证据。",
            prechecks="先调用 project_context_select/project_context_status 选择项目上下文；明确查询词、run_id、工具、级别、阶段或时间范围，sequence 范围必须同时给 run_id。",
            tool_order="先 esp_logs_latest 定位最近运行，再用 esp_logs_query 缩小范围，最后 esp_logs_get 读取完整目标 run。",
            success_evidence="结果 project_id 与当前项目一致，事件 sequence 有序，筛选条件与返回字段相符。",
            safety_boundary="SQLite 是正式查询源；JSONL 只是审计镜像。日志检索只读，不触发重跑、复位、烧录或删除。",
            failure_handling="查不到时报告筛选条件和空结果，逐步放宽条件；不得跨项目扫描或静默回退成未经约束的文件搜索。",
            final_report="报告查询条件、命中 run、关键事件、时间、状态、异常和证据来源。",
        ),
    },
    "debug_error": {
        "description": "自动发现并结构化报告 MicroPython 运行时异常。",
        "text": _workflow(
            goal="从实时采集、后台 Monitor 或项目原始日志中识别 MicroPython Traceback，并定位文件、行号和异常类型。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文并确认 run_id/串口来源；需要实时采集时先核对端口与 MicroPython 固件。",
            tool_order="实时用 esp_serial_capture 或 esp_serial_monitor_start/read/stop；随后调用 esp_error_parse_log，纯文本可用 esp_error_parse_text；修复后再运行并复测。",
            success_evidence="has_error、error_kind、file、line、exception_type 与原始 Traceback 一致；扫描来源和 scanned_bytes 明确。",
            safety_boundary="原始日志扫描必须限制在当前项目 logs 根目录和 max_bytes；修复不自动触发烧录、删除或覆盖板上文件。",
            failure_handling="跨 chunk Traceback 应继续读取到异常末行；无完整异常时报告字段缺失，不猜测。路径越界或日志截断必须显式报告。",
            final_report="报告 run_id、异常结构、原始证据位置、扫描上限/截断、修复建议和复测结果。",
        ),
    },
    "build_flash_monitor": {
        "description": "构建固件，经明确确认后烧录并独立监控。",
        "text": _workflow(
            goal="构建 ESP 工程；仅在用户明确批准后烧录，并单独启动串口监控验收。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文、读取 hardwork 和工程路径、核对端口；先完成 esp_project_build，确认产物与返回码。",
            tool_order="esp_project_build → 展示产物/风险并等待明确授权 → esp_flash_firmware(confirm=True) → esp_serial_monitor_start/read/stop 或 esp_serial_capture。",
            success_evidence="构建返回码和产物存在；烧录成功需工具返回；运行成功需独立串口证据。monitor_after_flash 未实现时不得声称自动监控已完成。",
            safety_boundary="烧录是高风险动作。没有本轮明确确认时 confirm 必须保持 False；不得顺带 erase、restore、full clean 或删除文件。",
            failure_handling="端口忙/消失时停止并重新验证；构建失败先修复。不得用清理掩盖路径/环境问题，尤其不删除未知 build 目录。",
            final_report="分别报告构建、授权、烧录、监控四段状态，列出产物、端口、run_id、日志和未完成项。",
        ),
    },
    "remote_file_management": {
        "description": "浏览、读取、上传、下载和受控删除板上 MicroPython 文件。",
        "text": _workflow(
            goal="管理 MicroPython 文件系统，所有路径和修改均可审计。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文、读取 hardwork、确认板上为 MicroPython 并核对端口；先列目录再决定动作。",
            tool_order="esp_file_list/esp_file_read 用于检查；esp_file_download 做备份；esp_file_upload 写入；只有明确批准时调用 esp_file_delete(confirm=True)。",
            success_evidence="列表/读回结果与目标路径一致；上传下载核对字节或内容；删除后再次列目录确认。",
            safety_boundary="删除是高风险动作，必须使用用户明确给出的路径和 confirm=True；不得批量猜路径、递归清空或借机烧录。",
            failure_handling="目标不存在时报告；mpremote/raw REPL 不可用时检查固件与依赖。删除失败不重复尝试未知替代路径。",
            final_report="报告每个路径、动作、后端、字节/读回证据、确认状态、run_id 和遗留文件。",
        ),
    },
    "gpio_status_query": {
        "description": "在线读取明确 GPIO 的逻辑电平且不改变模式。",
        "text": _workflow(
            goal="读取用户指定 GPIO 当前逻辑值，并保持已有输入/输出模式不变。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文并读取 hardwork GPIO 映射；只使用资料确认的 GPIO 列表，核对 MicroPython 和端口，并让用户明确接受当前程序会被 raw REPL 中断。",
            tool_order="用户确认后调用 esp_gpio_status(pins=[...], allow_program_interrupt=True)；需要关联行为时另行串口监控，但不要把串口推断当作物理观察。",
            success_evidence="gpio_read_only=True、mode_changed=False、program_interrupted=True，每个 pin 有结构化 value 或 error，failed_count=0 才算全部成功。",
            safety_boundary="不得自动扫描全部 GPIO，不调用 Pin.IN/Pin.OUT/init 改模式；raw REPL 会中断当前应用，未获确认时 allow_program_interrupt 必须保持 False；不得驱动蜂鸣器、LED 或高功率负载。",
            failure_handling="无效 pin 或部分失败时回到 hardwork 映射核对；MicroPython 不可达时不自动烧录。",
            final_report="报告 pins、逐 pin 值、失败数、证据类型、未确认物理状态和 run_id。",
        ),
    },
    "review_hardware_context": {
        "description": "采集主机、资料和可选 MicroPython 运行时硬件信息并标证据。",
        "text": _workflow(
            goal="自动汇总 USB 串口描述、已审查 hardwork 和可选 MicroPython 运行时信息，严格区分证据来源。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文；先用 passive 模式并要求端口确实已枚举。只有确认板上是 MicroPython、需要运行时字段且用户接受当前程序被中断时，才选择 mode=micropython。",
            tool_order="esp_hardware_info(mode=passive) → 用户明确确认后，必要时 esp_hardware_info(mode=micropython, allow_program_interrupt=True) → 对新增稳定事实再用 hardwork_mapping_patch。",
            success_evidence="每个字段含 value/evidence；被请求端口必须 enumerated=True；运行时模式记录 program_interrupted=True、reset_command_sent=False、physical_reset_excluded=False；probe_errors 单独列出。",
            safety_boundary="本流程不进入 bootloader、不运行 esptool、不发送复位命令、不烧录。运行时 raw REPL 会中断当前应用，且不能排除串口控制线的物理效应；资料、USB 描述和运行时证据不得混写。",
            failure_handling="运行时字段缺失时保留 passive 结果并报告 probe_errors；不能探测的 RAM/Flash/固件字段不得猜测。",
            final_report="按 USB、hardwork、runtime 三类报告字段、证据、错误、端口和未确认事项。",
        ),
    },
    "automated_regression_test": {
        "description": "运行显式的板上 MicroPython 回归文件集合。",
        "text": _workflow(
            goal="在板上依次运行用户指定的远程 MicroPython 测试文件，汇总通过、失败和跳过。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文、核对 MicroPython 与端口；先用 esp_file_list/esp_file_read 确认测试文件存在且内容受信任，并让用户确认将执行的完整路径列表。",
            tool_order="必要时上传测试文件 → 用户明确确认后调用 esp_regression_test(tests=[...], fail_fast=..., confirm_execution=True) → esp_error_parse_log/esp_logs_get 查看失败证据。",
            success_evidence="每个结果包含 path、ok、duration_us、stdout；failed=0 且 skipped=0 才算整组通过。",
            safety_boundary="只运行用户明确确认的显式路径，最多 32 项；脚本可能驱动硬件或写文件。未确认时 confirm_execution 必须保持 False；不得自动发现未知脚本，不烧录、不删除、不复位。",
            failure_handling="失败时保留第一处 error/stdout，修复或重传后重跑；端口断开时停止，不把未运行项算通过。",
            final_report="报告测试列表、passed/failed/skipped、时长、首个失败、run_id 和软件/实板证据边界。",
        ),
    },
    "performance_analysis": {
        "description": "用板上插桩统计 MicroPython 运行时间和堆变化。",
        "text": _workflow(
            goal="对一段内联代码或一个远程文件做有限次运行，统计 ticks_us 时长和 gc.mem_free 堆变化。",
            prechecks="调用 project_context_select/project_context_status 选择项目上下文、核对 MicroPython 与端口；code 和 remote_path 必须二选一，展示完整目标、iterations 和可能被重复的硬件/文件副作用，并取得明确确认。",
            tool_order="用户明确确认后调用 esp_performance_profile(confirm_repeated_execution=True)；如样本失败，再用 esp_error_parse_log 和日志工具定位；再次复测仍需核对目标与迭代。",
            success_evidence="samples 数量等于 iterations 且全部 ok；timing_us 和 memory_delta_bytes 给出 min/median/mean/max。",
            safety_boundary="这是 instrumented_wall_time，不是采样 profiler；最多 50 次。每次都会执行目标并可能重复驱动蜂鸣器、GPIO 或写文件；未确认时 confirm_repeated_execution 必须保持 False，且不用结果推断功耗或电流。",
            failure_handling="样本失败时保留其 error，不从成功子集虚报整体成功；超时时降低迭代或缩小目标，不自动复位/烧录。",
            final_report="报告目标、迭代、样本、时间/内存统计、失败、profile_kind、sampling_profiler=False 和 run_id。",
        ),
    },
}


PROMPTS = {
    name: definition["description"]
    for name, definition in PROMPT_DEFINITIONS.items()
}

def list_prompts() -> list[dict]:
    return [
        {"name": name, "description": definition["description"]}
        for name, definition in PROMPT_DEFINITIONS.items()
    ]


def get_prompt(name: str, arguments: dict | None = None) -> dict:
    definition = PROMPT_DEFINITIONS.get(name)
    if definition is None:
        return {"description": "Unknown prompt", "messages": []}
    return {
        "description": definition["description"],
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": definition["text"],
                },
            }
        ],
    }
