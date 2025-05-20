#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
无头 (Headless) 运行 Computer Use Demo Agent 的脚本。
(已集成 PC-Canary Evaluator - 最小侵入修改版)
支持多轮对话交互。
"""

import os
import sys
import argparse
import asyncio
import platform
import time
import json
import signal
from typing import List, Dict, Any, Optional, cast

# --- 添加 PC-Canary 路径 (根据你的实际路径修改) ---
PC_CANARY_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'PC-Canary'))
if PC_CANARY_PATH not in sys.path:
    print(f"Adding PC-Canary path: {PC_CANARY_PATH}")
    sys.path.append(PC_CANARY_PATH)

# --- 导入必要的模块 ---
from anthropic import Anthropic
from anthropic.types.beta import (
    BetaMessageParam,
    BetaContentBlockParam,
    BetaTextBlockParam,
    BetaToolResultBlockParam,
    BetaToolUseBlockParam,
)

# 从 computer_use_demo 导入核心组件
try:
    from computer_use_demo.loop import sampling_loop, SYSTEM_PROMPT, APIProvider
    from computer_use_demo.tools import (
        TOOL_GROUPS_BY_VERSION,
        ToolCollection,
        ToolResult,
        ToolVersion,
    )
except ImportError as e:
    print(f"错误: 无法导入 computer_use_demo 组件。请确保脚本在正确的环境中运行，或者已将项目添加到 PYTHONPATH。")
    print(f"原始错误: {e}")
    sys.exit(1)

# --- 导入 Evaluator 相关组件 ---
try:
    from evaluator.core.base_evaluator import BaseEvaluator, CallbackEventData
    from evaluator.core.events import AgentEvent
except ImportError as e:
    print(f"错误: 无法导入 PC-Canary Evaluator 组件。请确保 PC-Canary 路径正确并已添加到 PYTHONPATH。")
    print(f"原始错误: {e}")
    sys.exit(1)

# --- 全局标志 (用于回调终止循环) ---
evaluation_finished = False
evaluator_instance_for_signal: Optional[BaseEvaluator] = None # 用于信号处理

# --- 简单的控制台回调函数 ---

def headless_output_callback(block: BetaContentBlockParam) -> None:
    # (保持不变)
    if block['type'] == 'text':
        print(f"\nAssistant: {block['text']}")
    elif block['type'] == 'tool_use':
        print(f"\nAssistant wants to use Tool: {block['name']}")
        print(f"Input: {block['input']}")
    elif block['type'] == 'thinking':
            thinking_content = getattr(block, 'thinking', '...')
            print(f"\nAssistant [Thinking]:\n{thinking_content}\n")
    else:
        print(f"\n[未知输出类型]: {block}")

def headless_tool_output_callback(result: ToolResult, tool_id: str) -> None:
    # (保持不变，但注意：TOOL_CALL 事件现在由 loop.py 内部记录)
    print(f"\n[Tool Result for ID: {tool_id}]")
    if result.output:
        if result.__class__.__name__ == "CLIResult":
                print(f"Output:\n```bash\n{result.output}\n```")
        else:
            print(f"Output: {result.output}")
    if result.error:
        print(f"Error: {result.error}")
    if result.base64_image:
        print("[Screenshot captured (omitted in headless mode)]")

def headless_api_response_callback(request, response, error) -> None:
    # (保持不变)
    if error:
        print(f"\n[API Error]: {error}")
    pass

# --- Evaluator 回调函数 ---
def handle_evaluator_event(event_data: CallbackEventData, evaluator: BaseEvaluator):
    """处理评估器事件的回调函数"""
    print(f"\n[Evaluator Event]: {event_data.event_type} - {event_data.message}")
    global evaluation_finished
    if event_data.event_type in ["task_completed", "task_error"]:
        print(f"Evaluator reported final status: {event_data.event_type}")
        evaluation_finished = True

# --- 信号处理函数 ---
def signal_handler(sig, frame):
    """处理 CTRL+C 信号"""
    print("\n\n用户中断执行...")
    global evaluator_instance_for_signal
    if evaluator_instance_for_signal and evaluator_instance_for_signal.is_running:
        print("正在停止评估器...")
        evaluator_instance_for_signal.stop() # stop() 会处理保存和 TASK_END(stopped)
        # stop_app() 可能也需要调用，取决于任务
        if hasattr(evaluator_instance_for_signal, 'stop_app'):
             evaluator_instance_for_signal.stop_app()
    sys.exit(0)

# --- 主执行函数 ---
async def run_agent_loop(args, evaluator: BaseEvaluator): # <-- 接收 evaluator 实例
    """运行 Agent 的主异步循环"""
    global evaluation_finished # 引用全局标志

    # 1. 初始化客户端和工具集 (已移到 main 函数)
    api_key = args.api_key or os.getenv("ANTHROPIC_API_KEY") # api_key 在 main 中检查

    tool_version = cast(ToolVersion, args.tool_version)
    tool_group = TOOL_GROUPS_BY_VERSION[tool_version]
    tool_collection = ToolCollection(*(ToolCls() for ToolCls in tool_group.tools))
    print(f"使用的工具版本: {tool_version}")

    # 2. 构建系统提示 (保持不变)
    system_prompt_text = SYSTEM_PROMPT
    if args.system_prompt_suffix:
        system_prompt_text += " " + args.system_prompt_suffix
    # 注意：sampling_loop 会处理 system prompt 块

    # 3. 初始化消息历史
    messages: List[BetaMessageParam] = []

    # 4. 开始多轮对话循环 (添加 evaluation_finished 条件)
    turn_count = 0
    start_time = time.time() # 记录循环开始时间以备超时检查
    is_timeout = lambda : args.timeout > 0 and time.time() - start_time > args.timeout
    while (args.max_turns is None or turn_count < args.max_turns) and not evaluation_finished:
        # 检查超时 (相对于循环开始)
        if is_timeout():
            print(f"\n执行超时 ({args.timeout}秒)")
            break # 让 finally 处理停止

        print("-" * 30)
        # 获取用户输入
        try:
            user_input = ""
            if turn_count == 0:
                default_instr = evaluator.default_instruction
                if default_instr:
                    prompt = f'You (Press Enter for default: "{default_instr}"): '
                    user_input = input(prompt)
                    if not user_input.strip(): # 如果用户只按了回车或输入空白
                        print(f"Using default instruction: {default_instr}")
                        user_input = default_instr
                else:
                    # 如果没有默认指令，则正常提示
                    user_input = input("You: ")
            else:
                # 非第一轮，正常提示
                user_input = input("You: ")

            if user_input.lower() in ["quit", "exit"]:
                print("用户请求退出。")
                break # 正常退出循环
        except EOFError:
            print("\n检测到 EOF，退出。")
            break

        # 将用户输入添加到消息历史
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": user_input}]
        })

        # --- 事件记录：LLM 调用开始 ---
        llm_start_time = time.time()
        model_name_to_record = args.model # 或者尝试从 client 获取
        evaluator.record_event(AgentEvent.LLM_QUERY_START, {
            'timestamp': llm_start_time,
            'model_name': model_name_to_record
        })

        print("Assistant thinking...")
        llm_success = False
        llm_error = None
        usage_info = None # 初始化 usage_info
        try:
            # --- 调用核心 sampling_loop ---
            # 需要传递 evaluator 和 task_id 给内部记录 TOOL 事件
            messages = await sampling_loop(
                model=args.model,
                provider=APIProvider.ANTHROPIC,
                messages=messages,
                output_callback=headless_output_callback,
                tool_output_callback=headless_tool_output_callback, # 工具结果打印
                api_response_callback=headless_api_response_callback,
                api_key=api_key,
                tool_version=tool_version,
                max_tokens=args.max_tokens,
                system_prompt_suffix=args.system_prompt_suffix,
                evaluator=evaluator,                 # <--- 传递评估器
                evaluator_task_id=evaluator.task_id, # <--- 传递任务 ID
                is_timeout=is_timeout,
                only_n_most_recent_images=None,
                thinking_budget=None,
                token_efficient_tools_beta=False
                # TODO: 尝试让 sampling_loop 返回 usage_info
            )
            # 假设如果 sampling_loop 没抛异常，LLM 调用过程是成功的
            # 但我们没有直接拿到 usage_info
            llm_success = True
            # print(f"Debug: messages after loop: {messages}") # 调试用
        except Exception as e:
            print(f"\n[Error during agent loop]: {e}")
            llm_error = str(e)
            # break # 发生错误时退出循环

        # --- 事件记录：LLM 调用结束 ---
        # 暂时无法获取精确 token，记录 None
        evaluator.record_event(AgentEvent.LLM_QUERY_END, {
            'timestamp': time.time(),
            'status': 'success' if llm_success else 'error',
            'error': llm_error,
            'prompt_tokens': None, # <-- 缺失
            'completion_tokens': None, # <-- 缺失
            'cost': None
        })

        # --- 检查 Agent 是否报告完成 (简单示例，需要根据实际输出调整) ---
        # if messages:
        #     last_assistant_message = messages[-1]
        #     if last_assistant_message['role'] == 'assistant':
        #        # ... 解析 last_assistant_message['content'] ...
        #        # if "任务完成" in text_content:
        #        #     evaluator.record_event(AgentEvent.AGENT_REPORTED_COMPLETION, ...)
        #        pass
        if evaluator.hook_manager.evaluate_on_completion:
            evaluator.hook_manager.trigger_evaluate_on_completion()

        turn_count += 1
        time.sleep(1) # 短暂 sleep，避免 CPU 占用过高，并给回调一点时间

# --- 命令行参数解析与主函数 ---
if __name__ == "__main__":
    available_tool_versions = ["computer_use_20250124", "computer_only", "computer_use_20241022"]

    parser = argparse.ArgumentParser(description="Run Computer Use Demo Agent Headlessly with Evaluator")
    # Agent 参数
    parser.add_argument("--api_key", type=str, default=None, help="Anthropic API Key (or use ANTHROPIC_API_KEY env var)")
    parser.add_argument("--model", type=str, default="claude-3-7-sonnet-20250219", help="Anthropic model name")
    parser.add_argument("--tool_version", type=str, default=available_tool_versions[0], choices=available_tool_versions, help="Version of tools to use")
    parser.add_argument("--max_tokens", type=int, default=4096, help="Max tokens for model response")
    parser.add_argument("--system_prompt_suffix", type=str, default="", help="Additional text to append to the system prompt")
    parser.add_argument("--max_turns", type=int, default=10, help="Maximum number of conversation turns (user + assistant, default: 10)")
    # Evaluator 参数
    parser.add_argument("--task_id", type=str, required=True, help="PC-Canary Task ID (format: category/id, e.g., computeruse/task01_example)")
    parser.add_argument("--log_dir", type=str, default="logs_computer_use_eval", help="Directory for evaluator logs and results")
    parser.add_argument("--app_path", type=str, default=None, help="Path to specific application if required by the task")
    parser.add_argument("--timeout", type=int, default=300, help="Overall execution timeout in seconds (default: 300)")
    parser.add_argument("--exec_mode", type=str, choices=["mixed", "gui", "api"], default="mixed", 
                        help="Agent mode for tool use evaluation (default: mixed)")

    args = parser.parse_args()

    # 检查 API Key
    if not (args.api_key or os.getenv("ANTHROPIC_API_KEY")):
        print("错误: 必须提供 Anthropic API 密钥 (--api_key 或 ANTHROPIC_API_KEY 环境变量)")
        sys.exit(1)
    
    if not os.getenv("DISPLAY"):
        print("错误: 必须提供DISPLAY环境变量")
        sys.exit(1)

    # 解析 task_id
    try:
        category, task_id_part = args.task_id.split('/', 1)
        task_config = {"category": category, "id": task_id_part}
    except ValueError:
        print("错误: task_id 格式必须是 'category/id'")
        sys.exit(1)

    # 创建日志目录
    os.makedirs(args.log_dir, exist_ok=True)

    # 初始化 Evaluator
    evaluator: Optional[BaseEvaluator] = None # 明确类型
    try:
        print(f"[*] 初始化评估器 (Task: {args.task_id})...")
        evaluator = BaseEvaluator(
            task=task_config,
            log_dir=args.log_dir,
            app_path=args.app_path,
            custom_params={"exec_mode": args.exec_mode},
        )
        evaluator.timeout = args.timeout
        evaluator_instance_for_signal = evaluator # 赋值给全局变量供信号处理
        evaluator.register_completion_callback(handle_evaluator_event)
    except Exception as e:
        print(f"初始化评估器失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # 启动评估器
        print("[*] 启动评估器...")
        if not evaluator.start():
            print("评估器启动失败！")
            sys.exit(1)

        # 等待评估器内部初始化 (例如启动应用)
        wait_time = 2 # 秒
        print(f"[*] 等待 {wait_time} 秒以确保评估器就绪...")
        time.sleep(wait_time)

        print("[*] 启动 Agent 交互循环...")
        # 运行主循环
        asyncio.run(run_agent_loop(args, evaluator)) # 将 evaluator 传入

    except KeyboardInterrupt:
        print("\n主程序被中断。") # 信号处理器会处理停止
    except Exception as e:
        print(f"\n主程序发生未处理错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 最终停止评估器（如果仍在运行）
        if evaluator and evaluator.is_running:
            print("[*] (Finally) 停止评估器...")
            evaluator.stop()
        if evaluator and hasattr(evaluator, 'stop_app'):
            print("[*] (Finally) 停止关联应用...")
            evaluator.stop_app()

        # 报告最终结果
        if evaluator:
             print("\n" + "="*30 + " 评估结果 " + "="*30)
             final_results = evaluator.result_collector.get_results(evaluator.task_id)
             computed_metrics = final_results.get('computed_metrics', {})
             final_status = computed_metrics.get('task_completion_status', {})

             print("最终计算指标:")
             if computed_metrics:
                 for key, value in computed_metrics.items():
                     try:
                         value_str = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)
                     except TypeError:
                         value_str = str(value) # Fallback for non-serializable types
                     print(f"  {key}: {value_str}")
             else:
                 print("  未能计算任何指标。")

             print(f"\n最终任务状态: {final_status.get('status', '未知')}")
             if final_status.get('reason'):
                 print(f"原因: {final_status.get('reason')}")

             # 结果文件路径通常在 evaluator.stop() -> save_results() 中打印
             # result_file = evaluator.save_results() # 不需要重复保存

        print("="*72)
        print("脚本执行完毕。")
