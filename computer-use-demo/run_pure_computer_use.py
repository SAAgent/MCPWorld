#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
无头 (Headless) 运行 Computer Use Demo Agent 的脚本 (纯净版，无评估器)。
支持多轮对话交互。
"""

import os
import sys
import argparse
import asyncio
import platform
from typing import List, Dict, Any, Optional, cast

# --- 导入必要的模块 ---
from anthropic import Anthropic # 只导入 Anthropic
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

# --- 简单的控制台回调函数 ---

def headless_output_callback(block: BetaContentBlockParam) -> None:
    """处理并打印来自 Agent 的输出块 (文本, 工具使用, 思考)"""
    if block['type'] == 'text':
        print(f"\nAssistant: {block['text']}")
    elif block['type'] == 'tool_use':
        print(f"\nAssistant wants to use Tool: {block['name']}")
        print(f"Input: {block['input']}")
    elif block['type'] == 'thinking':
            thinking_content = getattr(block, 'thinking', '...') # 处理可能的属性缺失
            print(f"\nAssistant [Thinking]:\n{thinking_content}\n")
    else:
        print(f"\n[未知输出类型]: {block}")

def headless_tool_output_callback(result: ToolResult, tool_id: str) -> None:
    """处理并打印工具执行的结果"""
    print(f"\n[Tool Result for ID: {tool_id}]")
    if result.output:
        # 对于 CLIResult 可能需要特殊格式化
        if result.__class__.__name__ == "CLIResult":
                print(f"Output:\n```bash\n{result.output}\n```")
        else:
            print(f"Output: {result.output}")
    if result.error:
        print(f"Error: {result.error}")
    if result.base64_image:
        # 在纯文本终端无法显示图片，只做提示
        print("[Screenshot captured (omitted in headless mode)]")
        # 可以考虑保存到文件
        # try:
        #     import base64
        #     img_data = base64.b64decode(result.base64_image)
        #     filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}_{tool_id[:8]}.png"
        #     with open(filename, "wb") as f:
        #         f.write(img_data)
        #     print(f"[Screenshot saved to: {filename}]")
        # except Exception as e:
        #     print(f"[Error saving screenshot: {e}]")

def headless_api_response_callback(request, response, error) -> None:
    """简单的 API 响应日志 (可选)"""
    if error:
        print(f"\n[API Error]: {error}")
    # 可以根据需要添加对 request 和 response 的日志记录
    # print(f"[API Request]: {request.method} {request.url}")
    pass

# --- 主执行函数 ---
async def run_agent_loop(args):
    """运行 Agent 的主异步循环"""

    # 1. 初始化 Anthropic 客户端
    api_key = args.api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("错误: 未提供 Anthropic API 密钥。请使用 --api_key 或设置 ANTHROPIC_API_KEY 环境变量。")
        return

    # 注意: sampling_loop 内部会根据 provider 创建客户端，我们只需传递参数
    # client = Anthropic(api_key=api_key) # 不在这里创建

    # 2. 初始化工具集
    tool_version = cast(ToolVersion, args.tool_version)
    if tool_version not in TOOL_GROUPS_BY_VERSION:
            print(f"错误: 无效的工具版本 '{tool_version}'。可用版本: {list(TOOL_GROUPS_BY_VERSION.keys())}")
            return
    tool_group = TOOL_GROUPS_BY_VERSION[tool_version]
    tool_collection = ToolCollection(*(ToolCls() for ToolCls in tool_group.tools))
    print(f"使用的工具版本: {tool_version}")

    # 3. 构建系统提示
    system_prompt_text = SYSTEM_PROMPT
    if args.system_prompt_suffix:
        system_prompt_text += " " + args.system_prompt_suffix
    system_prompt_block = BetaTextBlockParam(type="text", text=system_prompt_text)
    # 注意：cache_control 等特性在这里不手动添加，让 sampling_loop 处理

    # 4. 初始化消息历史
    messages: List[BetaMessageParam] = []

    # 5. 开始多轮对话循环
    turn_count = 0
    while args.max_turns is None or turn_count < args.max_turns:
        print("-" * 30)
        # 获取用户输入
        try:
            user_input = input("You: ")
            if user_input.lower() in ["quit", "exit"]:
                print("Exiting.")
                break
        except EOFError: # 处理 Ctrl+D
            print("\nExiting.")
            break

        # 将用户输入添加到消息历史
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": user_input}]
        })

        # 调用核心 sampling_loop
        print("Assistant thinking...")
        try:
            messages = await sampling_loop(
                model=args.model,
                provider=APIProvider.ANTHROPIC, # 固定为 Anthropic
                messages=messages,
                output_callback=headless_output_callback,
                tool_output_callback=headless_tool_output_callback,
                api_response_callback=headless_api_response_callback,
                api_key=api_key, # 传递 api_key
                tool_version=tool_version,
                max_tokens=args.max_tokens,
                system_prompt_suffix=args.system_prompt_suffix, # sampling_loop 会处理 system prompt
                # sampling_loop 可能需要的其他参数 (参考其定义)
                only_n_most_recent_images=None, # 在无头模式下，可能不需要这个限制，或者设为 None
                thinking_budget=None, # 默认不开启 thinking
                token_efficient_tools_beta=False # 默认不开启
            )
        except Exception as e:
            print(f"\n[Error during agent loop]: {e}")
            # 可以选择是退出还是允许用户继续输入
            # break # 发生严重错误时退出
            # 或者仅打印错误，让用户决定下一步
            print("An error occurred. You can try again or type 'quit' to exit.")

        turn_count += 1

# --- 命令行参数解析 ---
if __name__ == "__main__":
    # 获取可用工具版本
    available_tool_versions = ["computer_use_20250124", "computer_use_20241022"]

    parser = argparse.ArgumentParser(description="Run Computer Use Demo Agent Headlessly (Pure Agent)")
    parser.add_argument("--api_key", type=str, default=None, help="Anthropic API Key (or use ANTHROPIC_API_KEY env var)")
    parser.add_argument("--model", type=str, default="claude-3-7-sonnet-20250219", help="Anthropic model name")
    parser.add_argument("--tool_version", type=str, default=available_tool_versions[0], choices=available_tool_versions, help="Version of tools to use")
    parser.add_argument("--max_tokens", type=int, default=4096, help="Max tokens for model response")
    parser.add_argument("--system_prompt_suffix", type=str, default="", help="Additional text to append to the system prompt")
    parser.add_argument("--max_turns", type=int, default=None, help="Maximum number of conversation turns (user + assistant)")

    args = parser.parse_args()

    try:
        asyncio.run(run_agent_loop(args))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
